"""
GPT Image generation — the processing stage for projects with processor='gpt'.

════════════════════════════════════════════════════════════════════════════
WHY THIS RUNS ON THE SERVER, NOT THE WINDOWS NODE
════════════════════════════════════════════════════════════════════════════
Photoshop needs Windows because it is a desktop application. This is an HTTPS
POST. Running it on the node would mean the source image travelling
Linux -> Windows -> OpenAI -> Windows -> storage, and the whole celebrity
pipeline stopping dead whenever that box has one of its logon episodes.

Here, the source images are already on local disk, generation parallelises
freely (it is network-bound, not CPU-bound), and a Windows outage costs
uploads but never blocks generation.

════════════════════════════════════════════════════════════════════════════
PERMANENT vs TRANSIENT FAILURE — THE IMPORTANT DISTINCTION
════════════════════════════════════════════════════════════════════════════
    HTTP 429, 500, 502, 503, 504   transient. Retry with backoff.
    HTTP 400 safety rejection      PERMANENT. Never retry.
    HTTP 401/403                   PERMANENT. The key is wrong; stop everything.
    insufficient_quota             PERMANENT. Billing. Stop everything.

The safety case is the one that matters. A rejection like

    "Your request was rejected by the safety system. safety_violations=[sexual]"

is deterministic — the same image and prompt will be rejected identically
every time. The reference script retried it five times with exponential
backoff, which burns money and time to fail in exactly the same way.

Permanently-failed images are parked with GPT's own words attached and are
retried ONLY when the admin presses the button. If the policy loosens in a
year, that button is how the image comes back.

════════════════════════════════════════════════════════════════════════════
COST
════════════════════════════════════════════════════════════════════════════
The response carries real token counts, so every call records what it
actually cost rather than an estimate. That is what the spend dashboard shows
and what the monthly cap counts.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

API_URL = "https://api.openai.com/v1/images/edits"
TIMEOUT_S = 600

MAX_RETRIES = 4
BASE_BACKOFF = 4.0


class PermanentFailure(Exception):
    """Retrying will fail identically. Park it for the admin."""

    def __init__(self, message: str, *, kind: str = "rejected",
                 categories: Optional[list[str]] = None):
        super().__init__(message)
        self.kind = kind      # 'rejected' | 'auth' | 'billing' | 'bad_request'
        # The specific policy categories OpenAI named, e.g. ['sexual'] or
        # ['gore']. Surfaced separately from the raw message so the failures
        # list can be grouped and filtered — "show me everything rejected for
        # gore" is a different question from "show me everything rejected",
        # and the answer decides whether the fix is a prompt change or a
        # different source image.
        self.categories = categories or []


class TransientFailure(Exception):
    """Worth another attempt later."""


@dataclass
class Generation:
    image_bytes: bytes
    input_tokens: int = 0
    output_tokens: int = 0
    text_tokens: int = 0
    duration_ms: int = 0


# ── Failure classification ──────────────────────────────────────────────────

# Matched on the WORDING, not on any one category. OpenAI names whichever
# policy was tripped — sexual, gore, violence, minors, and whatever they add
# next — and every one of them is equally permanent for the same image and
# prompt. Keying on a specific category would have meant a new category
# silently becoming retryable.
_PERMANENT_MARKERS = (
    "safety system",
    "safety_violations",
    "content_policy",
    "invalid_request_error",
    "image_parse_error",
)

_CATEGORY_RE = re.compile(r"safety_violations\s*=\s*\[([^\]]*)\]", re.I)
_STAGE_RE = re.compile(r'"moderation_stage"\s*:\s*"(input|output)"', re.I)


def extract_moderation_stage(body: str) -> str:
    """
    WHERE the safety filter refused, read out of the stored error.

    'input'  — the worker's PHOTO tripped the filter. The same photo trips
               it every time, so repainting repeats the refusal; the fix
               is a different photo.
    'output' — the model's OWN painting tripped it. A repaint is a fresh
               roll of the dice and can pass — observed 2026-09-14: the
               same source painted cleanly once, and its RERUN was refused
               at output.
    ''       — the message named no stage (older refusals, other shapes).
    """
    m = _STAGE_RE.search(body or "")
    return m.group(1).lower() if m else ""


def extract_categories(body: str) -> list[str]:
    """
    Pull the named policy categories out of a rejection message.

        "... safety_violations=[sexual]."          -> ['sexual']
        "... safety_violations=[violence, minors]" -> ['violence', 'minors']

    Returns [] when the message names none, which is normal for the more
    generic content_policy refusals.
    """
    m = _CATEGORY_RE.search(body or "")
    if not m:
        return []
    return [part.strip().strip("'\"") for part in m.group(1).split(",") if part.strip()]


def _classify(status: int, body: str) -> Exception:
    """
    Decide whether a failure is worth retrying.

    Reads the message rather than only the status code, because OpenAI
    returns 400 for both "this image is unacceptable" (permanent) and
    "your JSON was malformed" (also permanent, but a different fix) — and
    the operator needs to be told which.
    """
    lowered = (body or "").lower()

    if status in (401, 403):
        return PermanentFailure(
            f"OpenAI rejected the API key (HTTP {status}). Check the key on the "
            f"Pipeline page.", kind="auth")

    if "insufficient_quota" in lowered or "billing" in lowered:
        return PermanentFailure(
            "OpenAI reports the account is out of credit. Top up, then retry "
            "the parked images.", kind="billing")

    if status == 400 and any(m in lowered for m in _PERMANENT_MARKERS):
        return PermanentFailure(body[:600], kind="rejected",
                                categories=extract_categories(body))

    if status in (429, 500, 502, 503, 504):
        return TransientFailure(f"HTTP {status}: {body[:200]}")

    if status == 400:
        return PermanentFailure(body[:600], kind="bad_request")

    return TransientFailure(f"HTTP {status}: {body[:200]}")


# ── The call ────────────────────────────────────────────────────────────────

def _part(path: Path) -> tuple[str, bytes, str]:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return (path.name, path.read_bytes(), mime)


def generate(db: Session, *, source: Path, style: Path, project=None,
             location: str = "", log_fn=None) -> Generation:
    """
    Send one image through the style transfer and return the result.

    Order matters: the style reference is the FIRST image and the source
    photo is the SECOND, because the prompt refers to them that way.

    ════════════════════════════════════════════════════════════════════════
    THE STYLE REFERENCE IS OPTIONAL, AND THE PROMPT DECIDES
    ════════════════════════════════════════════════════════════════════════
    `openai_use_style_image` switches it off, in which case only the worker's
    photo is sent. That is not a preference about quality — it is about which
    prompt is written. "Transform the style of the second image to the style
    of the first" needs two pictures. A prompt that describes the look in
    words needs one, and sending a reference alongside it confuses the model
    into blending two instructions.

    The decision is read HERE rather than by the caller, so there is one
    place that knows how many images go in the request. A caller deciding it
    and a builder assuming it is the shape that produced the Chrome-profile
    bug: two copies of one rule, drifting apart in silence.

    Raises PermanentFailure or TransientFailure. Never returns partial work.
    """
    def emit(msg, level="info"):
        if log_fn:
            log_fn(msg, level=level)

    api_key = api_key_for(db, project)
    data = request_fields(db, project, stage=1, location=location)
    parts = step1_images(db, project, source=source, style=style)
    emit("sending the style reference and the photo" if len(parts) == 2
         else "sending the photo only — the style reference is switched off")
    files = [("image[]", p) for p in parts]
    headers = {"Authorization": f"Bearer {api_key}"}
    return _send_and_read(headers, data, files, emit)


def api_key_for(db: Session, project) -> str:
    from .pipeline import get_secret
    api_key = get_secret(db, "openai_api_key", project=project)
    if not api_key:
        raise PermanentFailure("No OpenAI API key configured.", kind="auth")
    return api_key


def settings_problem(db: Session, project, *, stage: int) -> Optional[str]:
    """
    A problem with the project's SETTINGS that would fail every picture of
    this step alike — today, a style picture the prompt asks for that is
    not on disk. Asked BEFORE any picture is claimed: raised per picture,
    it marked each photo of a send as permanently failed (attempts 999)
    for what was one missing file (site audit, 2026-10-29, v279).
    """
    from .pipeline import get_setting
    from .config import WORKSPACE_DIR
    rel = str(get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / rel if rel else Path("")
    if stage == 2:
        if bool(get_setting(db, "step2_send_style", project=project)) and not style.is_file():
            return ("Step 2 is set to send the style picture, but it is not on the "
                    "server. Upload it again on the Pipeline page, or switch off "
                    "sending it. Nothing was sent; the pictures wait.")
        return None
    if bool(get_setting(db, "openai_use_style_image", project=project)) and not style.is_file():
        return ("The prompt is set to use a style reference image, but it is not on "
                "the server. Upload it again on the Pipeline page, or turn off "
                "'Send the style reference image'. Nothing was sent; the photos wait.")
    return None


def step1_images(db: Session, project, *, source: Path, style: Path) -> list:
    """
    The pictures a PAINTING request carries, as (name, bytes, mime), in the
    order the prompt refers to them. Shared by the live call and the batch
    file, so "which images, in which order" is decided in one place.
    """
    from .pipeline import get_setting
    use_style = bool(get_setting(db, "openai_use_style_image", project=project))
    if use_style and not style.is_file():
        raise PermanentFailure(
            "The prompt is set to use a style reference image, but none has "
            "been uploaded. Either upload one on the Pipeline page, or turn "
            "off 'Send the style reference image' there.",
            kind="bad_request")
    if not source.is_file():
        raise PermanentFailure(f"Source image is missing: {source}", kind="bad_request")
    # The reference FIRST. Every prompt that mentions two pictures calls the
    # reference "the first image", so the order is part of the instruction
    # rather than a detail — swap it and the model styles the reference to
    # look like the photograph.
    return [_part(style), _part(source)] if use_style else [_part(source)]


# The sizes gpt-image-2 accepts, for step 2's "same shape as the painting".
_SHAPES = (("1024x1536", 1024 / 1536), ("1024x1024", 1.0), ("1536x1024", 1536 / 1024))


def same_shape_size(width: int, height: int) -> str:
    """The accepted size closest in shape to a picture — the tester's rule."""
    ratio = (width or 1) / (height or 1)
    return min(_SHAPES, key=lambda s: abs(s[1] - ratio))[0]


def request_fields(db: Session, project, *, stage: int, location: str,
                   input_size: Optional[tuple] = None) -> dict:
    """
    Everything a request says apart from its pictures: model, prompt, size,
    quality, background. Stage 1 reads the openai_* settings, stage 2 the
    step2_* ones. Shared by the live call and the batch file.

    {LOCATION} is filled in HERE, before the prompt is sent. It was the one
    placeholder the generation prompt never resolved — the upload title,
    keywords and description have always been rendered, but the prompt went
    out raw, so the model invented the poster text from the picture.
    """
    from .pipeline import get_setting
    if stage == 2:
        prompt = str(get_setting(db, "step2_prompt", project=project) or "").strip()
        if not prompt:
            raise PermanentFailure(
                "Step 2 is switched on but its prompt is empty. Write it on "
                "the Settings page (Step 2), or switch step 2 off.",
                kind="bad_request")
        model = str(get_setting(db, "step2_model", project=project))
        size = str(get_setting(db, "step2_size", project=project) or "auto")
        if size == "same":
            size = same_shape_size(*(input_size or (1024, 1536)))
        quality = str(get_setting(db, "step2_quality", project=project) or "auto")
        background = str(get_setting(db, "step2_background", project=project) or "auto")
    else:
        prompt = str(get_setting(db, "openai_prompt", project=project))
        model = str(get_setting(db, "openai_model", project=project))
        size = str(get_setting(db, "openai_size", project=project) or "auto")
        quality = str(get_setting(db, "openai_quality", project=project) or "auto")
        background = str(get_setting(db, "openai_background", project=project) or "auto")
    prompt = prompt.replace("{LOCATION}", location).replace("{location}", location)
    data = {"model": model, "prompt": prompt, "n": "1"}
    # "auto" means omit the parameter entirely, letting the API decide.
    # Transparent is the owner's production look for the PAINTING (MEASURED
    # 2026-09-05); step 2 is opaque by his test (2026-10-28).
    if size != "auto":
        data["size"] = size
    if quality != "auto":
        data["quality"] = quality
    if background != "auto":
        data["background"] = background
    return data


# Step 2 sends the painting at no more than this on its long side — what the
# laptop tester sent when the owner settled the prompt (SEND_MAX_EDGE there).
# A painting is ~1024x1536 already, so this normally changes nothing.
STEP2_MAX_EDGE = 1536


def step2_input(master_bytes: bytes) -> tuple[bytes, tuple[int, int]]:
    """
    The painting as step 2 receives it: the see-through original as a PNG,
    see-through parts KEPT (the owner's tested input), no bigger than
    STEP2_MAX_EDGE. Returns (png bytes, (width, height)).
    """
    import io
    from PIL import Image
    with Image.open(io.BytesIO(master_bytes)) as im:
        im.load()
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")
        if max(im.size) > STEP2_MAX_EDGE:
            im.thumbnail((STEP2_MAX_EDGE, STEP2_MAX_EDGE), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue(), im.size


def step2_images(db: Session, project, *, painting_png: bytes,
                 style: Path) -> list:
    """The pictures a STEP-2 request carries: the painting, plus the style
    picture first only if step2_send_style asks (off in his tests)."""
    from .pipeline import get_setting
    parts = [("painting.png", painting_png, "image/png")]
    if bool(get_setting(db, "step2_send_style", project=project)):
        if not style.is_file():
            raise PermanentFailure(
                "Step 2 is set to send the style picture, but none has been "
                "uploaded.", kind="bad_request")
        parts.insert(0, _part(style))
    return parts


def generate_step2(db: Session, *, painting_png: bytes, input_size: tuple,
                   style: Path, project, location: str = "",
                   log_fn=None) -> Generation:
    """STEP 2, one at a time at full price (batch switched off)."""
    def emit(msg, level="info"):
        if log_fn:
            log_fn(msg, level=level)
    api_key = api_key_for(db, project)
    data = request_fields(db, project, stage=2, location=location,
                          input_size=input_size)
    files = [("image[]", p) for p in step2_images(
        db, project, painting_png=painting_png, style=style)]
    headers = {"Authorization": f"Bearer {api_key}"}
    return _send_and_read(headers, data, files, emit)


# ════════════════════════════════════════════════════════════════════════════
#  THE BATCH API — half price, answered within 24 hours (v266)
# ════════════════════════════════════════════════════════════════════════════
#
# Shapes read from OpenAI's own pages on 2026-10-28, not guessed:
#   · a JSONL file, one line per request:
#       {"custom_id", "method": "POST", "url": "/v1/images/edits", "body"}
#     where `body` is the JSON form of the edit request, and pictures go in
#     `images: [{"image_url": "<data URL>"}]` (each at most 20,971,520
#     characters);
#   · the file goes up to /v1/files with purpose "batch" (200 MB at most);
#   · /v1/batches {input_file_id, endpoint, completion_window: "24h"};
#   · results come back as an output file, one line per success, and an
#     error file for the rest — matched by custom_id, NOT by order.
# OpenAI's batch guide names the gpt-image-2.5 models for images and its
# gpt-image-2 model page lists Batch as supported; the owner chose to start
# without a test batch (2026-10-28). If OpenAI refuses the model, the send
# fails loudly with OpenAI's words and nothing is charged.

BATCH_FILE_MAX_BYTES = 180 * 1024 * 1024     # under OpenAI's 200 MB, with room
DATA_URL_MAX_CHARS = 20_971_520
API_ROOT = "https://api.openai.com/v1"


def data_url(part: tuple) -> str:
    _name, raw, mime = part
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def batch_body(fields: dict, parts: list) -> dict:
    """The JSON body of one batch line: the request fields plus pictures."""
    body = {k: v for k, v in fields.items() if k != "n"}
    body["n"] = 1
    images = []
    for part in parts:
        url = data_url(part)
        if len(url) > DATA_URL_MAX_CHARS:
            raise PermanentFailure(
                f"The picture {part[0]} is too big to send in a batch "
                f"({len(part[1]) // (1024 * 1024)} MB).", kind="bad_request")
        images.append({"image_url": url})
    body["images"] = images
    return body


def _api(method: str, path: str, api_key: str, **kw):
    try:
        resp = requests.request(method, f"{API_ROOT}{path}",
                                headers={"Authorization": f"Bearer {api_key}"},
                                timeout=kw.pop("timeout", 300), **kw)
    except requests.RequestException as e:
        raise TransientFailure(f"network error: {e}")
    if resp.status_code >= 400:
        raise _classify(resp.status_code, resp.text)
    return resp


def batch_upload_file(api_key: str, path: Path) -> str:
    with open(path, "rb") as fh:
        resp = _api("POST", "/files", api_key, timeout=1800,
                    data={"purpose": "batch"},
                    files={"file": (path.name, fh, "application/jsonl")})
    return resp.json()["id"]


def batch_create(api_key: str, input_file_id: str) -> dict:
    return _api("POST", "/batches", api_key, json={
        "input_file_id": input_file_id,
        "endpoint": "/v1/images/edits",
        "completion_window": "24h",
    }).json()


def batch_get(api_key: str, batch_id: str) -> dict:
    return _api("GET", f"/batches/{batch_id}", api_key, timeout=60).json()


def batch_cancel(api_key: str, batch_id: str) -> dict:
    return _api("POST", f"/batches/{batch_id}/cancel", api_key, timeout=60).json()


def file_download(api_key: str, file_id: str, dest: Path) -> None:
    """Stream a result file to disk — a few hundred pictures is gigabytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(f"{API_ROOT}/files/{file_id}/content",
                          headers={"Authorization": f"Bearer {api_key}"},
                          stream=True, timeout=600) as resp:
            if resp.status_code >= 400:
                raise _classify(resp.status_code, resp.text)
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(1024 * 1024):
                    fh.write(chunk)
    except requests.RequestException as e:
        raise TransientFailure(f"network error: {e}")


def classify_line_error(status: int, body) -> Exception:
    """A failed batch line, judged by the same rules as a live failure."""
    text = body if isinstance(body, str) else json.dumps(body or {})
    return _classify(int(status or 0), text)


def _send_and_read(headers: dict, data: dict, files: list, emit) -> Generation:
    """
    POST one edit request with the retry rules above, and read the picture
    out of the answer. Shared by step 1 (`generate`) and step 2
    (`generate_step2`) so the two can never disagree about what is worth a
    retry and what is not.
    """
    started = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            emit(f"attempt {attempt}/{MAX_RETRIES}")
        try:
            resp = requests.post(API_URL, headers=headers, data=data,
                                 files=files, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise TransientFailure(f"network error: {e}")
            wait = BASE_BACKOFF * (2 ** (attempt - 1))
            emit(f"network error, retrying in {wait:.0f}s", "warn")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            break

        err = _classify(resp.status_code, resp.text)
        if isinstance(err, PermanentFailure):
            # No retry, no backoff, no further spend. This is the whole point
            # of classifying — the reference script would have tried four more
            # times and failed identically each time.
            raise err
        if attempt == MAX_RETRIES:
            raise err
        wait = BASE_BACKOFF * (2 ** (attempt - 1))
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                wait = max(wait, float(retry_after))
            except ValueError:
                pass
        emit(f"HTTP {resp.status_code}, retrying in {wait:.0f}s", "warn")
        time.sleep(wait)
    else:
        raise TransientFailure("no response after retries")

    return parse_image_payload(resp.json(), started)


def parse_image_payload(payload: dict, started: float) -> Generation:
    """
    The picture and token counts out of an images/edits answer. ONE reader
    for the live call and for a Batch API result line, whose `body` is the
    very same JSON — two readers of one shape would be two chances to drift.
    """
    items = (payload or {}).get("data") or []
    if not items:
        raise TransientFailure("response contained no image data")

    b64 = items[0].get("b64_json")
    if b64:
        image_bytes = base64.b64decode(b64)
    else:
        url = items[0].get("url")
        if not url:
            raise TransientFailure("response item carried neither b64_json nor url")
        image_bytes = requests.get(url, timeout=300).content

    usage = payload.get("usage") or {}
    detail = usage.get("input_tokens_details") or {}
    return Generation(
        image_bytes=image_bytes,
        input_tokens=int(detail.get("image_tokens") or usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        text_tokens=int(detail.get("text_tokens") or 0),
        duration_ms=int((time.time() - started) * 1000),
    )

