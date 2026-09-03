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
from decimal import Decimal
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

API_URL = "https://api.openai.com/v1/images/edits"
TIMEOUT_S = 600

# Published per-million-token rates for gpt-image-2. Kept here rather than in
# DEFAULTS because they are OpenAI's numbers, not a preference — if they
# change, this is the one place to correct, and the nightly reconciliation
# against the Costs API is what will tell you they have.
PRICE_PER_MTOK = {
    "image_input":  Decimal("8.00"),
    "image_output": Decimal("30.00"),
    "text_input":   Decimal("5.00"),
}

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

    def cost_usd(self) -> Decimal:
        return (
            PRICE_PER_MTOK["image_input"] * Decimal(self.input_tokens) / Decimal(1_000_000)
            + PRICE_PER_MTOK["image_output"] * Decimal(self.output_tokens) / Decimal(1_000_000)
            + PRICE_PER_MTOK["text_input"] * Decimal(self.text_tokens) / Decimal(1_000_000)
        )


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
             log_fn=None) -> Generation:
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
    from .pipeline import get_secret, get_setting

    def emit(msg, level="info"):
        if log_fn:
            log_fn(msg, level=level)

    api_key = get_secret(db, "openai_api_key", project=project)
    if not api_key:
        raise PermanentFailure("No OpenAI API key configured.", kind="auth")
    use_style = bool(get_setting(db, "openai_use_style_image", project=project))
    if use_style and not style.is_file():
        raise PermanentFailure(
            "The prompt is set to use a style reference image, but none has "
            "been uploaded. Either upload one on the Pipeline page, or turn "
            "off 'Send the style reference image' there.",
            kind="bad_request")
    if not source.is_file():
        raise PermanentFailure(f"Source image is missing: {source}", kind="bad_request")

    data = {
        "model":  str(get_setting(db, "openai_model", project=project)),
        "prompt": str(get_setting(db, "openai_prompt", project=project)),
        "n": "1",
    }
    size = str(get_setting(db, "openai_size", project=project) or "auto")
    quality = str(get_setting(db, "openai_quality", project=project) or "auto")
    if size != "auto":
        data["size"] = size
    if quality != "auto":
        data["quality"] = quality

    if use_style:
        # The reference FIRST. Every prompt that mentions two pictures calls
        # the reference "the first image", so the order is part of the
        # instruction rather than a detail — swap it and the model styles the
        # reference to look like the photograph.
        files = [("image[]", _part(style)), ("image[]", _part(source))]
        emit("sending the style reference and the photo")
    else:
        files = [("image[]", _part(source))]
        emit("sending the photo only — the style reference is switched off")
    headers = {"Authorization": f"Bearer {api_key}"}

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

    payload = resp.json()
    items = payload.get("data") or []
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


# ── Spend ───────────────────────────────────────────────────────────────────

def record_spend(db: Session, *, service: str, operation: str, cost: Decimal,
                 project_id=None, saved_poster_id=None, units: int = 1,
                 input_tokens: int = 0, output_tokens: int = 0,
                 estimated: bool = False) -> None:
    """Append one metered call. Never raises — a bookkeeping failure must not
    lose the work that was actually done."""
    from .models import ApiSpend
    try:
        db.add(ApiSpend(
            service=service, operation=operation,
            project_id=project_id, saved_poster_id=saved_poster_id,
            units=units, input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            cost_usd=str(cost), estimated=1 if estimated else 0,
        ))
    except Exception as e:
        log.error("Could not record API spend: %s", e)


def month_to_date_usd(db: Session, service: Optional[str] = None) -> Decimal:
    """Spend since the 1st, for the cap and the dashboard."""
    from datetime import datetime
    from .models import ApiSpend

    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    q = db.query(ApiSpend.cost_usd).filter(ApiSpend.created_at >= start)
    if service:
        q = q.filter(ApiSpend.service == service)
    total = Decimal("0")
    for (value,) in q.all():
        try:
            total += Decimal(value or "0")
        except Exception:
            continue
    return total


def cap_state(db: Session, project=None) -> dict:
    """
    Where this month's spend stands against the cap.

    Returns {'cap': Decimal, 'spent': Decimal, 'over': bool, 'action': str}.
    `cap` of 0 means no cap, which is the default — a hard stop based on a
    figure nobody set is worse than a message you can act on.
    """
    from .pipeline import get_setting
    try:
        cap = Decimal(str(get_setting(db, "spend_cap_usd_month", project=project) or 0))
    except Exception:
        cap = Decimal("0")
    action = str(get_setting(db, "spend_cap_action", project=project) or "warn")
    spent = month_to_date_usd(db)
    return {
        "cap": cap,
        "spent": spent,
        "over": bool(cap > 0 and spent >= cap),
        "action": action,
    }
