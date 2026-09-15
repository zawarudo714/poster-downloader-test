"""
The place check: does the worker's photograph show the place the title names?

═══════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
═══════════════════════════════════════════════════════════════════════════
On 2026-09-15 the owner caught a worker image that was not the place at all,
by putting it through Google Lens by hand. This module does that step for
him: each saved worker image is shown to Google's Cloud Vision "web
detection" (the official, paid cousin of Lens — Lens itself has no API),
and Google's words are compared to the title. The result is a hint for the
owner's eye on the Worker Images screen, never an automatic reject.

═══════════════════════════════════════════════════════════════════════════
THE DESIGN CONTRACT
═══════════════════════════════════════════════════════════════════════════
· One verdict per row, forever. A SavedPoster's file is immutable — a swap
  or replacement creates a SUCCESSOR row — so a row is checked once and the
  answer never goes stale. There is deliberately no re-check machinery.

· Google's answer is a fact about the IMAGE; the verdict is a fact about
  the PAIR (image, title). So a duplicate image (same content_hash) reuses
  the stored Google words without paying Google again, but the verdict is
  always recomputed against the new row's own title. Copying the verdict
  itself would be wrong the day the same photo is saved on two titles.

· "Google had no opinion" and "we could not ask Google" are DIFFERENT
  answers, stored differently (status 'no_opinion' vs status NULL with
  place_check_error). A dead API key must look broken, not normal.

· The bias is toward CATCHING, at the owner's instruction (2026-09-15):
  any Google answer that shares no word with the title is a 'mismatch',
  even a vague one like "temple". He would rather glance at a few false
  alarms than miss a wrong place. 'no_opinion' is only a truly empty
  answer.

· The check must never make a worker wait: the save doors run it on a
  background thread AFTER their commit (launch_check), the same daemon-
  thread pattern the importer and the zip builder already use. A crash in
  the thread is written onto the row as place_check_error — the row itself
  is the report, so there is no claimed work that can end in silence.

· Endpoint and shapes MEASURED from Google's own docs, 2026-09-15:
  POST https://vision.googleapis.com/v1/images:annotate with
  {"requests":[{"image":{"content":<base64>},
                "features":[{"type":"WEB_DETECTION","maxResults":N}]}]}
  → responses[0].webDetection.{bestGuessLabels[].label,
                               webEntities[].description}
  Auth here is the simple API key (?key=...), which the owner creates in
  Google Cloud and pastes into the dashboard. UNVERIFIED against a live
  key from this box: if Google refuses key auth, the refusal lands in
  place_check_error where the owner can read it, not in silence.
"""

import base64
import hashlib
import io
import threading
import unicodedata
from datetime import datetime

import requests
from sqlalchemy.orm import Session

from .models import SavedPoster, MasterTitle
from .utils import saved_poster_path

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"

# Words too common to mean anything about WHICH place this is. Deliberately
# short — over-filtering makes the catching bias blind. Photo-site noise
# words are here because they appear in best-guess labels constantly and
# would otherwise "match" nothing while still counting as an opinion.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "una", "las", "los", "del", "der",
    "photo", "photos", "image", "images", "picture", "pictures", "wallpaper",
    "stock", "royalty", "free", "download", "view", "views", "tourism",
    "travel", "usa", "hd",
}


def _fold(text: str) -> str:
    """Lower-case and strip accents, so 'São Paulo' and 'sao paulo' agree."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _words(text: str) -> set:
    out = set()
    for raw in _fold(text).replace("-", " ").split():
        w = "".join(c for c in raw if c.isalnum())
        if len(w) >= 3 and w not in _STOPWORDS:
            out.add(w)
    return out


def _overlaps(title_words: set, guess_words: set) -> bool:
    """
    True if any meaningful word is shared. A longer word also matches its
    own prefix ('mountain' ↔ 'mountains'), which soaks up plurals without
    inviting 'man' to match 'manchester'.
    """
    for t in title_words:
        for g in guess_words:
            if t == g:
                return True
            if len(t) >= 5 and len(g) >= 5 and (t.startswith(g) or g.startswith(t)):
                return True
    return False


def verdict_for(title_text: str, guess_text: str) -> str:
    """
    'match' / 'mismatch' / 'no_opinion' for one (title words, Google words)
    pair. Pure text-in text-out so the behaviour test can hammer it without
    a database or a network.
    """
    guess_words = _words(guess_text)
    if not guess_words:
        return "no_opinion"
    return "match" if _overlaps(_words(title_text), guess_words) else "mismatch"


def _title_text(mt: MasterTitle) -> str:
    """
    Everything we legitimately call this place: the title itself, the
    sheet's search phrase (which usually carries the city and country),
    and the marketplace name if one was supplied. More correct names means
    fewer false alarms, which the catching bias needs.
    """
    return " ".join(filter(None, (mt.title, mt.search_query, mt.marketplace_title)))


def _prepared_image(path) -> bytes:
    """
    The file, shrunk to something Google-sized. Vision reads a 1024px copy
    as well as a 4000px one, and the smaller upload is faster and kinder to
    the request budget. If PIL cannot read the format, the raw bytes go as
    they are — Vision handles the common formats itself.
    """
    raw = path.read_bytes()
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        img.thumbnail((1024, 1024))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return raw


def _ask_google(image_bytes: bytes, api_key: str) -> str:
    """
    One web-detection call. Returns Google's words as a ';'-joined line —
    best-guess label first, then the top entity names — or "" when Google
    genuinely had nothing. Raises on anything that means WE could not ask.
    """
    body = {"requests": [{
        "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
        "features": [{"type": "WEB_DETECTION", "maxResults": 10}],
    }]}
    r = requests.post(f"{VISION_URL}?key={api_key}", json=body, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Google answered {r.status_code}: {r.text[:300]}")
    payload = r.json()
    first = (payload.get("responses") or [{}])[0]
    if first.get("error"):
        # 200 at the HTTP layer with a per-image error inside — still "we
        # could not ask", not "Google had no opinion".
        raise RuntimeError(f"Google refused the image: {first['error'].get('message', first['error'])}")
    web = first.get("webDetection") or {}
    parts = []
    for lab in web.get("bestGuessLabels") or []:
        if lab.get("label"):
            parts.append(lab["label"])
    for ent in web.get("webEntities") or []:
        desc = (ent.get("description") or "").strip()
        if desc and desc not in parts:
            parts.append(desc)
        if len(parts) >= 8:
            break
    return "; ".join(parts)[:500]


def check_poster(db: Session, poster: SavedPoster) -> str:
    """
    Run the whole check for one row and write the outcome onto it. Returns
    the status word, or 'error'. Never raises: every exit is written to the
    row, because the row is the report the screens read.

    The caller decides WHETHER to run (feature toggle, key present) —
    keeping the gate in one place, at each door, rather than half here and
    half there.
    """
    mt = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
    try:
        path = saved_poster_path(poster)
        if not path.exists():
            raise RuntimeError("the image file is missing on disk")

        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        # content_hash finally gets its producer. It was declared long ago
        # ("deferred to a follow-up worker") and never filled; the check
        # reads the bytes anyway, so the hash is free here.
        if not poster.content_hash:
            poster.content_hash = sha

        # Same bytes already asked about? Reuse Google's ANSWER (a fact
        # about the image) — the verdict is still computed against THIS
        # row's title below.
        twin = (db.query(SavedPoster)
                  .filter(SavedPoster.content_hash == sha,
                          SavedPoster.id != poster.id,
                          SavedPoster.place_check_status.isnot(None))
                  .first())
        if twin is not None:
            guess = twin.place_check_guess or ""
        else:
            from .pipeline import get_secret, project_for_title
            project = project_for_title(db, mt) if mt else None
            # get_secret decrypts, and tolerates a plaintext value — so a key
            # saved before it was a secret still reads, and one saved after is
            # decrypted properly.
            key = get_secret(db, "google_vision_api_key", project=project).strip()
            if not key:
                raise RuntimeError("no Google Vision key on the dashboard")
            guess = _ask_google(_prepared_image(path), key)

        poster.place_check_status = verdict_for(_title_text(mt) if mt else "", guess)
        poster.place_check_guess = guess
        poster.place_check_error = None
        poster.place_check_at = datetime.utcnow()
        return poster.place_check_status
    except Exception as e:                     # noqa: BLE001 — the row IS the report
        poster.place_check_status = None
        poster.place_check_error = str(e)[:500]
        poster.place_check_at = datetime.utcnow()
        return "error"


def launch_check(poster_id: int) -> None:
    """
    Fire-and-forget for the save doors: the worker's save must never wait
    on Google. Own session, own commit; a failure is written onto the row
    inside check_poster, and a failure to even reach the row has nothing to
    strand — the row simply stays "never checked", which the PLACE CHECK
    button's fill loop picks up later.
    """
    def _run():
        from .db import SessionLocal
        db = SessionLocal()
        try:
            poster = db.query(SavedPoster).filter_by(id=poster_id).first()
            if poster is None or poster.deleted_at is not None:
                return
            if poster.place_check_status is not None:
                return                       # already answered, never re-pay
            from .pipeline import get_setting, project_for_title
            mt = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            project = project_for_title(db, mt) if mt else None
            if not bool(int(get_setting(db, "place_check_enabled", project=project) or 0)):
                return
            check_poster(db, poster)
            db.commit()
        except Exception:
            # Nothing claimed, nothing stranded: the row stays unchecked
            # and the fill loop retries it. Rolling back keeps the session
            # clean for the close below.
            db.rollback()
        finally:
            db.close()

    threading.Thread(target=_run, name="place-check", daemon=True).start()
