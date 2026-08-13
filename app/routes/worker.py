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
from ..models import ActivityLog, MasterTitle, Project, Revision, SavedPoster, User
from ..pipeline import resolve_project
from ..projects import (
    active_project, allowed_projects, remember_project, scope_titles,
    set_project_cookie,
)
from ..timeutil import fmt_local, local_today
from ..parsing import IMAGE_EXT_RE, filename_for, folder_name_for, sanitize
from ..templating import templates
from ..utils import (
    count_live_posters_for_master, count_titles_worked_today,
    count_user_saves_for_date, count_user_saves_for_week,
    saved_poster_folder, saved_poster_path, title_folder_for,
)


router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

# ── Project scoping ──────────────────────────────────────────────────────────

def _my_projects(db: Session, user: User):
    """
    Every project this worker is allowed to touch. Used for the switcher and
    for permission checks — NOT for scoping the queue. See _worker_project().

    A worker with no assignment falls back to the default project, so the
    existing install keeps working untouched.
    """
    return allowed_projects(db, user)


def _project_ui(db: Session, project_id) -> dict:
    """
    The bits of a project the worker's screen needs to render itself.

    Sent with every title so the front end never assumes a niche: whether to
    show an external source link or the in-page grid, how many images are
    expected, and what this project calls the thing being saved. A third
    project changes these by declaring them, not by editing templates.
    """
    proj = resolve_project(db, project_id)
    return {
        "search_mode":      proj.search_mode,
        "images_per_title": proj.images_per_title,
        "item_noun":        proj.item_noun,
        "item_nouns":       proj.item_noun_plural,
    }


def _default_project_id_cached(db: Session):
    """The default project's id — NULL project_id means this one."""
    from ..pipeline import _default_project_id
    return _default_project_id(db)


def _worker_project(request, db: Session, user: User):
    """
    The ONE project a worker is currently standing in.

    ════════════════════════════════════════════════════════════════════════
    WHY NOT "ALL THEIR PROJECTS AT ONCE"
    ════════════════════════════════════════════════════════════════════════
    The first version of this scoped the queue to the UNION of a worker's
    projects, on the theory that they draw from everything assigned to them.
    That was wrong, and wrong in a way that only shows up once a worker has
    two projects:

      · GET pulled a mixture — movie titles interleaved with artists, because
        both number from 1 and the query ordered by external_id across both.
      · Browse All Titles listed 201,133 rows: every movie AND every artist.
      · RETURN UNWORKED handed back titles from a project the worker wasn't
        even looking at.

    The worker's screen has a project switcher and a project name in the
    header. They are IN a project; the queue must mean the same thing the
    header says. Everything worker-facing scopes through here.

    Falls back to their first permitted project so a worker who has never
    switched still gets a coherent queue rather than an empty one.
    """
    proj = active_project(request, db, user)
    if proj is not None:
        return proj
    permitted = _my_projects(db, user)
    return permitted[0] if permitted else None


def _scope_to_project(q, project):
    """Restrict a MasterTitle query to the worker's current project."""
    return scope_titles(q, project)


def _my_queue(db: Session, user: User, project=None):
    """
    MasterTitle rows claimed by `user` in the project they are standing in.

    Scoped, because a worker covering two niches holding 50 movie titles and
    50 artists should see 50 in each — not 100 interleaved in one list with
    no way to tell which is which.
    """
    return (
        scope_titles(db.query(MasterTitle), project)
          .filter(MasterTitle.claimed_by_id == user.id)
          .order_by(MasterTitle.external_id.asc().nullslast(), MasterTitle.id.asc())
          .all()
    )


def _source_search_url(db: Session, title: str, content_type: Optional[str],
                       project=None) -> str:
    """
    Where this project's workers go to find source images.

    Resolves through the per-project settings cascade rather than assuming
    TMDB, because MUSIK searches Brave in-page and returns an empty string
    here — meaning "no external link, use the built-in search".

    The movie project keeps its content-type-aware TMDB behaviour via the
    legacy helper below, since /search/tv and /search/movie are genuinely
    different URLs rather than one template with a substitution.
    """
    from ..pipeline import get_setting
    try:
        template = str(get_setting(db, "source_search_url", project=project) or "")
    except Exception:
        template = ""
    if not template:
        return ""
    if "themoviedb.org" in template:
        return _tmdb_search_url(title, content_type)
    from urllib.parse import quote_plus
    return (template
            .replace("{query}", quote_plus(title or ""))
            .replace("{content_type}", quote_plus(content_type or "")))


def _tmdb_search_url(title: str, content_type: Optional[str]) -> str:
    """Branch by content_type so worker lands on the right TMDB tab."""
    q = quote(title)
    if content_type and content_type.lower() in ("tvseries", "tv", "tv_series", "series"):
        return f"https://www.themoviedb.org/search/tv?query={q}"
    if content_type and content_type.lower() == "movie":
        return f"https://www.themoviedb.org/search/movie?query={q}"
    return f"https://www.themoviedb.org/search?query={q}"


def _serialize_master(t: MasterTitle, db: Session) -> dict:
    """
    Compact dict for a title in the worker's queue and for the open title.

    Carries the project's UI declarations (`**_project_ui`) because EVERY
    title the front end renders comes through here. An earlier version added
    those fields to three other payloads and missed this one, so the open
    title fell back to defaults: "Open TMDB" on a MUSIK artist, no search
    grid, and "0 posters saved out of 3" when the target is 2.

    If a worker-facing dict describes a title, it is built here.
    """
    live = count_live_posters_for_master(db, t.id)
    return {
        **_project_ui(db, t.project_id),
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
        "created_at": fmt_local(p.created_at, "%Y-%m-%d %H:%M"),
    }


def _active_revisions_for_user(db: Session, user: User, project=None):
    """
    Open + awaiting-approval revisions on this user's posters.

    IMPORTANT: we no longer filter out soft-deleted posters here. After the
    round-11 rework, deleting a flagged poster sends the revision to
    awaiting_approval (not auto-resolved), so the worker must still see
    it — otherwise their flag panel goes blank and they lose all signal
    that admin needs to acknowledge. Each row carries a `poster_deleted`
    flag and a `worker_action` so the UI can render a placeholder card
    instead of a broken image.
    """
    import json as _json
    rows = (
        scope_titles(db.query(Revision, SavedPoster, MasterTitle), project)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              SavedPoster.user_id == user.id,
          )
          .order_by(Revision.created_at.desc())
          .all()
    )
    out = []
    for (rev, sp, mt) in rows:
        # Hydrate the related-poster details for 'similar'-type revisions.
        # Filter related to live posters only — deleted ones shouldn't take
        # up a card slot in the similar grid; admin sees them differently.
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
            "created_at": fmt_local(rev.created_at, "%Y-%m-%d %H:%M"),
            "submitted_at": fmt_local(rev.submitted_at, "%Y-%m-%d %H:%M") or None,
            "worker_note": rev.worker_note or "",
            "worker_action": rev.worker_action or "",       # "deleted" | "replaced" | "no_action" | ""
            "admin_verdict": rev.admin_verdict or "",
            "was_rejected": was_rejected,
            "poster_id": sp.id,
            "filename": sp.filename,
            "poster_deleted": sp.deleted_at is not None,
            "related": related,                       # [{poster_id, filename, size}, ...]
            "title": mt.title,
            "year": mt.year,
            "title_folder": sp.title_folder_path,
            "date": sp.original_save_date.isoformat(),
            "master_id": mt.id,
            "tmdb_search": _source_search_url(db, mt.title, mt.content_type,
                                              resolve_project(db, mt.project_id)),
            **_project_ui(db, mt.project_id),
        })
    return out


def _state_payload(db: Session, user: User, project=None) -> dict:
    today = local_today()
    queue = _my_queue(db, user, project)
    queue_dicts = [_serialize_master(t, db) for t in queue]

    locked_id = user.locked_master_id
    locked = None
    if locked_id:
        lt = db.query(MasterTitle).filter_by(id=locked_id).first()
        # A worker who switches project mid-title would otherwise see a movie
        # open on the MUSIK screen. The lock is kept — switching back restores
        # it — but it is not shown outside its own project.
        if lt is not None and project is not None:
            lt_project_id = lt.project_id or _default_project_id_cached(db)
            if lt_project_id != project.id:
                lt = None
        if lt and lt.claimed_by_id == user.id:
            locked = _serialize_master(lt, db)
            locked["tmdb_search"] = _source_search_url(
                db, lt.title, lt.content_type, resolve_project(db, lt.project_id))
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
    receipts = []
    for r in pushed:
        # Per-day breakdown for transparency. Frozen at run creation time.
        by_day_raw = r.by_day_json
        by_day = {}
        if by_day_raw:
            try:
                by_day = json.loads(by_day_raw)
            except (TypeError, ValueError):
                pass
        # Which projects this single payment covered. Shown on the receipt so
        # a worker who covers two niches can see the whole week accounted for
        # in one payment rather than wondering which half they were paid for.
        by_project = {}
        if r.by_project_json:
            try:
                by_project = json.loads(r.by_project_json)
            except (TypeError, ValueError):
                pass
        back_pay_dates = []
        if r.back_pay_dates_json:
            try:
                back_pay_dates = json.loads(r.back_pay_dates_json)
            except (TypeError, ValueError):
                pass
        receipts.append({
            "id":           r.id,
            "period_start": r.period_start.isoformat(),
            "period_end":   r.period_end.isoformat(),
            "poster_count": r.poster_count,
            "amount_kes":   r.amount_kes,
            "rate_kes":     r.rate_kes,
            "reference":    r.reference or "",
            "note":         r.note or "",
            "pushed_at":    fmt_local(r.pushed_at, "%Y-%m-%d %H:%M") or None,
            "by_day":       by_day,           # {"2026-04-30": 5, ...}
            "by_project":   by_project,       # {"Tell-A-Vision": 120, ...}
            "back_pay_dates": back_pay_dates, # ["2026-04-23", ...] subset of by_day
        })
    # Chat unread (worker viewing their own thread).
    from ..chat import unread_count
    chat_unread = unread_count(db, worker_id=user.id, viewer_id=user.id)

    # Titles this worker has submitted for completion approval but admin
    # hasn't reviewed yet. We surface them so the worker has visible
    # confirmation their DONE click was received (instead of the title
    # silently vanishing from the queue while it's complete_pending).
    pending_complete_titles = (
        scope_titles(db.query(MasterTitle), project)
          .filter(MasterTitle.status == "complete_pending",
                  MasterTitle.claimed_by_id == user.id)
          .order_by(MasterTitle.updated_at.desc().nullslast())
          .all()
    )
    pending_complete_for_worker = [
        {
            "id":       t.id,
            "title":    t.title,
            "year":     t.year,
            "comment":  t.complete_comment or "",
            "submitted_at": fmt_local(t.updated_at, "%Y-%m-%d %H:%M") or "",
        }
        for t in pending_complete_titles
    ]

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
        "revisions": _active_revisions_for_user(db, user, project),
        "receipts": receipts,
        "chat_unread": chat_unread,
        "pending_complete_titles": pending_complete_for_worker,
        "default_pull_size": user.last_pull_size or DEFAULT_PULL_SIZE,
    }


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        return RedirectResponse("/admin", status_code=302)
    state = _state_payload(db, user, _worker_project(request, db, user))
    return templates.TemplateResponse(
        request,
        "user_dashboard.html",
        {"user": user, "state": state, "active_tab": "dashboard"},
    )


@router.get("/worker")
def worker_view(user: User = Depends(require_user)):
    """
    Retired. Kept as a redirect only so old bookmarks and the browser history
    don't dead-end on a 404.

    This used to render the worker dashboard for an admin — "act as a worker"
    mode. It was removed because Peek does the same job better: it shows a
    REAL worker's live state, read-only, whereas this showed the admin's own
    empty queue and let them claim titles as themselves, quietly attributing
    work (and pay) to the admin account.
    """
    if user.role == "admin":
        return RedirectResponse("/admin/peek", status_code=302)
    return RedirectResponse("/", status_code=302)


@router.get("/api/state")
def api_state(request: Request, user: User = Depends(require_user),
              db: Session = Depends(get_db)):
    return JSONResponse(_state_payload(db, user, _worker_project(request, db, user)))


@router.get("/switch_project/{slug}")
def switch_project(
    slug: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    A worker moving between the projects they're assigned to.

    Validated against their own assignments, not just "does this project
    exist" — the slug arrives in a URL the worker controls, and a bare
    existence check would let anyone browse any niche's queue.
    """
    target = next((p for p in _my_projects(db, user) if p.slug == slug), None)
    if target is None:
        raise HTTPException(404, "No such project.")

    resp = RedirectResponse(url="/", status_code=303)
    set_project_cookie(resp, target)
    remember_project(db, user, target)
    db.commit()
    return resp


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
    request: Request,
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
    project = _worker_project(request, db, user)
    query = _scope_to_project(db.query(MasterTitle), project)

    if status in ("pending", "in_progress", "complete", "complete_pending", "skipped"):
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

# ── In-page image search (projects whose workers don't leave the site) ───────

@router.get("/api/search/{master_id}")
def api_search(
    master_id: int,
    deep: int = Query(0),
    refresh: int = Query(0),
    cache_only: int = Query(0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Image results for a title the worker is holding, from cache when possible.

    Cache is keyed on (title, worker, variant) and lives for the claim, so
    toggling between the normal and deep grids is free after the first look.
    `refresh=1` forces a new query — the escape hatch when results look stale
    or a thumbnail has expired.
    """
    from ..brave_search import BraveError, search
    from ..models import SearchCache

    t = _load_my_master(db, user, master_id)
    variant = "deep" if deep else "normal"

    if not refresh:
        cached = (
            db.query(SearchCache)
              .filter_by(master_title_id=t.id, user_id=user.id, variant=variant)
              .first()
        )
        if cached and (datetime.utcnow() - cached.created_at) < timedelta(hours=24):
            payload = json.loads(cached.payload_json)
            payload["cached"] = True
            return JSONResponse(payload)

    if cache_only:
        # Opening a title must never spend a credit. A worker who reopens a
        # title to check something they already saved would otherwise trigger
        # a fresh paid query they never asked for. Cached results still show
        # (they cost nothing); otherwise the grid says "press SEARCH".
        return JSONResponse({"ok": True, "variant": variant, "results": [],
                             "queries": [], "filtered_small": 0,
                             "cached": False, "not_searched": True})

    project = resolve_project(db, t.project_id)
    try:
        outcome = search(db, t.title, deep=bool(deep), project=project)
    except BraveError as e:
        log_activity(db, user=user, action="search_failed", target_type="master_title",
                     target_id=t.id, details={"error": str(e), "variant": variant})
        db.commit()
        return JSONResponse({"ok": False, "message": e.worker_message}, status_code=503)

    payload = {
        "ok": True,
        "variant": variant,
        "queries": outcome.queries,
        "filtered_small": outcome.filtered_small,
        "results": [r.as_dict() for r in outcome.results],
        "cached": False,
    }

    # Upsert the cache row. Deliberately not fatal — a cache write failing
    # must not cost the worker the results they are looking at.
    try:
        row = (
            db.query(SearchCache)
              .filter_by(master_title_id=t.id, user_id=user.id, variant=variant)
              .first()
        )
        stored = json.dumps({k: v for k, v in payload.items() if k != "cached"})
        if row:
            row.payload_json = stored
            row.created_at = datetime.utcnow()
        else:
            db.add(SearchCache(master_title_id=t.id, user_id=user.id,
                               variant=variant, payload_json=stored))
        db.commit()
    except Exception:
        db.rollback()

    return JSONResponse(payload)


@router.post("/api/search_save/{master_id}")
def api_search_save(
    master_id: int,
    url: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Save one image the worker picked out of the grid.

    Fetches server-side the instant it is clicked — see app/imagefetch.py for
    why that matters — converts to JPEG, and then hands off to exactly the
    same save path the paste-a-URL flow uses, so duplicate detection, the
    soft limit, folder freezing and the activity log all behave identically.
    """
    from ..imagefetch import FetchError, fetch_as_jpeg

    t = _load_my_master(db, user, master_id)
    ok, reason = _validate_image_url(url)
    if not ok:
        raise HTTPException(400, reason)

    project = resolve_project(db, t.project_id)
    soft_limit = int(project.images_per_title or SOFT_LIMIT_PER_TITLE)
    live = count_live_posters_for_master(db, t.id)
    if live >= soft_limit:
        return JSONResponse(
            {"ok": False, "reason": "soft_limit",
             "message": f"You already have {live} of {soft_limit} images for this title.",
             "current_count": live, "soft_limit": soft_limit},
            status_code=409,
        )

    today = local_today()
    _ensure_first_save_metadata(t, today)
    db.flush()

    from ..workspace_migration import project_folder_for
    project_folder = project_folder_for(project)
    folder = title_folder_for(user.username, t.original_save_date,
                              t.title_folder_path, project_folder)

    count = live + 1
    target_name = filename_for(t.title, count, ".jpg")
    target_path = folder / target_name
    while target_path.exists():
        count += 1
        target_name = filename_for(t.title, count, ".jpg")
        target_path = folder / target_name

    try:
        written, img_w, img_h = fetch_as_jpeg(url, target_path)
    except FetchError as e:
        target_path.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "message": str(e)}, status_code=422)

    sp = SavedPoster(
        master_title_id    = t.id,
        user_id            = user.id,
        username           = user.username,
        project_folder     = project_folder,
        original_save_date = t.original_save_date,
        title_folder_path  = t.title_folder_path,
        filename           = target_name,
        source_url         = url,
        file_size          = written,
        image_width        = img_w,
        image_height       = img_h,
    )
    db.add(sp)
    db.flush()
    log_activity(db, user=user, action="saved", target_type="saved_poster",
                 target_id=sp.id, details={"via": "search", "url": url})
    db.commit()

    new_live = count_live_posters_for_master(db, t.id)
    return JSONResponse({
        "ok": True,
        "poster": _serialize_poster(sp),
        "count": new_live,
        "soft_limit": soft_limit,
        "at_limit": new_live >= soft_limit,
    })


@router.post("/pull_next")
def pull_next(
    request: Request,
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

    project = _worker_project(request, db, user)
    rows = (
        _scope_to_project(db.query(MasterTitle), project)
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
    request: Request,
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

    # Scoped as well as filtered by id: the id list comes from the browser, so
    # a worker could otherwise claim any title in the database by posting ids
    # their own master browse would never have shown them.
    project = _worker_project(request, db, user)
    rows = (
        _scope_to_project(db.query(MasterTitle), project)
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
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Release back to the pool any of my claims that have NO saved posters yet.
    Things I've actually started (started_at set) stay mine.

    Scoped to the project the worker is standing in: pressing RETURN UNWORKED
    on the MUSIK screen must not hand back movie titles they can't even see
    from there.
    """
    project = _worker_project(request, db, user)
    rows = (
        _scope_to_project(db.query(MasterTitle), project)
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
        "tmdb_search": _source_search_url(db, t.title, t.content_type,
                                          resolve_project(db, t.project_id)),
        **_project_ui(db, t.project_id),
    })


@router.post("/unlock")
def unlock(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.locked_master_id is not None:
        log_activity(db, user=user, action="unlocked", target_type="master_title", target_id=user.locked_master_id)
        user.locked_master_id = None
        db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/title/{master_id}/catalog")
def title_catalog(
    master_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Read-only list of all saved posters on this title. Used by the worker
    flag-banner "VIEW ALL POSTERS" button so they can spot duplicates and
    pick a different angle when replacing.

    Authorization: the worker can see catalogs for any title they have
    saved at least one poster on, OR any title currently claimed by them.
    This covers the flagged-but-completed case where the title is no
    longer "claimed" but the worker should still see what they did.
    """
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    # Permission check — the worker must have some link to this title.
    if t.claimed_by_id != user.id:
        saved_any = (
            db.query(SavedPoster.id)
              .filter(SavedPoster.master_title_id == t.id,
                      SavedPoster.user_id == user.id,
                      SavedPoster.deleted_at.is_(None))
              .first()
        )
        if not saved_any:
            raise HTTPException(403, "You haven't worked on this title.")
    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.master_title_id == t.id,
                  SavedPoster.deleted_at.is_(None))
          .order_by(SavedPoster.id.asc())
          .all()
    )
    return JSONResponse({
        "ok": True,
        "title": t.title,
        "year": t.year,
        "content_type": t.content_type,
        "status": t.status,
        "posters": [
            {
                "id":       p.id,
                "filename": p.filename,
                "size":     p.file_size or 0,
                "width":    p.image_width or 0,
                "height":   p.image_height or 0,
                "saved_by": p.username,
                "saved_on": p.original_save_date.isoformat() if p.original_save_date else "",
                "url":      f"/file_own/{p.id}",
            }
            for p in posters
        ],
    })


@router.post("/title/{master_id}/go_to")
def go_to_title(
    master_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    "Go to Title" from a flag card — reopens a completed/skipped title
    (if needed), locks it to the current worker, and returns the same
    payload as lock_title so the UI can immediately switch to the active
    panel.

    Three states for the source title:
      - already in_progress + claimed by this worker → just lock (cheap).
      - complete/skipped + (claim was this worker OR unclaimed) → flip
        status back to in_progress, lock to this worker.
      - claimed by SOMEONE ELSE → reject, even if status is complete.

    We never auto-resolve any active revisions here — the worker is
    explicitly going there to look at/fix them. Their actions on posters
    (replace/delete) take care of revision lifecycle as today.
    """
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    # If the title is currently claimed by another worker, block.
    if t.claimed_by_id and t.claimed_by_id != user.id:
        raise HTTPException(
            409,
            f"This title is currently being worked on by {t.claimed_by_name}.",
        )

    reopened = False
    if t.status in ("complete", "skipped"):
        t.status = "in_progress"
        t.completed_at = None
        t.skip_reason  = None
        reopened = True
        # Make sure they own the claim now (in case it was an unclaimed
        # leftover or attribution was theirs but record had no live claim).
        if t.claimed_by_id is None:
            t.claimed_by_id   = user.id
            t.claimed_by_name = user.username
            t.claimed_at      = datetime.utcnow()
        log_activity(db, user=user, action="reopened", target_type="master_title",
                     target_id=t.id, details={"via": "go_to_title"})
    elif t.claimed_by_id is None:
        # Pending unclaimed — just claim it.
        t.claimed_by_id   = user.id
        t.claimed_by_name = user.username
        t.claimed_at      = datetime.utcnow()
        t.status          = "in_progress"
        log_activity(db, user=user, action="claimed", target_type="master_title",
                     target_id=t.id, details={"via": "go_to_title"})

    user.locked_master_id = t.id
    log_activity(db, user=user, action="locked", target_type="master_title",
                 target_id=t.id, details={"via": "go_to_title"})
    db.commit()
    return JSONResponse({
        "ok": True,
        "id":           t.id,
        "title":        t.title,
        "year":         t.year,
        "content_type": t.content_type,
        "description":  (t.description or "")[:600],
        "tmdb_search":  _source_search_url(db, t.title, t.content_type,
                                           resolve_project(db, t.project_id)),
        **_project_ui(db, t.project_id),
        "reopened":     reopened,
    })


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
    confirm_cross_title: int = Form(0),
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

    # ── v15: Cross-title duplicate URL warning ────────────────────────
    # Check if this exact URL was already saved today on a DIFFERENT title
    # by this worker. Common on mobile when the clipboard doesn't update.
    # Soft warning only — not a hard block.
    if not confirm_cross_title:
        today_check = local_today()
        cross_dup = (
            db.query(SavedPoster, MasterTitle)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(
                  SavedPoster.user_id == user.id,
                  SavedPoster.source_url == src_url,
                  SavedPoster.deleted_at.is_(None),
                  SavedPoster.original_save_date == today_check,
                  SavedPoster.master_title_id != t.id,
              )
              .first()
        )
        if cross_dup:
            other_poster, other_title = cross_dup
            return JSONResponse(
                {"ok": False, "reason": "cross_title_duplicate",
                 "message": (
                     f"You already used this exact image URL on "
                     f"\"{other_title.title} ({other_title.year})\". "
                     f"This often happens when the clipboard didn't update. "
                     f"Save it here anyway?"
                 ),
                 "other_title": other_title.title,
                 "other_title_id": other_title.id},
                status_code=409,
            )

    # Soft warning at >= SOFT_LIMIT_PER_TITLE.
    live = count_live_posters_for_master(db, t.id)
    # Per-project cap: movies expect 3 images, MUSIK expects 2. Resolved
    # through the settings cascade so a third niche needs no code change.
    soft_limit = SOFT_LIMIT_PER_TITLE
    try:
        from ..pipeline import get_setting, resolve_project
        _proj = resolve_project(db, t.project_id)
        soft_limit = int(_proj.images_per_title
                         or get_setting(db, "soft_limit_per_title", project=_proj))
    except Exception:
        pass
    if live >= soft_limit and not confirm_soft_limit:
        return JSONResponse(
            {"ok": False, "reason": "soft_limit",
             "message": f"This title already has {live} posters saved. Save another?",
             "current_count": live, "soft_limit": soft_limit},
            status_code=409,
        )

    today = local_today()
    _ensure_first_save_metadata(t, today)
    db.flush()  # so t.title_folder_path / t.original_save_date are visible to helpers

    # The project's folder segment, resolved once and stamped onto the poster
    # row below so saved_poster_path() never needs a join to rebuild it.
    from ..workspace_migration import project_folder_for
    from ..pipeline import resolve_project
    project_folder = project_folder_for(resolve_project(db, t.project_id))

    folder = title_folder_for(user.username, t.original_save_date,
                              t.title_folder_path, project_folder)

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
        project_folder     = project_folder,
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
        "soft_warning": new_live >= soft_limit,
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
    reason_source: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Soft-delete a saved poster file.

    Two cases:
      (a) The poster has NO active revisions. Plain deletion — soft-delete
          the row, unlink the file. No admin involvement.
      (b) The poster HAS active revisions (open or awaiting_approval), OR
          is a participant in a similar-pair revision. We do NOT auto-
          resolve those revisions anymore — instead each such revision is
          set to (or stays at) `awaiting_approval` with the worker's note,
          so admin must explicitly approve the deletion. This is a change
          from round 9 (which auto-resolved on delete). The motivation:
          a silent auto-resolve let workers accidentally bypass admin
          review of intentional deletions of flagged content.

    Response:
      {ok: true}                         — plain delete (case a)
      {ok: true, submitted_for_approval: true,
       revision_ids: [...]}              — admin approval needed (case b)
    """
    sp = _load_my_poster(db, user, poster_id)
    fs_path = saved_poster_path(sp)
    fs_path.unlink(missing_ok=True)

    sp.deleted_at = datetime.utcnow()
    sp.delete_note = note.strip() or None

    if reason_source not in ("preset", "manual"):
        reason_source = ""
    submitted_revision_ids: list[int] = []

    # Direct revisions on this poster (simple or similar-where-primary).
    revs = (
        db.query(Revision)
          .filter(
              Revision.saved_poster_id == sp.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    for r in revs:
        # Push to awaiting_approval regardless of current state. If it was
        # already awaiting_approval (e.g. they replaced then deleted), we
        # refresh the submitted_at and worker_note so admin sees the latest.
        r.status = "awaiting_approval"
        r.submitted_at = datetime.utcnow()
        r.worker_note = (note.strip() or r.worker_note or "")
        # Mark this revision as resolved-via-delete so admin UI can show
        # the right action label ("Approve deletion" vs "Approve fix").
        r.worker_action = "deleted"
        submitted_revision_ids.append(r.id)

    # Similar-pair revisions where this poster is in related_poster_ids
    # but is NOT the primary saved_poster_id. Same treatment — escalate.
    import json as _json
    sim_revs = (
        db.query(Revision)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              Revision.revision_type == "similar",
              Revision.saved_poster_id != sp.id,
          )
          .all()
    )
    for r in sim_revs:
        try:
            related = _json.loads(r.related_poster_ids or "[]")
        except Exception:
            related = []
        if sp.id in related and r.id not in submitted_revision_ids:
            r.status = "awaiting_approval"
            r.submitted_at = datetime.utcnow()
            r.worker_note = (note.strip() or r.worker_note or "")
            r.worker_action = "deleted"
            submitted_revision_ids.append(r.id)

    # `needs_revision` on the master title — recompute counting any active
    # revisions (open OR awaiting_approval) on ANY of the master's posters,
    # INCLUDING the just-deleted one. We intentionally drop the
    # `SavedPoster.deleted_at.is_(None)` filter here: now that deletes can
    # be pending review, a soft-deleted poster with an awaiting_approval
    # revision still counts as "flagged".
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

    # If ALL posters on this title are now deleted, reset the title to
    # "pending" so it returns to the pool. Also clean up the empty
    # workspace folder. This prevents ghost titles that show as
    # in_progress with zero posters.
    if mt:
        remaining = (
            db.query(SavedPoster)
              .filter_by(master_title_id=mt.id)
              .filter(SavedPoster.deleted_at.is_(None))
              .count()
        )
        if remaining == 0 and mt.status in ("in_progress", "complete_pending"):
            mt.status = "pending"
            mt.needs_revision = 0
            mt.completed_at = None
            mt.admin_note = None
            # Unclaim from the worker
            if mt.claimed_by_id:
                u = db.query(User).filter_by(id=mt.claimed_by_id).first()
                if u and u.locked_master_id == mt.id:
                    u.locked_master_id = None
            mt.claimed_by_id = None
            # Clean up empty date/title folder on disk
            import shutil
            title_dir = saved_poster_path(sp).parent
            if title_dir.is_dir():
                try:
                    # Remove the title subfolder if it's empty (or only has deleted files)
                    remaining_files = list(title_dir.iterdir())
                    if not remaining_files:
                        shutil.rmtree(title_dir, ignore_errors=True)
                except Exception:
                    pass

    log_activity(
        db, user=user, action="deleted", target_type="saved_poster", target_id=sp.id,
        details={"filename": sp.filename, "master_id": sp.master_title_id,
                 "note": note.strip() or None,
                 "reason_source": reason_source or None,
                 "submitted_revisions": submitted_revision_ids},
    )
    db.commit()

    payload = {"ok": True}
    if submitted_revision_ids:
        payload["submitted_for_approval"] = True
        payload["revision_ids"] = submitted_revision_ids
    return JSONResponse(payload)


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

    # ── These are DIFFERENT BYTES, so any post-production verdict on the old
    # ones is void. Without this, a poster that reached failed_processing and
    # was then replaced kept that status forever: the new picture sat behind a
    # judgement passed on a picture that no longer exists, and nothing in the
    # pipeline ever looked at it again.
    #
    # Only terminal states are cleared. A poster mid-flight is left alone —
    # a node is holding it, and yanking it here would hand the same item out
    # twice. Cleared to NULL rather than 'greenlit' so the replacement goes
    # through the normal approval gate instead of inheriting an approval that
    # was given to the previous image.
    if sp.pipeline_status in ("failed_processing", "failed_upload", "unusable"):
        sp.pipeline_status  = None
        sp.process_attempts = 0
        sp.process_error    = None
        sp.claimed_at       = None
        sp.claimed_by       = None

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
        r.worker_action = "replaced"
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
            r.worker_action = "replaced"
            submitted_ids.append(r.id)

    # The title's pipeline rollup is derived from its posters, so clearing one
    # above leaves it stale — the title would still read "failed" with nothing
    # under it failing.
    if mt is not None:
        from ..pipeline import recompute_title_status
        recompute_title_status(db, mt)

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
    reason_source: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Worker clicks DONE on a title.

    Routing depends on whether there are pending changes:

      No active revisions on this title → go straight to 'complete'.
        Normal first-time completion.

      Active revisions exist (open OR awaiting_approval) → route to
        'complete_pending'. All open revisions on the title are escalated
        to awaiting_approval (treated as if the worker actioned them by
        clicking DONE). Admin must explicitly approve the completion via
        the PENDING COMPLETIONS section of /admin/revisions.

    The old force=1 bypass is gone. There is no "mark complete anyway"
    escape hatch — once admin has flagged or once a deletion is pending,
    admin always reviews the final state.

    `reason_source` is a hint about HOW the comment was produced — 'preset'
    if a preset button was clicked, 'manual' if typed. Logged for activity
    log differentiation.
    """
    t = _load_my_master(db, user, master_id)

    # ── v15: Guard — cannot complete a title with zero live posters ────
    # A title is "complete" only when it has at least one poster. If all
    # posters were deleted (e.g. admin flagged the only poster, worker
    # deleted it, admin approved the deletion), completing with 0 posters
    # makes no sense. The worker should skip instead.
    live_count = count_live_posters_for_master(db, t.id)
    if live_count == 0:
        raise HTTPException(
            400,
            "Cannot mark this title as done — it has no posters. "
            "Add at least one poster, or skip the title instead."
        )

    # IMPORTANT: do NOT filter by SavedPoster.deleted_at here. A poster
    # deleted while it had a flag now carries an awaiting_approval revision
    # that admin still needs to see — that absolutely counts toward
    # "should this be complete_pending?".
    active_revs = (
        db.query(Revision)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .filter(
              SavedPoster.master_title_id == t.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )

    if reason_source not in ("preset", "manual"):
        reason_source = ""

    if active_revs:
        # Hold the title for admin review. Escalate any still-open revisions
        # to awaiting_approval — clicking DONE counts as "I'm done acting on
        # these, please review". Already-awaiting revisions are left as-is.
        escalated_ids: list[int] = []
        for r in active_revs:
            if r.status == "open":
                r.status = "awaiting_approval"
                r.submitted_at = datetime.utcnow()
                r.worker_note = comment.strip() or r.worker_note or "(none)"
                # If the underlying poster has been soft-deleted but no
                # explicit worker_action was set, treat this as a passive
                # "no-op" submission (worker chose to leave the flag as-is
                # and just submit the title). Admin can decide.
                if r.worker_action is None:
                    sp = db.query(SavedPoster).filter_by(id=r.saved_poster_id).first()
                    r.worker_action = "deleted" if (sp and sp.deleted_at) else "no_action"
                escalated_ids.append(r.id)

        t.status = "complete_pending"
        # We DO NOT set completed_at here — that's the final-approval
        # timestamp. Use updated_at for "when was it submitted".
        t.updated_at = datetime.utcnow()
        t.complete_comment = comment.strip() or None
        # Don't clear admin_note here — admin may have left notes during
        # the flag stage; preserve them for context until approval.
        if user.locked_master_id == master_id:
            user.locked_master_id = None
        log_activity(
            db, user=user, action="submitted_for_completion",
            target_type="master_title", target_id=t.id,
            details={"comment": comment.strip() or None,
                     "reason_source": reason_source or None,
                     "escalated_revisions": escalated_ids},
        )
        db.commit()
        return JSONResponse({
            "ok": True,
            "pending_approval": True,
            "message": "Submitted for admin approval — title will show 'awaiting approval' until reviewed.",
        })

    # No active revisions — straight to complete.
    t.status = "complete"
    t.completed_at = datetime.utcnow()
    t.complete_comment = comment.strip() or None
    t.admin_note = None  # auto-clear admin's send-back note when title is finished
    if user.locked_master_id == master_id:
        user.locked_master_id = None
    log_activity(
        db, user=user, action="completed", target_type="master_title", target_id=t.id,
        details={"comment": comment.strip() or None,
                 "reason_source": reason_source or None},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/title/{master_id}/skip")
def title_skip(
    master_id: int,
    reason: str = Form(""),
    reason_source: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = _load_my_master(db, user, master_id)
    t.status = "skipped"
    t.skip_reason = reason.strip() or None
    t.admin_note = None  # clear admin's prior send-back note
    if user.locked_master_id == master_id:
        user.locked_master_id = None
    if reason_source not in ("preset", "manual"):
        reason_source = ""
    # Resolve any active revisions on this title — skip overrides flags.
    # Admin can review in the Skipped panel and send it back if needed.
    active_revs = (
        db.query(Revision)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .filter(
              SavedPoster.master_title_id == t.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    for r in active_revs:
        r.status = "resolved"
        r.resolved_by = user.username
        r.resolved_at = datetime.utcnow()
        r.admin_verdict = "auto-resolved: title skipped"
    t.needs_revision = 0

    # ── v15: Delete all live posters when skipping ──────────────────────
    # If the worker saved posters under this title and then clicks Skip
    # instead of Confirm/Done, those posters should not exist (they'd
    # otherwise still count towards pay, which makes no sense for a
    # skipped title). Soft-delete every live poster + unlink from disk.
    live_posters = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.master_title_id == t.id,
              SavedPoster.deleted_at.is_(None),
          )
          .all()
    )
    deleted_poster_ids: list[int] = []
    for sp in live_posters:
        fs = saved_poster_path(sp)
        fs.unlink(missing_ok=True)
        sp.deleted_at = datetime.utcnow()
        sp.delete_note = "auto-deleted: title skipped"
        deleted_poster_ids.append(sp.id)

    # Clean up the now-empty title folder on disk.
    if deleted_poster_ids and live_posters:
        import shutil
        title_dir = saved_poster_path(live_posters[0]).parent
        if title_dir.is_dir():
            try:
                remaining_files = list(title_dir.iterdir())
                if not remaining_files:
                    shutil.rmtree(title_dir, ignore_errors=True)
            except Exception:
                pass

    log_activity(
        db, user=user, action="skipped", target_type="master_title", target_id=t.id,
        details={"reason": reason.strip() or None,
                 "reason_source": reason_source or None,
                 "resolved_flags": len(active_revs),
                 "deleted_posters": deleted_poster_ids},
    )
    db.commit()
    return JSONResponse({"ok": True, "deleted_posters": len(deleted_poster_ids)})


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


@router.post("/api/receipts/{run_id}/not_received")
def receipt_not_received(
    run_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    v15: Worker indicates they have NOT received a pushed payment.
    Sets not_received_at so admin can follow up. The receipt disappears
    from the worker's dashboard (same as ack), but admin sees the
    'not_received' status instead of 'acknowledged'.
    """
    from ..models import PaymentRun
    run = db.query(PaymentRun).filter_by(id=run_id).first()
    if run is None or run.worker_id != user.id:
        raise HTTPException(404, "Receipt not found.")
    if run.pushed_at is None:
        raise HTTPException(400, "Receipt was never pushed to you.")
    if run.ack_at is not None:
        raise HTTPException(400, "Already acknowledged.")
    if run.not_received_at is None:
        run.not_received_at = datetime.utcnow()
        log_activity(db, user=user, action="receipt_not_received", target_type="payment_run", target_id=run.id)
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
        db.query(SavedPoster.id, SavedPoster.original_save_date,
                 MasterTitle.project_id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(
              SavedPoster.user_id == user.id,
              SavedPoster.deleted_at.is_(None),
          )
          .all()
    )
    if not rows:
        return JSONResponse({"ok": True, "days": [], "rate_kes": str(rate_dec)})

    all_ids = {pid for pid, _d, _p in rows}

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
    from ..pipeline import _default_project_id
    default_pid = _default_project_id(db)
    names = {p.id: p.name for p in db.query(Project).all()}

    by_day: dict[str, dict] = {}
    for pid, d, proj_id in rows:
        key = d.isoformat()
        b = by_day.setdefault(key, {"date": key, "paid": 0, "eligible": 0,
                                    "pending": 0, "by_project": {}})
        if pid in paid_ids:
            b["paid"] += 1
        elif pid in blocked:
            b["pending"] += 1
        else:
            b["eligible"] += 1
        name = names.get(proj_id or default_pid, "")
        b["by_project"][name] = b["by_project"].get(name, 0) + 1

    days = sorted(by_day.values(), key=lambda r: r["date"], reverse=True)
    # Compute amounts.
    for d in days:
        # Eligible amount = eligible_count × rate. Paid amount = paid_count × rate
        # (assumes rate didn't change — informational only; the actual paid
        # amount is on the PaymentRun row, which we surface elsewhere).
        d["eligible_amount_kes"] = str(rate_dec * d["eligible"])
        d["paid_amount_kes"]     = str(rate_dec * d["paid"])
        # Rendered only when a day actually spans more than one project.
        # On a single-project day it would restate the total in words.
        split = d.pop("by_project", {})
        d["project_split"] = (
            " · ".join(f"{name} {n}" for name, n in
                       sorted(split.items(), key=lambda kv: -kv[1]))
            if len(split) > 1 else ""
        )

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
    # Project comes along for the ride. A worker covering two niches sees
    # both in one day's list, and "Adele" next to "Pulp Fiction" with nothing
    # distinguishing them is confusing in exactly the moment they are
    # checking their own pay.
    rows = (
        db.query(SavedPoster.id, SavedPoster.master_title_id,
                 MasterTitle.title, MasterTitle.year, MasterTitle.project_id)
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
    from ..pipeline import _default_project_id
    default_pid = _default_project_id(db)
    names = {p.id: p.name for p in db.query(Project).all()}

    by_title: dict[int, dict] = {}
    for pid, mid, title, year, proj_id in rows:
        b = by_title.setdefault(mid, {
            "master_id": mid, "title": title, "year": year,
            # NULL project_id means the DEFAULT project, not "no project" —
            # the 101,605 imported rows are all NULL.
            "project": names.get(proj_id or default_pid, ""),
            "paid": 0, "eligible": 0, "pending": 0, "total": 0,
        })
        b["total"] += 1
        if pid in paid_ids:    b["paid"] += 1
        elif pid in blocked:    b["pending"] += 1
        else:                   b["eligible"] += 1

    titles = sorted(by_title.values(), key=lambda r: r["title"].lower())
    return JSONResponse({"ok": True, "date": d, "titles": titles})


# ── Worker performance stats ────────────────────────────────────────────────

@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Worker's own stats page — chart + totals + records + pace deltas."""
    if user.role == "admin":
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "user_stats.html",
        {"user": user, "active_tab": "stats"},
    )


@router.get("/api/stats/me")
def api_stats_me(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """JSON stats for the current worker. Drives the /stats page."""
    from ..stats import compute_worker_stats
    data = compute_worker_stats(db, worker_id=user.id, is_admin_view=False)
    return JSONResponse(data)
