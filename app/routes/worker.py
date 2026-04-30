"""
User-facing routes (file kept named worker.py to preserve internal layout;
URLs / UI no longer say "worker").

Endpoints:
    GET  /                  → user dashboard
    GET  /worker            → same dashboard, explicit (admins can use it)
    GET  /api/state         → JSON state poll
    GET  /api/master        → paginated master browse (read-only; for selecting)
    POST /pull_next         → claim next N pending master titles
    POST /select_titles     → claim a set of master title ids (manual select)
    POST /release           → release untouched claims back to pending
    POST /lock/{master_id}  → set active locked title; returns TMDB deep-link
    POST /unlock            → clear active title
    POST /save_image        → download URL → file under the title's frozen folder
    POST /poster/{id}/delete   → soft-delete a saved poster + remove from disk
    POST /poster/{id}/replace  → replace a saved poster's bytes with a new URL
    POST /title/{id}/complete  → mark master complete
    POST /title/{id}/skip      → mark master skipped (with reason)
    POST /title/{id}/reopen    → revert a complete/skipped master back to in_progress
    POST /revisions/{id}/resolve → resolve own revision (after replace)
    GET  /file_own          → serve own file (for revision preview / inline view)
"""

from __future__ import annotations

import json
import re
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..audit import log as log_activity
from ..auth import require_user
from ..config import (
    ALLOWED_DOWNLOAD_HOSTS, DEFAULT_PULL_SIZE, DOWNLOAD_TIMEOUT_S,
    MASTER_PAGE_SIZE, MAX_DOWNLOAD_BYTES, MAX_PULL_SIZE, RESTRICT_HOSTS,
    SOFT_LIMIT_PER_TITLE,
)
from ..db import get_db
from ..models import ActivityLog, MasterTitle, Revision, SavedPoster, User
from ..parsing import IMAGE_EXT_RE, filename_for, folder_name_for, sanitize
from ..templating import templates
from ..utils import (
    count_live_posters_for_master, count_titles_worked_today,
    count_user_saves_for_date, count_user_saves_for_week,
    saved_poster_folder, saved_poster_path, title_folder_for,
)


router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _my_queue(db: Session, user: User):
    """All MasterTitle rows currently claimed by `user` (any active status)."""
    return (
        db.query(MasterTitle)
          .filter(MasterTitle.claimed_by_id == user.id)
          .order_by(MasterTitle.external_id.asc().nullslast(), MasterTitle.id.asc())
          .all()
    )


def _tmdb_search_url(title: str, content_type: Optional[str]) -> str:
    """Branch by content_type so worker lands on the right TMDB tab."""
    q = quote(title)
    if content_type and content_type.lower() in ("tvseries", "tv", "tv_series", "series"):
        return f"https://www.themoviedb.org/search/tv?query={q}"
    if content_type and content_type.lower() == "movie":
        return f"https://www.themoviedb.org/search/movie?query={q}"
    return f"https://www.themoviedb.org/search?query={q}"


def _serialize_master(t: MasterTitle, db: Session) -> dict:
    """Compact dict for the title list in the user's queue."""
    live = count_live_posters_for_master(db, t.id)
    return {
        "id": t.id,
        "external_id": t.external_id,
        "title": t.title,
        "year": t.year,
        "content_type": t.content_type,
        "description": (t.description or "")[:300],
        "status": t.status,
        "needs_revision": bool(t.needs_revision),
        "saved_count": live,
        "started": t.started_at is not None,
        "started_date": t.original_save_date.isoformat() if t.original_save_date else None,
        "skip_reason": t.skip_reason or "",
        "complete_comment": t.complete_comment or "",
        "admin_note": t.admin_note or "",
    }


def _serialize_poster(p: SavedPoster) -> dict:
    return {
        "id": p.id,
        "filename": p.filename,
        "title_folder": p.title_folder_path,
        "date": p.original_save_date.isoformat(),
        "size": p.file_size,
        "low_quality_url": bool(p.low_quality_url),
        "image_width": p.image_width,
        "image_height": p.image_height,
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def _active_revisions_for_user(db: Session, user: User):
    """Open + awaiting-approval revisions on this user's live posters."""
    import json as _json
    rows = (
        db.query(Revision, SavedPoster, MasterTitle)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              SavedPoster.user_id == user.id,
              SavedPoster.deleted_at.is_(None),
          )
          .order_by(Revision.created_at.desc())
          .all()
    )
    out = []
    for (rev, sp, mt) in rows:
        # Hydrate the related-poster details for 'similar'-type revisions.
        related = []
        if rev.revision_type == "similar" and rev.related_poster_ids:
            try:
                ids = _json.loads(rev.related_poster_ids)
            except Exception:
                ids = []
            if ids:
                related_rows = (
                    db.query(SavedPoster)
                      .filter(SavedPoster.id.in_(ids), SavedPoster.deleted_at.is_(None))
                      .all()
                )
                for r in related_rows:
                    related.append({
                        "poster_id": r.id,
                        "filename": r.filename,
                        "size": r.file_size,
                    })

        # "Was rejected" = status='open' AND admin_verdict was set (admin pushed back).
        was_rejected = (rev.status == "open" and bool(rev.admin_verdict))

        out.append({
            "revision_id": rev.id,
            "status": rev.status,
            "revision_type": rev.revision_type or "simple",
            "comment": rev.comment or "",
            "flagged_by": rev.flagged_by,
            "created_at": rev.created_at.strftime("%Y-%m-%d %H:%M"),
            "submitted_at": rev.submitted_at.strftime("%Y-%m-%d %H:%M") if rev.submitted_at else None,
            "worker_note": rev.worker_note or "",
            "admin_verdict": rev.admin_verdict or "",
            "was_rejected": was_rejected,
            "poster_id": sp.id,
            "filename": sp.filename,
            "related": related,                       # [{poster_id, filename, size}, ...]
            "title": mt.title,
            "year": mt.year,
            "title_folder": sp.title_folder_path,
            "date": sp.original_save_date.isoformat(),
            "master_id": mt.id,
            "tmdb_search": _tmdb_search_url(mt.title, mt.content_type),
        })
    return out


def _state_payload(db: Session, user: User) -> dict:
    today = date_type.today()
    queue = _my_queue(db, user)
    queue_dicts = [_serialize_master(t, db) for t in queue]

    locked_id = user.locked_master_id
    locked = None
    if locked_id:
        lt = db.query(MasterTitle).filter_by(id=locked_id).first()
        if lt and lt.claimed_by_id == user.id:
            locked = _serialize_master(lt, db)
            locked["tmdb_search"] = _tmdb_search_url(lt.title, lt.content_type)
            # Posters already on this title (live only)
            posters = (
                db.query(SavedPoster)
                  .filter(
                      SavedPoster.master_title_id == lt.id,
                      SavedPoster.deleted_at.is_(None),
                  )
                  .order_by(SavedPoster.created_at.asc())
                  .all()
            )
            locked["posters"] = [_serialize_poster(p) for p in posters]
        else:
            # Stale lock — clear it.
            user.locked_master_id = None
            db.commit()

    # Payments transparency: how many of today's saves are awaiting revision.
    from ..payments import count_pending_revisions_today, pending_receipts_for_worker
    pending_count = count_pending_revisions_today(db, user.id)
    # Pushed-but-not-acked receipts from admin.
    pushed = pending_receipts_for_worker(db, user.id)
    receipts = [{
        "id":           r.id,
        "period_start": r.period_start.isoformat(),
        "period_end":   r.period_end.isoformat(),
        "poster_count": r.poster_count,
        "amount_kes":   r.amount_kes,
        "rate_kes":     r.rate_kes,
        "reference":    r.reference or "",
        "note":         r.note or "",
        "pushed_at":    r.pushed_at.strftime("%Y-%m-%d %H:%M") if r.pushed_at else None,
    } for r in pushed]
    # Chat unread (worker viewing their own thread).
    from ..chat import unread_count
    chat_unread = unread_count(db, worker_id=user.id, viewer_id=user.id)

    return {
        "username": user.username,
        "role": user.role,
        "today": today.isoformat(),
        "saved_today":   count_user_saves_for_date(db, user.username, today),
        "saved_week":    count_user_saves_for_week(db, user.username, today),
        "titles_today":  count_titles_worked_today(db, user.id, today),
        "pending_today": pending_count,    # "X not counted until revised"
        "queue": queue_dicts,
        "locked": locked,
        "revisions": _active_revisions_for_user(db, user),
        "receipts": receipts,
        "chat_unread": chat_unread,
        "default_pull_size": user.last_pull_size or DEFAULT_PULL_SIZE,
    }


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        return RedirectResponse("/admin", status_code=302)
    state = _state_payload(db, user)
    return templates.TemplateResponse(
        request,
        "user_dashboard.html",
        {"user": user, "state": state, "active_tab": "dashboard"},
    )


@router.get("/worker", response_class=HTMLResponse)
def worker_view(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Same dashboard — admins can hit this URL to act as a regular user."""
    state = _state_payload(db, user)
    return templates.TemplateResponse(
        request,
        "user_dashboard.html",
        {"user": user, "state": state, "active_tab": "dashboard"},
    )


@router.get("/api/state")
def api_state(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return JSONResponse(_state_payload(db, user))


@router.get("/master_browse", response_class=HTMLResponse)
def master_browse_page(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(
        request,
        "master_browse.html",
        {"user": user, "active_tab": "dashboard"},
    )


# ── Master browse (read-only; for manual select) ─────────────────────────────

@router.get("/api/master")
def api_master(
    page: int = Query(1, ge=1),
    page_size: int = Query(MASTER_PAGE_SIZE, ge=10, le=500),
    q: str = Query(""),
    status: str = Query(""),  # 'pending' | 'in_progress' | 'complete' | 'skipped' | ''=all
    content_type: str = Query(""),
    only_unclaimed: int = Query(1),  # default: hide rows already claimed by someone
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Paginated, filtered master browse for the user's manual-select view.
    Read-only — users cannot edit master content here.
    """
    query = db.query(MasterTitle)

    if status in ("pending", "in_progress", "complete", "skipped"):
        query = query.filter(MasterTitle.status == status)
    if content_type:
        query = query.filter(MasterTitle.content_type == content_type)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(MasterTitle.title.ilike(like))
    if only_unclaimed:
        # Show pending OR rows claimed by me; never others' in-progress.
        query = query.filter(
            or_(
                MasterTitle.claimed_by_id.is_(None),
                MasterTitle.claimed_by_id == user.id,
            )
        )

    total = query.count()
    rows = (
        query
        .order_by(MasterTitle.external_id.asc().nullslast(), MasterTitle.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return JSONResponse({
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": [
            {
                "id": r.id,
                "external_id": r.external_id,
                "title": r.title,
                "year": r.year,
                "content_type": r.content_type,
                "status": r.status,
                "needs_revision": bool(r.needs_revision),
                "claimed_by_name": r.claimed_by_name,
                "mine": (r.claimed_by_id == user.id),
            }
            for r in rows
        ],
    })


# ── Pull / claim ─────────────────────────────────────────────────────────────

@router.post("/pull_next")
def pull_next(
    n: int = Form(DEFAULT_PULL_SIZE),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Claim the next N pending master rows (in external_id order).
    Atomic-ish: SQLite gives us per-statement atomicity; we re-check status
    inside the transaction before flipping.
    """
    if n < 1:
        raise HTTPException(400, "Pull size must be at least 1.")
    n = min(n, MAX_PULL_SIZE)

    rows = (
        db.query(MasterTitle)
          .filter(
              MasterTitle.status == "pending",
              MasterTitle.claimed_by_id.is_(None),
          )
          .order_by(MasterTitle.external_id.asc().nullslast(), MasterTitle.id.asc())
          .limit(n)
          .all()
    )

    now = datetime.utcnow()
    claimed_ids: list[int] = []
    for r in rows:
        # Defensive double-check — could have been claimed by a parallel request.
        if r.claimed_by_id is None and r.status == "pending":
            r.claimed_by_id   = user.id
            r.claimed_by_name = user.username
            r.claimed_at      = now
            r.status          = "in_progress"
            r.updated_at      = now
            claimed_ids.append(r.id)

    user.last_pull_size = n
    log_activity(
        db, user=user, action="claimed", target_type="bulk",
        details={"count": len(claimed_ids), "ids": claimed_ids, "via": "pull_next", "requested": n},
    )
    db.commit()
    return JSONResponse({"ok": True, "claimed": len(claimed_ids), "ids": claimed_ids})


@router.post("/select_titles")
def select_titles(
    ids: str = Form(...),  # comma-separated
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Claim a specific set of master title ids (manual-select flow).
    Skips any that are already claimed by someone else or not pending.
    """
    try:
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    except ValueError:
        raise HTTPException(400, "Bad ids list.")
    if not wanted:
        raise HTTPException(400, "No ids supplied.")

    rows = (
        db.query(MasterTitle)
          .filter(
              MasterTitle.id.in_(wanted),
              MasterTitle.status == "pending",
              MasterTitle.claimed_by_id.is_(None),
          )
          .all()
    )

    now = datetime.utcnow()
    claimed_ids: list[int] = []
    for r in rows:
        r.claimed_by_id   = user.id
        r.claimed_by_name = user.username
        r.claimed_at      = now
        r.status          = "in_progress"
        r.updated_at      = now
        claimed_ids.append(r.id)

    log_activity(
        db, user=user, action="claimed", target_type="bulk",
        details={"count": len(claimed_ids), "ids": claimed_ids, "via": "select_titles", "requested_count": len(wanted)},
    )
    db.commit()
    skipped = len(wanted) - len(claimed_ids)
    return JSONResponse({"ok": True, "claimed": len(claimed_ids), "skipped": skipped, "ids": claimed_ids})


@router.post("/release")
def release(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Release back to the pool any of my claims that have NO saved posters yet.
    Things I've actually started (started_at set) stay mine.
    """
    rows = (
        db.query(MasterTitle)
          .filter(
              MasterTitle.claimed_by_id == user.id,
              MasterTitle.status == "in_progress",
              MasterTitle.started_at.is_(None),
          )
          .all()
    )

    released_ids: list[int] = []
    now = datetime.utcnow()
    for r in rows:
        r.claimed_by_id   = None
        r.claimed_by_name = None
        r.claimed_at      = None
        r.status          = "pending"
        r.updated_at      = now
        released_ids.append(r.id)

    # If the locked title was just released, unlock.
    if user.locked_master_id in released_ids:
        user.locked_master_id = None

    log_activity(
        db, user=user, action="released", target_type="bulk",
        details={"count": len(released_ids), "ids": released_ids},
    )
    db.commit()
    return JSONResponse({"ok": True, "released": len(released_ids), "ids": released_ids})


# ── Lock / unlock ────────────────────────────────────────────────────────────

@router.post("/lock/{master_id}")
def lock_title(
    master_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    if t.claimed_by_id != user.id:
        raise HTTPException(403, "You haven't claimed this title.")

    user.locked_master_id = master_id
    log_activity(db, user=user, action="locked", target_type="master_title", target_id=master_id)
    db.commit()

    return JSONResponse({
        "ok": True,
        "id": master_id,
        "title": t.title,
        "year": t.year,
        "content_type": t.content_type,
        "description": (t.description or "")[:600],
        "tmdb_search": _tmdb_search_url(t.title, t.content_type),
    })


@router.post("/unlock")
def unlock(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.locked_master_id is not None:
        log_activity(db, user=user, action="unlocked", target_type="master_title", target_id=user.locked_master_id)
        user.locked_master_id = None
        db.commit()
    return JSONResponse({"ok": True})


# ── Save image ───────────────────────────────────────────────────────────────

def _validate_image_url(url: str) -> tuple[bool, str]:
    url = url.strip()
    if not url:
        return False, "Empty URL."
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must be http(s)."
    if not parsed.netloc:
        return False, "URL has no host."
    if RESTRICT_HOSTS and parsed.netloc.lower() not in ALLOWED_DOWNLOAD_HOSTS:
        return False, f"Host not allowed: {parsed.netloc}"
    if not IMAGE_EXT_RE.search(parsed.path):
        return False, "URL must end in .jpg/.jpeg/.png/.webp/.gif"
    return True, ""


# Low-quality detection — e.g. 'media.themoviedb.org/t/p/w440_and_h660_face/...'
# vs HD 'image.tmdb.org/t/p/original/...'. We don't tell the worker the exact
# host check; we just nudge them about copying the *link address*.
_LOW_QUALITY_PATH_RE = re.compile(r"/(?:w\d+|h\d+)(?:_and_[wh]\d+)?(?:_face|_filter\(\w+\))?/", re.IGNORECASE)


def _is_low_quality_url(url: str) -> bool:
    """
    Heuristic: if the URL path contains a TMDB size descriptor (w440, h660,
    w300_and_h450_face, etc.) OR isn't on `image.tmdb.org`, treat it as a
    likely low-quality preview link (the kind you get from "Copy image
    address" on a thumbnail).
    """
    parsed = urlparse(url.strip())
    if not parsed.netloc:
        return False
    if parsed.netloc.lower() != "image.tmdb.org":
        return True
    if _LOW_QUALITY_PATH_RE.search(parsed.path):
        return True
    return False


def _download_to(url: str, target_path: Path) -> int:
    """Stream + size-cap download. Returns bytes written. Raises HTTPException on failure."""
    try:
        with requests.get(
            url,
            stream=True,
            timeout=DOWNLOAD_TIMEOUT_S,
            headers={"User-Agent": "Mozilla/5.0 PosterDownloader/1.0"},
        ) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type:
                raise HTTPException(400, "URL returned an HTML page, not an image.")

            written = 0
            with open(target_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        f.close()
                        target_path.unlink(missing_ok=True)
                        raise HTTPException(413, "Image exceeds maximum allowed size.")
                    f.write(chunk)
            return written
    except HTTPException:
        raise
    except requests.RequestException as e:
        raise HTTPException(502, f"Download failed: {e}")


def _ensure_first_save_metadata(t: MasterTitle, today: date_type) -> None:
    """
    Set immutable folder fields on first save. Called inside save_image / replace.
    Once set, never re-derived.
    """
    if t.original_save_date is None:
        t.original_save_date = today
    if not t.title_folder_path:
        num_str = str(t.external_id) if t.external_id is not None else ""
        t.title_folder_path = folder_name_for(num_str, t.title, t.year or "N/A")
    if t.started_at is None:
        t.started_at = datetime.utcnow()


@router.post("/save_image")
def save_image(
    url: str = Form(...),
    confirm_duplicate: int = Form(0),
    confirm_soft_limit: int = Form(0),
    confirm_low_quality: int = Form(0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Download a poster URL into the locked title's frozen folder.
    Returns 409 with reason='duplicate' / 'soft_limit' / 'low_quality' for client to confirm.
    """
    if user.locked_master_id is None:
        raise HTTPException(400, "No active title — open one first.")
    t = db.query(MasterTitle).filter_by(id=user.locked_master_id).first()
    if not t or t.claimed_by_id != user.id:
        raise HTTPException(400, "Active title is invalid; open it again.")
    if t.status in ("complete", "skipped"):
        raise HTTPException(409, f"This title is marked {t.status}. Reopen it to add posters.")

    ok, reason = _validate_image_url(url)
    if not ok:
        raise HTTPException(400, reason)
    src_url = url.strip()

    # Low-quality preview-URL detection (cheap, before any download).
    if _is_low_quality_url(src_url) and not confirm_low_quality:
        return JSONResponse(
            {"ok": False, "reason": "low_quality",
             "message": (
                 "This looks like a low-resolution preview, not the full-size poster. "
                 "On TMDB, click the poster thumbnail first to open the full-size view, "
                 "then right-click and choose 'Copy link address' (not 'Copy image address')."
             )},
            status_code=409,
        )

    # Duplicate URL guard — same URL already saved on this title (live).
    dup = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.master_title_id == t.id,
              SavedPoster.source_url == src_url,
              SavedPoster.deleted_at.is_(None),
          )
          .first()
    )
    if dup and not confirm_duplicate:
        return JSONResponse(
            {"ok": False, "reason": "duplicate",
             "message": "You already saved this URL on this title. Save again anyway?",
             "filename": dup.filename},
            status_code=409,
        )

    # Soft warning at >= SOFT_LIMIT_PER_TITLE.
    live = count_live_posters_for_master(db, t.id)
    if live >= SOFT_LIMIT_PER_TITLE and not confirm_soft_limit:
        return JSONResponse(
            {"ok": False, "reason": "soft_limit",
             "message": f"This title already has {live} posters saved. Save another?",
             "current_count": live, "soft_limit": SOFT_LIMIT_PER_TITLE},
            status_code=409,
        )

    today = date_type.today()
    _ensure_first_save_metadata(t, today)
    db.flush()  # so t.title_folder_path / t.original_save_date are visible to helpers

    folder = title_folder_for(user.username, t.original_save_date, t.title_folder_path)

    # Compute next count using DB live-count + folder probe to avoid filename collisions.
    base = count_live_posters_for_master(db, t.id)
    count = base + 1
    target_name = filename_for(t.title, count, src_url)
    target_path = folder / target_name
    while target_path.exists():
        count += 1
        target_name = filename_for(t.title, count, src_url)
        target_path = folder / target_name

    written = _download_to(src_url, target_path)

    # Read pixel dimensions from the file we just wrote (zero-dep header parse).
    from ..imghdr_lite import read_file_dimensions
    dims = read_file_dimensions(target_path)
    img_w, img_h = (dims if dims else (None, None))

    sp = SavedPoster(
        master_title_id    = t.id,
        user_id            = user.id,
        username           = user.username,
        original_save_date = t.original_save_date,
        title_folder_path  = t.title_folder_path,
        filename           = target_name,
        source_url         = src_url,
        file_size          = written,
        low_quality_url    = 1 if (_is_low_quality_url(src_url) and confirm_low_quality) else 0,
        image_width        = img_w,
        image_height       = img_h,
        # Deferred: content_hash on a follow-up worker — keep save_image fast.
    )
    db.add(sp)
    db.flush()

    log_activity(
        db, user=user, action="saved", target_type="saved_poster", target_id=sp.id,
        details={
            "master_id": t.id, "filename": target_name,
            "title_folder": t.title_folder_path, "url": src_url,
            "size": written,
        },
    )
    db.commit()

    new_live = count_live_posters_for_master(db, t.id)
    return JSONResponse({
        "ok": True,
        "poster_id": sp.id,
        "filename": target_name,
        "title_folder": t.title_folder_path,
        "saved_count_for_title": new_live,
        "saved_today": count_user_saves_for_date(db, user.username, today),
        "saved_week":  count_user_saves_for_week(db, user.username, today),
        "soft_warning": new_live >= SOFT_LIMIT_PER_TITLE,
    })


# ── Delete / replace a saved poster ──────────────────────────────────────────

def _load_my_poster(db: Session, user: User, poster_id: int) -> SavedPoster:
    sp = db.query(SavedPoster).filter_by(id=poster_id).first()
    if not sp or sp.deleted_at is not None:
        raise HTTPException(404, "Poster not found.")
    if sp.user_id != user.id:
        raise HTTPException(403, "Not your poster.")
    return sp


@router.post("/poster/{poster_id}/delete")
def delete_poster(
    poster_id: int,
    note: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    sp = _load_my_poster(db, user, poster_id)
    fs_path = saved_poster_path(sp)
    fs_path.unlink(missing_ok=True)

    sp.deleted_at = datetime.utcnow()
    sp.delete_note = note.strip() or None

    # If there are open OR awaiting_approval revisions on this poster, mark them
    # resolved — the file is gone, nothing to approve. Stash the worker's note
    # on each resolved revision so admin can see why it was deleted.
    revs = (
        db.query(Revision)
          .filter(
              Revision.saved_poster_id == sp.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    for r in revs:
        r.status = "resolved"
        r.resolved_by = user.username
        r.resolved_at = datetime.utcnow()
        r.worker_note = note.strip() or r.worker_note
        suffix = (": " + note.strip()) if note.strip() else ""
        r.admin_verdict = "auto-resolved: file deleted" + suffix

    # Also resolve any 'similar'-type revisions where this poster was a participant.
    import json as _json
    sim_revs = (
        db.query(Revision)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              Revision.revision_type == "similar",
          )
          .all()
    )
    for r in sim_revs:
        try:
            related = _json.loads(r.related_poster_ids or "[]")
        except Exception:
            related = []
        if sp.id in related and r.saved_poster_id != sp.id:
            r.status = "resolved"
            r.resolved_by = user.username
            r.resolved_at = datetime.utcnow()
            r.worker_note = note.strip() or r.worker_note
            suffix = (": " + note.strip()) if note.strip() else ""
            r.admin_verdict = "auto-resolved: similar-pair file deleted" + suffix

    # Recompute needs_revision flag on the master title
    mt = db.query(MasterTitle).filter_by(id=sp.master_title_id).first()
    if mt:
        any_active = (
            db.query(Revision)
              .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
              .filter(
                  SavedPoster.master_title_id == mt.id,
                  Revision.status.in_(("open", "awaiting_approval")),
              )
              .count()
        )
        mt.needs_revision = 1 if any_active else 0

    log_activity(
        db, user=user, action="deleted", target_type="saved_poster", target_id=sp.id,
        details={"filename": sp.filename, "master_id": sp.master_title_id,
                 "note": note.strip() or None},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/poster/{poster_id}/replace")
def replace_poster(
    poster_id: int,
    url: str = Form(...),
    worker_note: str = Form(""),
    confirm_low_quality: int = Form(0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Replace a saved poster's bytes (and filename, if URL extension differs).
    Used both for fixing flagged revisions and general redo.

    If the poster has open revisions, those go to 'awaiting_approval' so
    admin can verify the replacement before clearing the flag.

    Returns 409 reason='low_quality' if the URL looks like a TMDB preview;
    client can re-call with confirm_low_quality=1 to bypass.
    """
    sp = _load_my_poster(db, user, poster_id)
    ok, reason = _validate_image_url(url)
    if not ok:
        raise HTTPException(400, reason)
    src_url = url.strip()

    if _is_low_quality_url(src_url) and not confirm_low_quality:
        return JSONResponse(
            {"ok": False, "reason": "low_quality",
             "message": (
                 "This looks like a low-resolution preview, not the full-size poster. "
                 "On TMDB, click the poster thumbnail first to open the full-size view, "
                 "then right-click and choose 'Copy link address' (not 'Copy image address')."
             )},
            status_code=409,
        )

    # Reuse the same poster index so naming stays stable. e.g. "Title 2.jpg" → "Title 2.webp"
    folder = saved_poster_folder(sp)
    folder.mkdir(parents=True, exist_ok=True)
    # Extract the count number from the existing filename ("...{title} {n}.ext")
    m = re.search(r" (\d+)\.(jpg|jpeg|png|webp|gif)$", sp.filename, re.IGNORECASE)
    count = int(m.group(1)) if m else 1
    # Delete the old physical file before writing the new one.
    old_fs = saved_poster_path(sp)
    old_filename = sp.filename
    old_fs.unlink(missing_ok=True)

    mt = sp.master_title  # eager via relationship if loaded; safe to access
    new_name = filename_for(mt.title if mt else "Replacement", count, src_url)
    target = folder / new_name
    # If by some race the new name already exists, bump until unique.
    while target.exists():
        count += 1
        new_name = filename_for(mt.title if mt else "Replacement", count, src_url)
        target = folder / new_name

    written = _download_to(src_url, target)
    from ..imghdr_lite import read_file_dimensions
    dims = read_file_dimensions(target)
    img_w, img_h = (dims if dims else (None, None))

    sp.filename        = new_name
    sp.source_url      = src_url
    sp.file_size       = written
    sp.low_quality_url = 1 if (_is_low_quality_url(src_url) and confirm_low_quality) else 0
    sp.image_width     = img_w
    sp.image_height    = img_h

    # Move any open revision on this poster to 'awaiting_approval'.
    # Includes 'similar'-type revisions where this poster is the primary.
    open_revs = (
        db.query(Revision)
          .filter_by(saved_poster_id=sp.id, status="open")
          .all()
    )
    submitted_ids = []
    for r in open_revs:
        r.status = "awaiting_approval"
        r.submitted_at = datetime.utcnow()
        r.worker_note = worker_note.strip() or None
        submitted_ids.append(r.id)

    # Also handle 'similar'-type revisions where this poster appears in
    # related_poster_ids (admin marked it as similar to another). Replacing
    # any of the linked posters resolves the comparison.
    import json as _json
    candidate_revs = (
        db.query(Revision)
          .filter(Revision.status == "open", Revision.revision_type == "similar")
          .all()
    )
    for r in candidate_revs:
        try:
            related = _json.loads(r.related_poster_ids or "[]")
        except Exception:
            related = []
        if sp.id in related and r.id not in submitted_ids:
            r.status = "awaiting_approval"
            r.submitted_at = datetime.utcnow()
            r.worker_note = worker_note.strip() or None
            submitted_ids.append(r.id)

    log_activity(
        db, user=user, action="replaced", target_type="saved_poster", target_id=sp.id,
        details={"old_filename": old_filename, "new_filename": new_name,
                 "url": src_url, "size": written, "submitted_revisions": submitted_ids},
    )
    db.commit()
    return JSONResponse({
        "ok": True,
        "poster_id": sp.id,
        "filename": new_name,
        "submitted_revisions": submitted_ids,
    })


# ── Mark complete / skip / reopen ────────────────────────────────────────────

def _load_my_master(db: Session, user: User, master_id: int) -> MasterTitle:
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    if t.claimed_by_id != user.id:
        raise HTTPException(403, "Not your title.")
    return t


@router.post("/title/{master_id}/complete")
def title_complete(
    master_id: int,
    comment: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = _load_my_master(db, user, master_id)
    t.status = "complete"
    t.completed_at = datetime.utcnow()
    t.complete_comment = comment.strip() or None
    t.admin_note = None  # auto-clear admin's send-back note when title is finished
    if user.locked_master_id == master_id:
        user.locked_master_id = None
    log_activity(
        db, user=user, action="completed", target_type="master_title", target_id=t.id,
        details={"comment": comment.strip() or None},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/title/{master_id}/skip")
def title_skip(
    master_id: int,
    reason: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = _load_my_master(db, user, master_id)
    t.status = "skipped"
    t.skip_reason = reason.strip() or None
    t.admin_note = None  # clear admin's prior send-back note
    if user.locked_master_id == master_id:
        user.locked_master_id = None
    log_activity(
        db, user=user, action="skipped", target_type="master_title", target_id=t.id,
        details={"reason": reason.strip() or None},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/title/{master_id}/reopen")
def title_reopen(
    master_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = _load_my_master(db, user, master_id)
    if t.status not in ("complete", "skipped"):
        raise HTTPException(400, "Title isn't completed or skipped.")
    t.status = "in_progress"
    t.completed_at = None
    t.skip_reason = None
    log_activity(db, user=user, action="reopened", target_type="master_title", target_id=t.id)
    db.commit()
    return JSONResponse({"ok": True})


# ── Revisions (resolve directly without replace, e.g. user fixed elsewhere) ──

@router.post("/revisions/{revision_id}/resolve")
def resolve_revision(
    revision_id: int,
    worker_note: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Worker says 'I've handled this' — sends to admin for approval."""
    rev = db.query(Revision).filter_by(id=revision_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found.")
    sp = db.query(SavedPoster).filter_by(id=rev.saved_poster_id).first()
    if not sp or sp.user_id != user.id:
        raise HTTPException(403, "Not your revision.")
    rev.status = "awaiting_approval"
    rev.submitted_at = datetime.utcnow()
    rev.worker_note = worker_note.strip() or None
    log_activity(db, user=user, action="submitted_for_approval", target_type="revision", target_id=rev.id)
    db.commit()
    return JSONResponse({"ok": True})


# ── Serve own files (for in-page preview) ───────────────────────────────────

@router.get("/file_own/{poster_id}")
def serve_own_file(
    poster_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    sp = db.query(SavedPoster).filter_by(id=poster_id).first()
    if not sp or sp.deleted_at is not None or sp.user_id != user.id:
        raise HTTPException(404, "File not found.")
    p = saved_poster_path(sp)
    if not p.is_file():
        raise HTTPException(404, "File missing on disk.")
    return FileResponse(p)


# ── Chat (worker side) ─────────────────────────────────────────────────────

@router.get("/api/chat")
def chat_poll(
    after: int = Query(0, ge=0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Worker view: poll the (admin, me) thread.
    `after` is the highest message id the client already has — server returns
    only newer ones, plus an unread count summary.
    """
    from ..chat import list_messages, serialize_message, unread_count
    rows = list_messages(db, worker_id=user.id, after_id=(after or None), limit=200)
    return JSONResponse({
        "ok": True,
        "messages": [serialize_message(m) for m in rows],
        "unread":   unread_count(db, worker_id=user.id, viewer_id=user.id),
    })


@router.post("/api/chat/send")
def chat_send(
    body: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from ..chat import send_message, serialize_message
    try:
        msg = send_message(db, worker_id=user.id, sender=user, body=body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(db, user=user, action="chat_sent", target_type="chat", target_id=msg.id)
    db.commit()
    return JSONResponse({"ok": True, "message": serialize_message(msg)})


@router.post("/api/chat/mark_read")
def chat_mark_read(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Worker marks their own thread as read."""
    from ..chat import mark_read
    mark_read(db, worker_id=user.id, viewer_id=user.id)
    db.commit()
    return JSONResponse({"ok": True})


# ── Payment receipts (worker acknowledge) ──────────────────────────────────

@router.post("/api/receipts/{run_id}/ack")
def acknowledge_receipt(
    run_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Worker confirms they saw a pushed receipt. After ack, the receipt
    disappears from their dashboard and the admin sees the ack timestamp.
    """
    from ..models import PaymentRun
    run = db.query(PaymentRun).filter_by(id=run_id).first()
    if run is None or run.worker_id != user.id:
        raise HTTPException(404, "Receipt not found.")
    if run.pushed_at is None:
        raise HTTPException(400, "Receipt was never pushed to you.")
    if run.ack_at is None:
        run.ack_at = datetime.utcnow()
        log_activity(db, user=user, action="receipt_ack", target_type="payment_run", target_id=run.id)
        db.commit()
    return JSONResponse({"ok": True})


@router.get("/chat", response_class=HTMLResponse)
def chat_view(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """
    Worker chat page — they only ever see one thread (with admins).
    Admins hitting this URL get bounced to the admin chat hub.
    """
    if user.role == "admin":
        return RedirectResponse("/admin/chat", status_code=302)
    return templates.TemplateResponse(
        request, "user_chat.html",
        {"user": user, "active_tab": "chat"},
    )


# ── Save history (worker transparency) ────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
def history_view(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """
    Worker-facing save history. Shows one row per day they worked, with the
    poster count + computed amount based on the current rate. Clicking a day
    expands to the per-title breakdown (loaded via /api/history/day).
    """
    if user.role == "admin":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "user_history.html",
        {"user": user, "active_tab": "history"},
    )


@router.get("/api/history/days")
def api_history_days(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Per-day summary for the current worker — one row per save_date that
    has at least one live poster. Each row breaks the count into:
       paid       — already in a past PaymentRun
       eligible   — counts toward future pay (live, no open revision)
       pending    — has open / awaiting-approval revision (transparency)
    Plus the computed amount for the eligible bucket at the current rate.
    Newest day first.
    """
    from ..payments import (
        get_rate_kes, parse_decimal, _already_paid_poster_ids,
    )
    rate = get_rate_kes(db)
    rate_dec = parse_decimal(rate)
    paid_ids = _already_paid_poster_ids(db, user.id)

    # All this worker's live poster rows: id + save_date.
    rows = (
        db.query(SavedPoster.id, SavedPoster.original_save_date)
          .filter(
              SavedPoster.user_id == user.id,
              SavedPoster.deleted_at.is_(None),
          )
          .all()
    )
    if not rows:
        return JSONResponse({"ok": True, "days": [], "rate_kes": str(rate_dec)})

    all_ids = {pid for pid, _d in rows}

    # Block list: posters under any open / awaiting-approval revision.
    blocked = set()
    blocked_rows = (
        db.query(Revision.saved_poster_id)
          .filter(
              Revision.saved_poster_id.in_(all_ids),
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    for (pid,) in blocked_rows:
        blocked.add(pid)
    # Similar-pair related list also counts as blocked.
    sim_rows = (
        db.query(Revision.related_poster_ids)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              Revision.revision_type == "similar",
          )
          .all()
    )
    for (raw,) in sim_rows:
        if not raw:
            continue
        try:
            for pid in json.loads(raw):
                if isinstance(pid, int) and pid in all_ids:
                    blocked.add(pid)
        except (TypeError, ValueError):
            pass

    # Bucket by date.
    by_day: dict[str, dict] = {}
    for pid, d in rows:
        key = d.isoformat()
        b = by_day.setdefault(key, {"date": key, "paid": 0, "eligible": 0, "pending": 0})
        if pid in paid_ids:
            b["paid"] += 1
        elif pid in blocked:
            b["pending"] += 1
        else:
            b["eligible"] += 1

    days = sorted(by_day.values(), key=lambda r: r["date"], reverse=True)
    # Compute amounts.
    for d in days:
        # Eligible amount = eligible_count × rate. Paid amount = paid_count × rate
        # (assumes rate didn't change — informational only; the actual paid
        # amount is on the PaymentRun row, which we surface elsewhere).
        d["eligible_amount_kes"] = str(rate_dec * d["eligible"])
        d["paid_amount_kes"]     = str(rate_dec * d["paid"])

    return JSONResponse({
        "ok": True,
        "days":      days,
        "rate_kes":  str(rate_dec),
    })


@router.get("/api/history/day/{d}")
def api_history_day(
    d: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Per-title breakdown for a single date: each MasterTitle the worker
    saved on that day with the count of live posters + their state
    (paid / eligible / pending).
    """
    from ..payments import _already_paid_poster_ids
    try:
        target_d = date_type.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, "Bad date.")

    paid_ids = _already_paid_poster_ids(db, user.id)

    # Posters this worker saved that day, joined to their MasterTitle.
    rows = (
        db.query(SavedPoster.id, SavedPoster.master_title_id, MasterTitle.title, MasterTitle.year)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(
              SavedPoster.user_id == user.id,
              SavedPoster.deleted_at.is_(None),
              SavedPoster.original_save_date == target_d,
          )
          .all()
    )
    if not rows:
        return JSONResponse({"ok": True, "date": d, "titles": []})

    all_ids = {r[0] for r in rows}

    # Block list — same logic as /api/history/days.
    blocked = set()
    blocked_rows = (
        db.query(Revision.saved_poster_id)
          .filter(
              Revision.saved_poster_id.in_(all_ids),
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    for (pid,) in blocked_rows:
        blocked.add(pid)
    sim_rows = (
        db.query(Revision.related_poster_ids)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              Revision.revision_type == "similar",
          )
          .all()
    )
    for (raw,) in sim_rows:
        if not raw:
            continue
        try:
            for pid in json.loads(raw):
                if isinstance(pid, int) and pid in all_ids:
                    blocked.add(pid)
        except (TypeError, ValueError):
            pass

    # Aggregate per master title.
    by_title: dict[int, dict] = {}
    for pid, mid, title, year in rows:
        b = by_title.setdefault(mid, {
            "master_id": mid, "title": title, "year": year,
            "paid": 0, "eligible": 0, "pending": 0, "total": 0,
        })
        b["total"] += 1
        if pid in paid_ids:    b["paid"] += 1
        elif pid in blocked:    b["pending"] += 1
        else:                   b["eligible"] += 1

    titles = sorted(by_title.values(), key=lambda r: r["title"].lower())
    return JSONResponse({"ok": True, "date": d, "titles": titles})
