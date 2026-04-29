"""
Admin-facing routes. Mounted under /admin.

Endpoints:
    GET  /admin                           → dashboard (counts + per-user stats)
    GET  /admin/users                     → user management page
    POST /admin/users/create              → create user
    POST /admin/users/{id}/toggle         → toggle is_active
    POST /admin/users/{id}/reset_password → set new password
    POST /admin/users/{id}/release_queue  → release ALL claims (started or not) back to pending
    GET  /admin/master                    → paginated master sheet view
    GET  /admin/api/master                → JSON paginated master (same paging used by user/master)
    POST /admin/master/{id}/status        → set status for one row (admin override)
    POST /admin/master/bulk_status        → bulk status change for filtered/selected ids
    POST /admin/master/clear              → wipe all master rows
    POST /admin/master/upload             → start background import
    GET  /admin/import/{id}               → poll import status
    GET  /admin/browse                    → image browser shell
    GET  /admin/api/browse                → JSON of all live posters for (worker, date)
    GET  /admin/file/{poster_id}          → serve a SavedPoster image
    POST /admin/poster/{id}/flag          → flag a SavedPoster
    POST /admin/poster/{id}/unflag        → resolve all open flags on a poster
    GET  /admin/revisions                 → revisions list page
    GET  /admin/audit                     → activity log page
    GET  /admin/api/audit                 → activity log JSON
    GET  /admin/api/tree                  → workspace fs tree
    POST /admin/zip/start                 → start background zip job
    GET  /admin/zip/status/{job_id}       → poll zip job
    GET  /admin/zip/download/{job_id}     → download finished zip
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import threading
import time
import zipfile
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..audit import log as log_activity
from ..auth import hash_password, require_admin
from ..config import MASTER_PAGE_SIZE
from ..envs import current_workspace_dir
from ..db import SessionLocal, get_db
from ..models import (
    ActivityLog, ImportJob, MasterTitle, Revision, SavedPoster, User,
)
from ..templating import templates
from ..utils import (
    count_user_saves_for_date, count_user_saves_for_week,
    list_date_folders, list_users_with_workspaces, safe_under_workspace,
    saved_poster_folder, saved_poster_path,
)


router = APIRouter(prefix="/admin")


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    today = date_type.today()
    users = db.query(User).filter_by(role="worker").order_by(User.username.asc()).all()
    user_stats = []
    for w in users:
        claims = (
            db.query(MasterTitle)
              .filter(MasterTitle.claimed_by_id == w.id, MasterTitle.status == "in_progress")
              .count()
        )
        user_stats.append({
            "id": w.id,
            "username": w.username,
            "today":  count_user_saves_for_date(db, w.username, today),
            "week":   count_user_saves_for_week(db, w.username, today),
            "claims": claims,
            "is_active": bool(w.is_active),
        })
    open_revs = db.query(Revision).filter_by(status="open").count()
    awaiting_revs = db.query(Revision).filter_by(status="awaiting_approval").count()

    # Master totals
    pending_master   = db.query(MasterTitle).filter_by(status="pending").count()
    in_progress      = db.query(MasterTitle).filter_by(status="in_progress").count()
    complete_master  = db.query(MasterTitle).filter_by(status="complete").count()
    skipped_master   = db.query(MasterTitle).filter_by(status="skipped").count()
    revision_master  = db.query(MasterTitle).filter(MasterTitle.needs_revision == 1).count()
    total_master     = db.query(MasterTitle).count()

    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {"user": admin, "admin": admin, "today": today.isoformat(),
            "users": user_stats, "open_revisions": open_revs,
            "awaiting_revisions": awaiting_revs,
            "awaiting_revisions": awaiting_revs,
            "master_pending": pending_master, "master_in_progress": in_progress,
            "master_complete": complete_master, "master_skipped": skipped_master,
            "master_needs_revision": revision_master, "master_total": total_master,
            "active_tab": "dashboard",
        },
    )


# ── User management ─────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """
    Users live in the LIVE env's users table (it's the global auth source).
    Claim counts are per-env though — they show how many in-progress titles
    each user holds *in that user's own env*. Cheap because only workers have
    claims, and we already know each worker's env.
    """
    from ..auth import _live_users_session
    from ..envs import LIVE_ENV, list_test_envs, current_session_factory

    live_db = _live_users_session()
    try:
        users = live_db.query(User).order_by(User.role.desc(), User.username.asc()).all()

        # Build a per-env claim-count map by inspecting each env's master_titles table.
        # Each env's users table mirrors the live one (we re-import users on first
        # session to that env), so user IDs match.
        envs_seen = sorted({u.env for u in users})
        claim_counts: dict[int, int] = {}
        for env_name in envs_seen:
            from ..envs import _build_engine, _engines, _sessions, _engines_lock
            if env_name not in _sessions:
                with _engines_lock:
                    if env_name not in _sessions:
                        _build_engine(env_name)
            EnvSession = _sessions[env_name]
            sess = EnvSession()
            try:
                rows = (
                    sess.query(MasterTitle.claimed_by_id, func.count(MasterTitle.id))
                        .filter(MasterTitle.claimed_by_id.isnot(None),
                                MasterTitle.status == "in_progress")
                        .group_by(MasterTitle.claimed_by_id)
                        .all()
                )
            finally:
                sess.close()
            for uid, n in rows:
                claim_counts[uid] = claim_counts.get(uid, 0) + n
    finally:
        live_db.close()

    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"user": admin, "admin": admin, "users": users,
         "claim_counts": claim_counts,
         "live_env": LIVE_ENV,
         "test_envs": list_test_envs(),
         "active_tab": "users"},
    )


@router.post("/users/create")
def create_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("worker"),
    env: str = Form("live"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    User accounts always live in the live env's users table (it's the system-
    wide auth source — see auth._live_users_session). The `env` form field
    decides which env the new user OPERATES in: workers are pinned to it on
    every request; admins can still switch around freely.
    """
    from ..auth import _live_users_session
    from ..envs import LIVE_ENV, test_env_exists

    username = username.strip()
    env = env.strip() or LIVE_ENV
    if not re.match(r"^[A-Za-z0-9_.-]{2,64}$", username):
        raise HTTPException(400, "Username must be 2–64 chars, alnum / _ . -")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    if role not in ("admin", "worker"):
        raise HTTPException(400, "Role must be admin or worker.")
    if env != LIVE_ENV and not test_env_exists(env):
        raise HTTPException(400, f"Env {env!r} doesn't exist. Create it first.")

    live_db = _live_users_session()
    try:
        if live_db.query(User).filter_by(username=username).first():
            raise HTTPException(409, "Username already exists (usernames are global across all environments).")
        u = User(
            username=username,
            password_hash=hash_password(password),
            role=role,
            env=env,
        )
        live_db.add(u)
        live_db.flush()
        # Activity log goes into the *current* env's audit (where the admin was when they did this).
        log_activity(db, user=admin, action="user_created", target_type="user", target_id=u.id,
                     details={"username": username, "role": role, "env": env})
        live_db.commit()
        db.commit()
    finally:
        live_db.close()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/toggle")
def toggle_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    from ..auth import _live_users_session
    live_db = _live_users_session()
    try:
        u = live_db.query(User).filter_by(id=user_id).first()
        if not u:
            raise HTTPException(404, "User not found")
        if u.id == admin.id:
            raise HTTPException(400, "Cannot disable your own account.")
        u.is_active = 0 if u.is_active else 1
        log_activity(db, user=admin, action="user_toggled", target_type="user", target_id=u.id,
                     details={"is_active": bool(u.is_active)})
        live_db.commit()
        db.commit()
    finally:
        live_db.close()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/reset_password")
def reset_password(
    user_id: int,
    new_password: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..auth import _live_users_session
    if len(new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    live_db = _live_users_session()
    try:
        u = live_db.query(User).filter_by(id=user_id).first()
        if not u:
            raise HTTPException(404, "User not found")
        u.password_hash = hash_password(new_password)
        log_activity(db, user=admin, action="password_reset", target_type="user", target_id=u.id)
        live_db.commit()
        db.commit()
    finally:
        live_db.close()
    return RedirectResponse("/admin/users", status_code=302)


@router.post("/users/{user_id}/release_queue")
def release_user_queue(
    user_id: int,
    keep_started: int = Form(1),  # default: only release un-started
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin override: release a user's claimed-but-unworked rows back to the pool.
    keep_started=1 (default) protects rows where work has begun (started_at set).
    keep_started=0 releases EVERYTHING back to pending, including started rows
    (their saved posters remain on disk; the title returns to the pool for someone else).

    Operates on the *user's* env, not the admin's — so an admin in 'live' can
    release a worker that's pinned to a test env without first having to switch.
    """
    from ..auth import _live_users_session
    from ..envs import _build_engine, _engines, _sessions, _engines_lock

    # Look up the user from the live users table (global auth source).
    live_db = _live_users_session()
    try:
        u = live_db.query(User).filter_by(id=user_id).first()
        if not u:
            raise HTTPException(404, "User not found")
        target_env = u.env
    finally:
        live_db.close()

    # Open a session against the user's env.
    if target_env not in _sessions:
        with _engines_lock:
            if target_env not in _sessions:
                _build_engine(target_env)
    EnvSession = _sessions[target_env]
    sess = EnvSession()
    try:
        q = sess.query(MasterTitle).filter(
            MasterTitle.claimed_by_id == user_id,
            MasterTitle.status == "in_progress",
        )
        if keep_started:
            q = q.filter(MasterTitle.started_at.is_(None))
        rows = q.all()
        now = datetime.utcnow()
        released_ids = []
        for r in rows:
            r.claimed_by_id = None
            r.claimed_by_name = None
            r.claimed_at = None
            r.status = "pending"
            r.updated_at = now
            released_ids.append(r.id)
        # Clear the user's lock if it pointed to a released row.
        # The user row only exists in env's users table if they've touched
        # that env before (require_user merges them in on first request).
        u_env = sess.query(User).filter_by(id=user_id).first()
        if u_env and u_env.locked_master_id in released_ids:
            u_env.locked_master_id = None
        sess.commit()
    finally:
        sess.close()

    # Audit-log row goes into admin's current env (where they hit the button).
    log_activity(
        db, user=admin, action="released", target_type="user", target_id=user_id,
        details={"count": len(released_ids), "keep_started": bool(keep_started),
                 "target_env": target_env},
    )
    db.commit()
    return JSONResponse({"ok": True, "released": len(released_ids), "env": target_env})


# ── Master sheet ─────────────────────────────────────────────────────────────

@router.get("/master", response_class=HTMLResponse)
def master_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(MASTER_PAGE_SIZE, ge=10, le=500),
    q: str = Query(""),
    status: str = Query(""),
    content_type: str = Query(""),
    needs_revision: int = Query(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Initial render of the master page; the JS app pages further via /admin/api/master."""
    return templates.TemplateResponse(
        request,
        "admin_master.html",
        {"user": admin, "admin": admin,
            "page": page, "page_size": page_size, "q": q, "status": status,
            "content_type": content_type, "needs_revision": needs_revision,
            "active_tab": "master",
        },
    )


def _master_query(db: Session, q: str, status: str, content_type: str, needs_revision: int):
    query = db.query(MasterTitle)
    if status in ("pending", "in_progress", "complete", "skipped"):
        query = query.filter(MasterTitle.status == status)
    if content_type:
        query = query.filter(MasterTitle.content_type == content_type)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(MasterTitle.title.ilike(like))
    if needs_revision:
        query = query.filter(MasterTitle.needs_revision == 1)
    return query


@router.get("/api/master")
def api_master(
    page: int = Query(1, ge=1),
    page_size: int = Query(MASTER_PAGE_SIZE, ge=10, le=500),
    q: str = Query(""),
    status: str = Query(""),
    content_type: str = Query(""),
    needs_revision: int = Query(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = _master_query(db, q, status, content_type, needs_revision)
    total = query.count()
    rows = (
        query
        .order_by(MasterTitle.external_id.asc().nullslast(), MasterTitle.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return JSONResponse({
        "page": page, "page_size": page_size, "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": [
            {
                "id": r.id, "external_id": r.external_id,
                "title": r.title, "year": r.year, "content_type": r.content_type,
                "votes": r.votes, "rating": r.rating,
                "status": r.status, "needs_revision": bool(r.needs_revision),
                "claimed_by": r.claimed_by_name,
                "skip_reason": r.skip_reason or "",
                "started": t_started_str(r),
                "description": (r.description or "")[:200],
            }
            for r in rows
        ],
    })


def t_started_str(r: MasterTitle) -> str:
    return r.original_save_date.isoformat() if r.original_save_date else ""


@router.post("/master/{title_id}/status")
def master_set_status(
    title_id: int,
    status: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if status not in ("pending", "in_progress", "complete", "skipped"):
        raise HTTPException(400, "Bad status.")
    t = db.query(MasterTitle).filter_by(id=title_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    old = t.status
    t.status = status
    if status == "complete":
        t.completed_at = datetime.utcnow()
    elif status == "pending":
        t.claimed_by_id = None
        t.claimed_by_name = None
        t.claimed_at = None
    log_activity(
        db, user=admin, action="status_changed", target_type="master_title", target_id=t.id,
        details={"from": old, "to": status, "by_admin": True},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/master/bulk_status")
def master_bulk_status(
    ids: str = Form(...),
    status: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if status not in ("pending", "complete", "skipped"):
        raise HTTPException(400, "Bad status for bulk operation.")
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    except ValueError:
        raise HTTPException(400, "Bad ids.")
    if not id_list:
        raise HTTPException(400, "No ids supplied.")

    rows = db.query(MasterTitle).filter(MasterTitle.id.in_(id_list)).all()
    now = datetime.utcnow()
    for r in rows:
        r.status = status
        r.updated_at = now
        if status == "pending":
            r.claimed_by_id = None
            r.claimed_by_name = None
            r.claimed_at = None
        elif status == "complete":
            r.completed_at = now
    log_activity(
        db, user=admin, action="bulk_status", target_type="bulk", details={
            "count": len(rows), "ids": [r.id for r in rows], "status": status,
        },
    )
    db.commit()
    return JSONResponse({"ok": True, "updated": len(rows)})


@router.post("/master/clear")
def master_clear(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Wipe master entirely. Saved posters (if any) keep their data; their master_title_id
    becomes a dangling reference, but SavedPoster has the immutable folder so files are still locatable."""
    n = db.query(MasterTitle).count()
    db.query(MasterTitle).delete()
    log_activity(db, user=admin, action="bulk_status", target_type="bulk",
                 details={"deleted_master_rows": n})
    db.commit()
    return RedirectResponse("/admin/master", status_code=302)


# ── Background master import ────────────────────────────────────────────────

IMPORT_LOCK = threading.Lock()


def _import_worker(job_id: int, raw_bytes: bytes, file_ext: str, replace: bool, started_by: str):
    """Runs in a background thread. Owns its own DB session."""
    db = SessionLocal()
    try:
        job = db.query(ImportJob).filter_by(id=job_id).first()
        if not job:
            return
        job.state = "running"
        db.commit()

        # Parse rows
        rows = []
        if file_ext in (".xlsx", ".xlsm"):
            try:
                from openpyxl import load_workbook
                wb = load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
                ws = wb.active
                header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
                headers = [str(h).strip().lower() if h is not None else "" for h in header_row]
                for r in ws.iter_rows(min_row=2, values_only=True):
                    rows.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
            except Exception as e:
                job.state = "error"
                job.error = f"Excel read failed: {e}"
                job.finished_at = datetime.utcnow()
                db.commit()
                return
        else:
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            for r in reader:
                rows.append({(k or "").strip().lower(): v for k, v in r.items()})

        job.total_rows = len(rows)
        db.commit()

        if replace:
            db.query(MasterTitle).delete()
            db.commit()

        # Insert in batches for speed
        batch = []
        BATCH_SIZE = 1000
        for r in rows:
            title = (str(r.get("title") or "")).strip()
            if not title:
                continue
            # external_id: try the literal "0" column (which is named "0" in our CSV header), then "num"
            ext = r.get("0") or r.get("num") or r.get("external_id")
            try:
                ext_id = int(str(ext).strip()) if ext not in (None, "") else None
            except (ValueError, TypeError):
                ext_id = None
            year_raw = r.get("releaseyear") or r.get("release_year") or r.get("year") or ""
            year_str = str(year_raw).strip() if year_raw not in (None, "") else "N/A"
            m = re.search(r"\d{4}", year_str)
            year_str = m.group() if m else "N/A"
            try:
                votes = int(str(r.get("votes")).strip()) if r.get("votes") not in (None, "") else None
            except (ValueError, TypeError):
                votes = None
            try:
                rating = float(str(r.get("rating")).strip()) if r.get("rating") not in (None, "") else None
            except (ValueError, TypeError):
                rating = None
            content_type_raw = r.get("contenttype") or r.get("content_type") or None
            content_type = str(content_type_raw).strip() if content_type_raw else None
            description = r.get("description")
            description = str(description).strip() if description not in (None, "") else None

            mt = MasterTitle(
                external_id=ext_id, title=title, year=year_str,
                content_type=content_type, votes=votes, rating=rating,
                description=description, status="pending",
            )
            batch.append(mt)
            if len(batch) >= BATCH_SIZE:
                db.bulk_save_objects(batch)
                job.done_rows += len(batch)
                db.commit()
                batch = []

        if batch:
            db.bulk_save_objects(batch)
            job.done_rows += len(batch)
            db.commit()

        job.state = "done"
        job.finished_at = datetime.utcnow()
        db.commit()

        # Audit at the end (reuse the existing session from this worker thread)
        log_activity(
            db, user=None, action="imported", target_type="import_job", target_id=job.id,
            details={"by": started_by, "rows": job.done_rows, "replaced": bool(replace)},
            commit=True,
        )
    except Exception as e:
        try:
            job = db.query(ImportJob).filter_by(id=job_id).first()
            if job:
                job.state = "error"
                job.error = str(e)
                job.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/master/upload")
async def master_upload(
    file: UploadFile = File(...),
    replace: int = Form(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")
    name = (file.filename or "").lower()
    ext = ".xlsx" if name.endswith(".xlsx") or name.endswith(".xlsm") else ".csv"

    job = ImportJob(
        started_by=admin.username, state="pending",
        total_rows=0, done_rows=0, replaced=int(bool(replace)),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    threading.Thread(
        target=_import_worker,
        args=(job.id, raw, ext, bool(replace), admin.username),
        daemon=True,
    ).start()

    return JSONResponse({"ok": True, "job_id": job.id})


@router.get("/import/{job_id}")
def import_status(job_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.query(ImportJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(404, "Unknown job.")
    return JSONResponse({
        "id": job.id, "state": job.state, "total": job.total_rows,
        "done": job.done_rows, "error": job.error or "",
        "replaced": bool(job.replaced),
        "started_by": job.started_by,
    })


# ── Image browser ────────────────────────────────────────────────────────────

@router.get("/browse", response_class=HTMLResponse)
def browse_page(
    request: Request,
    worker: Optional[str] = None,
    date: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workers = list_users_with_workspaces()
    if not workers:
        users = db.query(User).filter_by(role="worker").order_by(User.username.asc()).all()
        workers = [u.username for u in users]
    selected_worker = worker or (workers[0] if workers else "")
    dates = list_date_folders(selected_worker) if selected_worker else []
    today_iso = date_type.today().isoformat()
    if today_iso not in dates and selected_worker:
        dates = [today_iso] + dates
    selected_date = date or (today_iso if today_iso in dates else (dates[0] if dates else today_iso))

    return templates.TemplateResponse(
        request,
        "admin_image_browser.html",
        {"user": admin, "admin": admin, "workers": workers, "dates": dates,
            "selected_worker": selected_worker, "selected_date": selected_date,
            "active_tab": "browse",
        },
    )


@router.get("/api/browse")
def api_browse(
    worker: str,
    date: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Group all live SavedPosters for a (user, original_save_date) pair by master title.
    Each title returns its full poster set, with revision info per poster.
    """
    try:
        d = date_type.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Bad date.")

    rows = (
        db.query(SavedPoster, MasterTitle, Revision)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .outerjoin(
              Revision,
              and_(
                  Revision.saved_poster_id == SavedPoster.id,
                  Revision.status.in_(("open", "awaiting_approval")),
              ),
          )
          .filter(
              SavedPoster.username == worker,
              SavedPoster.original_save_date == d,
              SavedPoster.deleted_at.is_(None),
          )
          .order_by(SavedPoster.title_folder_path.asc(), SavedPoster.filename.asc())
          .all()
    )

    # Group by master title (preserve folder-name ordering)
    from collections import OrderedDict
    titles = OrderedDict()
    for sp, mt, rev in rows:
        key = mt.id if mt else None
        if key not in titles:
            titles[key] = {
                "master_id": key,
                "title": mt.title if mt else "(unknown)",
                "year":  mt.year  if mt else "",
                "title_folder": sp.title_folder_path,
                "needs_revision": bool(mt.needs_revision) if mt else False,
                "posters": [],
            }
        titles[key]["posters"].append({
            "poster_id": sp.id,
            "filename": sp.filename,
            "title_folder": sp.title_folder_path,
            "size": sp.file_size,
            "low_quality_url": bool(sp.low_quality_url),
            "image_width":  sp.image_width,
            "image_height": sp.image_height,
            "flagged": rev is not None,
            "revision_id": rev.id if rev else None,
            "revision_status": rev.status if rev else None,
            "revision_type":   rev.revision_type if rev else None,
            "comment": rev.comment if rev else "",
            "worker_note": rev.worker_note if rev else "",
        })

    return JSONResponse({
        "worker": worker, "date": date,
        "title_count": len(titles),
        "poster_count": sum(len(t["posters"]) for t in titles.values()),
        "titles": list(titles.values()),
    })


@router.get("/file/{poster_id}")
def serve_poster_file(
    poster_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sp = db.query(SavedPoster).filter_by(id=poster_id).first()
    if not sp or sp.deleted_at is not None:
        raise HTTPException(404, "File not found.")
    p = saved_poster_path(sp)
    if not p.is_file():
        raise HTTPException(404, "File missing on disk.")
    return FileResponse(p)


# ── Revisions ────────────────────────────────────────────────────────────────

@router.post("/poster/{poster_id}/flag")
def flag_poster(
    poster_id: int,
    comment: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sp = db.query(SavedPoster).filter_by(id=poster_id).first()
    if not sp or sp.deleted_at is not None:
        raise HTTPException(404, "Poster not found.")

    # If there's already an active revision (open OR awaiting_approval), update it
    # back to 'open' with the new comment instead of stacking another row.
    existing = (
        db.query(Revision)
          .filter(
              Revision.saved_poster_id == sp.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .first()
    )
    if existing:
        existing.comment       = comment.strip() or None
        existing.flagged_by    = admin.username
        existing.status        = "open"
        existing.submitted_at  = None
        existing.worker_note   = None
        existing.admin_verdict = None
        log_activity(db, user=admin, action="flagged", target_type="revision", target_id=existing.id,
                     details={"updated": True, "poster_id": sp.id})
        db.commit()
        return JSONResponse({"ok": True, "id": existing.id, "updated": True})

    mt = db.query(MasterTitle).filter_by(id=sp.master_title_id).first()
    if mt:
        mt.needs_revision = 1

    rev = Revision(
        saved_poster_id=sp.id,
        comment=comment.strip() or None,
        flagged_by=admin.username,
        status="open",
    )
    db.add(rev)
    db.flush()
    log_activity(db, user=admin, action="flagged", target_type="revision", target_id=rev.id,
                 details={"poster_id": sp.id, "filename": sp.filename})
    db.commit()
    return JSONResponse({"ok": True, "id": rev.id, "updated": False})


@router.post("/poster/{poster_id}/unflag")
def unflag_poster(
    poster_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sp = db.query(SavedPoster).filter_by(id=poster_id).first()
    if not sp:
        raise HTTPException(404, "Poster not found.")
    active = (
        db.query(Revision)
          .filter(
              Revision.saved_poster_id == sp.id,
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    cleared = 0
    for r in active:
        db.delete(r)
        cleared += 1
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
    log_activity(db, user=admin, action="unflagged", target_type="saved_poster", target_id=sp.id,
                 details={"cleared": cleared})
    db.commit()
    return JSONResponse({"ok": True, "cleared": cleared})


# ── Mark posters as too similar (special revision type) ─────────────────────

@router.post("/posters/mark_similar")
def mark_similar(
    poster_ids: str = Form(...),       # comma-separated list
    comment: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin selects 2+ posters in the gallery and clicks "These are too similar".
    Creates ONE Revision of type='similar' linking all the selected posters.
    The worker sees them as a group and picks one to redo (REPLACE / DELETE).
    """
    import json as _json
    try:
        ids = [int(x) for x in poster_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "Bad poster_ids — must be a comma-separated list of integers.")
    ids = list(dict.fromkeys(ids))  # de-dup, preserve order
    if len(ids) < 2:
        raise HTTPException(400, "Pick at least two posters to mark as similar.")

    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.id.in_(ids), SavedPoster.deleted_at.is_(None))
          .all()
    )
    if len(posters) < 2:
        raise HTTPException(404, "Some selected posters no longer exist.")

    # All must belong to the same master title — comparing across titles is meaningless.
    master_ids = {p.master_title_id for p in posters}
    if len(master_ids) != 1:
        raise HTTPException(400, "All selected posters must belong to the same title.")
    # All must belong to the same user — the revision is for them to act on.
    user_ids = {p.user_id for p in posters}
    if len(user_ids) != 1:
        raise HTTPException(400, "All selected posters must belong to the same user.")

    # Use the first as the primary saved_poster_id; keep all in related_poster_ids
    # (including the primary, so the worker UI just iterates one list).
    primary = posters[0]
    rev = Revision(
        saved_poster_id    = primary.id,
        comment            = comment.strip() or "These two posters look too similar — pick one to replace.",
        flagged_by         = admin.username,
        status             = "open",
        revision_type      = "similar",
        related_poster_ids = _json.dumps([p.id for p in posters]),
    )
    db.add(rev)

    # Mark the master as needing revision so it surfaces on the worker side.
    mt = db.query(MasterTitle).filter_by(id=primary.master_title_id).first()
    if mt:
        mt.needs_revision = 1

    db.flush()
    log_activity(
        db, user=admin, action="flagged_similar", target_type="revision", target_id=rev.id,
        details={"poster_ids": ids, "master_id": primary.master_title_id},
    )
    db.commit()
    return JSONResponse({"ok": True, "revision_id": rev.id})


# ── Approval / rejection of awaiting_approval revisions ─────────────────────

@router.post("/revisions/{revision_id}/approve")
def approve_revision(
    revision_id: int,
    verdict: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin confirms the worker's fix. Revision → resolved, flag clears from worker side."""
    rev = db.query(Revision).filter_by(id=revision_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found.")
    if rev.status not in ("awaiting_approval", "open"):
        raise HTTPException(400, "Revision is not in a state that can be approved.")
    rev.status = "resolved"
    rev.resolved_by = admin.username
    rev.resolved_at = datetime.utcnow()
    rev.admin_verdict = verdict.strip() or None

    # Recompute needs_revision on the master
    sp = db.query(SavedPoster).filter_by(id=rev.saved_poster_id).first()
    if sp:
        mt = db.query(MasterTitle).filter_by(id=sp.master_title_id).first()
        if mt:
            any_active = (
                db.query(Revision)
                  .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
                  .filter(
                      SavedPoster.master_title_id == mt.id,
                      Revision.status.in_(("open", "awaiting_approval")),
                      Revision.id != rev.id,
                  )
                  .count()
            )
            mt.needs_revision = 1 if any_active else 0

    log_activity(db, user=admin, action="approved", target_type="revision", target_id=rev.id,
                 details={"verdict": verdict.strip() or None})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/revisions/{revision_id}/reject")
def reject_revision(
    revision_id: int,
    verdict: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin says 'no, redo it'. Revision → open with new comment for worker."""
    rev = db.query(Revision).filter_by(id=revision_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found.")
    if rev.status != "awaiting_approval":
        raise HTTPException(400, "Revision is not awaiting approval.")
    rev.status = "open"
    rev.admin_verdict = verdict.strip() or None
    # Keep submitted_at so the worker side can detect this is a re-opened-after-rejection.
    # The combination (status=open AND admin_verdict set) is the "REJECTED" indicator.
    # Append the verdict to the comment so the worker sees it as the new instruction
    if verdict.strip():
        prefix = (rev.comment or "").strip()
        rev.comment = (prefix + "\n\n[admin]: " + verdict.strip()) if prefix else ("[admin]: " + verdict.strip())
    log_activity(db, user=admin, action="rejected", target_type="revision", target_id=rev.id,
                 details={"verdict": verdict.strip() or None})
    db.commit()
    return JSONResponse({"ok": True})


# ── Skip-revise: admin sends a skipped title back to the worker with a note ──

@router.post("/title/{master_id}/skip_revise")
def skip_revise(
    master_id: int,
    note: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin reviews a skipped title and tells the worker to take another look.
    Resets the title to in_progress (still claimed by original worker if they're
    still around; otherwise pending) and stores the admin's note for display.
    """
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    if t.status != "skipped":
        raise HTTPException(400, "Title is not skipped.")
    note = note.strip()
    if not note:
        raise HTTPException(400, "A note is required when sending a skipped title back.")

    # Preserve the skip_reason for the worker's reference; just add admin_note.
    t.admin_note = note
    if t.claimed_by_id is not None:
        # Original claimer is still around — bounce back to in_progress for them
        t.status = "in_progress"
    else:
        # Was unclaimed — push back to pending so anyone can pick it up
        t.status = "pending"
    log_activity(
        db, user=admin, action="skip_revised", target_type="master_title", target_id=t.id,
        details={"note": note, "previous_skip_reason": t.skip_reason or ""},
    )
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/title/{master_id}/clear_admin_note")
def clear_admin_note(
    master_id: int,
    user_role: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin clears their own note (used when the title's been re-handled)."""
    t = db.query(MasterTitle).filter_by(id=master_id).first()
    if not t:
        raise HTTPException(404, "Title not found.")
    t.admin_note = None
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/revisions", response_class=HTMLResponse)
def revisions_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    open_rows = (
        db.query(Revision, SavedPoster, MasterTitle)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .outerjoin(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(Revision.status == "open")
          .order_by(Revision.created_at.desc())
          .all()
    )
    awaiting_rows = (
        db.query(Revision, SavedPoster, MasterTitle)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .outerjoin(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(Revision.status == "awaiting_approval")
          .order_by(Revision.submitted_at.desc().nullslast())
          .all()
    )
    # Recent deletions (auto-resolved revisions where the worker deleted the file).
    # Filter: status=resolved, admin_verdict starts with 'auto-resolved: file deleted',
    # AND admin hasn't yet acknowledged.
    deletion_rows = (
        db.query(Revision, SavedPoster, MasterTitle)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .outerjoin(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(
              Revision.status == "resolved",
              Revision.admin_acked_at.is_(None),
              Revision.admin_verdict.like("auto-resolved: file deleted%"),
          )
          .order_by(Revision.resolved_at.desc())
          .limit(50)
          .all()
    )
    resolved_rows = (
        db.query(Revision, SavedPoster, MasterTitle)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .outerjoin(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(Revision.status == "resolved")
          .order_by(Revision.resolved_at.desc())
          .limit(50)
          .all()
    )
    return templates.TemplateResponse(
        request,
        "admin_revisions.html",
        {"user": admin, "admin": admin,
            "open_rows": open_rows, "awaiting_rows": awaiting_rows,
            "deletion_rows": deletion_rows,
            "resolved_rows": resolved_rows,
            "active_tab": "revisions",
        },
    )


# ── Skipped-title review ────────────────────────────────────────────────────

@router.get("/skipped", response_class=HTMLResponse)
def skipped_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(MasterTitle)
          .filter(MasterTitle.status == "skipped")
          .order_by(MasterTitle.completed_at.desc().nullslast(), MasterTitle.updated_at.desc())
          .limit(500)
          .all()
    )
    return templates.TemplateResponse(
        request,
        "admin_skipped.html",
        {"user": admin, "admin": admin, "titles": rows, "active_tab": "skipped"},
    )


# ── Deletion review (worker deleted a flagged poster) ───────────────────────

@router.post("/deletions/{revision_id}/acknowledge")
def acknowledge_deletion(
    revision_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Mark this auto-resolved-via-deletion revision as 'seen' by admin.
    It disappears from the Recent Deletions panel.
    """
    rev = db.query(Revision).filter_by(id=revision_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found.")
    if rev.status != "resolved":
        raise HTTPException(400, "Revision is not resolved.")
    rev.admin_acked_at = datetime.utcnow()
    log_activity(db, user=admin, action="ack_deletion", target_type="revision", target_id=rev.id)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/deletions/{revision_id}/escalate")
def escalate_deletion(
    revision_id: int,
    note: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Admin saw the deletion and disagrees. Sends the title back to the worker
    with a note (worker sees it as the admin-note banner on the active title).
    If the title was already 'complete', it's reverted to 'in_progress' so the
    worker actually sees it as something requiring action.
    """
    note = (note or "").strip()
    if not note:
        raise HTTPException(400, "A note is required when sending a deletion back.")
    rev = db.query(Revision).filter_by(id=revision_id).first()
    if not rev:
        raise HTTPException(404, "Revision not found.")
    sp = db.query(SavedPoster).filter_by(id=rev.saved_poster_id).first()
    if not sp:
        raise HTTPException(404, "Underlying poster row missing.")
    mt = db.query(MasterTitle).filter_by(id=sp.master_title_id).first()
    if not mt:
        raise HTTPException(404, "Underlying title row missing.")
    mt.admin_note = note
    if mt.status == "complete":
        mt.status = "in_progress"
        mt.completed_at = None
    rev.admin_acked_at = datetime.utcnow()
    log_activity(
        db, user=admin, action="escalate_deletion", target_type="master_title", target_id=mt.id,
        details={"revision_id": rev.id, "deleted_filename": sp.filename, "note": note},
    )
    db.commit()
    return JSONResponse({"ok": True})


# ── Backups + restore ───────────────────────────────────────────────────────

@router.get("/backups", response_class=HTMLResponse)
def backups_page(
    request: Request,
    admin: User = Depends(require_admin),
):
    from ..backups import list_backups
    return templates.TemplateResponse(
        request,
        "admin_backups.html",
        {"user": admin, "admin": admin, "backups": list_backups(),
         "active_tab": "backups"},
    )


@router.post("/backups/snapshot")
def create_snapshot(
    name: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..backups import manual_snapshot
    try:
        path = manual_snapshot(name)
    except Exception as e:
        raise HTTPException(500, f"Snapshot failed: {e}")
    log_activity(db, user=admin, action="snapshot_created", target_type="backup",
                 details={"filename": path.name, "name": name})
    db.commit()
    return JSONResponse({"ok": True, "filename": path.name})


@router.post("/backups/restore")
def restore_from_backup(
    filename: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Replace the live DB with a backup file. Disposes the SQLAlchemy engine
    pool — both the legacy `db.engine` and the env-cache's live engine — so
    the next request opens against the restored file.
    """
    from ..backups import restore_backup
    from ..db import engine
    from ..envs import LIVE_ENV, _engines, _sessions, _engines_lock
    # Log first while the old DB is still live.
    log_activity(db, user=admin, action="restore_started", target_type="backup",
                 details={"filename": filename})
    db.commit()
    db.close()

    # Dispose the legacy module-level engine.
    engine.dispose()
    # Dispose (and drop) the env-cache entry for live, so the next request
    # rebuilds it against the restored file. Without this, pooled connections
    # to the *deleted-then-replaced* SQLite file would continue serving stale
    # data on Linux (where unlink doesn't kill open handles).
    with _engines_lock:
        live_eng = _engines.pop(LIVE_ENV, None)
        _sessions.pop(LIVE_ENV, None)
    if live_eng is not None:
        try:
            live_eng.dispose()
        except Exception:
            pass

    try:
        safety = restore_backup(filename)
    except FileNotFoundError:
        raise HTTPException(404, "Backup not found.")
    except PermissionError:
        raise HTTPException(403, "Bad backup path.")
    except Exception as e:
        raise HTTPException(500, f"Restore failed: {e}")

    return JSONResponse({"ok": True, "safety_snapshot": safety.name if safety else None})


@router.post("/backups/delete")
def delete_backup_route(
    filename: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..backups import delete_backup
    try:
        ok = delete_backup(filename)
    except PermissionError:
        raise HTTPException(403, "Bad backup path.")
    if not ok:
        raise HTTPException(404, "Backup not found.")
    log_activity(db, user=admin, action="backup_deleted", target_type="backup",
                 details={"filename": filename})
    db.commit()
    return JSONResponse({"ok": True})


# ── Test environments ───────────────────────────────────────────────────────

@router.get("/envs", response_class=HTMLResponse)
def envs_page(
    request: Request,
    admin: User = Depends(require_admin),
):
    from ..envs import list_test_envs, current_env, LIVE_ENV
    return templates.TemplateResponse(
        request,
        "admin_envs.html",
        {"user": admin, "admin": admin,
         "envs": list_test_envs(),
         "live_env": LIVE_ENV,
         "current_env": current_env(),
         "active_tab": "envs"},
    )


@router.post("/envs/create")
def envs_create(
    name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..envs import create_test_env
    name = name.strip()
    try:
        create_test_env(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(db, user=admin, action="env_create", target_type="env",
                 details={"name": name}, commit=True)
    return JSONResponse({"ok": True, "name": name})


@router.post("/envs/enter")
def envs_enter(
    request: Request,
    name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Set the pd_env cookie so subsequent requests run in this environment.
    Workers do NOT get this endpoint — they always operate in live.

    Note: the activity-log row written here lands in *the env we're currently
    in*, not the one we're switching to. That's the right behaviour: the
    "I left/entered an env" event belongs to the env you departed from.

    Returns a redirect (form submit) or JSON (fetch) based on Accept header.
    """
    from ..envs import LIVE_ENV, test_env_exists
    name = name.strip()
    if name != LIVE_ENV and not test_env_exists(name):
        raise HTTPException(404, f"Env {name!r} doesn't exist.")
    log_activity(db, user=admin, action="env_enter", target_type="env",
                 details={"name": name}, commit=True)
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        resp = JSONResponse({"ok": True, "active": name})
    else:
        resp = RedirectResponse("/admin", status_code=303)
    # httponly so JS can't peek; samesite=Lax so it follows top-level nav.
    # Session cookie (no Max-Age) — closing the browser drops you back to live,
    # which is a sensible safety default.
    resp.set_cookie("pd_env", name, httponly=True, samesite="lax", path="/")
    return resp


@router.post("/envs/leave")
def envs_leave(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Switch back to live by clearing the pd_env cookie. Returns either a
    redirect (if called as a form submit from the env banner) or JSON
    (if called from a fetch in JS) — detected via the Accept header.
    """
    log_activity(db, user=admin, action="env_leave", target_type="env",
                 details={}, commit=True)
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        resp = JSONResponse({"ok": True, "active": "live"})
    else:
        # Form submit from the banner — redirect back to admin home.
        resp = RedirectResponse("/admin", status_code=303)
    resp.delete_cookie("pd_env", path="/")
    return resp


@router.post("/envs/reset")
def envs_reset(
    name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..envs import reset_test_env, LIVE_ENV
    name = name.strip()
    if name == LIVE_ENV:
        raise HTTPException(400, "Cannot reset the live environment.")
    try:
        reset_test_env(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(db, user=admin, action="env_reset", target_type="env",
                 details={"name": name}, commit=True)
    return JSONResponse({"ok": True})


@router.post("/envs/delete")
def envs_delete(
    name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..envs import delete_test_env, LIVE_ENV
    name = name.strip()
    if name == LIVE_ENV:
        raise HTTPException(400, "Cannot delete the live environment.")
    try:
        delete_test_env(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    log_activity(db, user=admin, action="env_delete", target_type="env",
                 details={"name": name}, commit=True)
    return JSONResponse({"ok": True})


# ── Audit log ────────────────────────────────────────────────────────────────

@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request, admin: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request,
        "admin_audit.html",
        {"user": admin, "admin": admin, "active_tab": "audit"},
    )


@router.get("/api/audit")
def api_audit(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=10, le=500),
    user: str = Query(""),
    action: str = Query(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(ActivityLog)
    if user.strip():
        q = q.filter(ActivityLog.username == user.strip())
    if action.strip():
        q = q.filter(ActivityLog.action == action.strip())
    total = q.count()
    rows = (
        q.order_by(ActivityLog.created_at.desc())
         .offset((page - 1) * page_size)
         .limit(page_size)
         .all()
    )
    return JSONResponse({
        "page": page, "page_size": page_size, "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": [
            {
                "id": r.id,
                "username": r.username, "action": r.action,
                "target_type": r.target_type, "target_id": r.target_id,
                "details": (json.loads(r.details) if r.details else None),
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            }
            for r in rows
        ],
    })


# ── Filesystem tree ─────────────────────────────────────────────────────────

@router.get("/api/tree")
def filesystem_tree(admin: User = Depends(require_admin)):
    out = []
    for username in list_users_with_workspaces():
        user_node = {"name": username, "children": []}
        for d in list_date_folders(username):
            d_path = current_workspace_dir() / username / d
            d_node = {"name": d, "children": []}
            for tf in sorted([p for p in d_path.iterdir() if p.is_dir()], key=lambda p: p.name):
                from ..utils import list_images_in
                imgs = list_images_in(tf)
                d_node["children"].append({"name": tf.name, "count": len(imgs)})
            user_node["children"].append(d_node)
        out.append(user_node)
    return JSONResponse({"workers": out})


# ── Zip a date folder ───────────────────────────────────────────────────────

ZIP_JOBS: dict[str, dict] = {}
ZIP_LOCK = threading.Lock()


def _zip_worker(job_id: str, src: Path, zip_path: Path):
    try:
        all_files = []
        for root, _dirs, files in os.walk(src):
            for f in files:
                all_files.append(Path(root) / f)
        total = max(len(all_files), 1)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, fp in enumerate(all_files):
                arcname = fp.relative_to(src.parent)
                zf.write(fp, arcname)
                with ZIP_LOCK:
                    ZIP_JOBS[job_id].update(done=i + 1, total=total)
        with ZIP_LOCK:
            ZIP_JOBS[job_id].update(state="done", path=str(zip_path), error="")
    except Exception as e:
        with ZIP_LOCK:
            ZIP_JOBS[job_id].update(state="error", error=str(e))


@router.post("/zip/start")
def zip_start(
    worker: str = Form(...),
    date: str = Form(...),
    admin: User = Depends(require_admin),
):
    src = (current_workspace_dir() / worker / date).resolve()
    if not safe_under_workspace(src) or not src.is_dir():
        raise HTTPException(404, "Source folder not found.")

    zip_dir = current_workspace_dir() / "_zips"
    zip_dir.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    zip_name = f"{worker}_{date}_{ts}.zip"
    zip_path = zip_dir / zip_name

    job_id = f"{worker}-{date}-{ts}"
    with ZIP_LOCK:
        ZIP_JOBS[job_id] = {
            "state": "running", "done": 0, "total": 0,
            "path": "", "error": "", "name": zip_name,
        }
    threading.Thread(target=_zip_worker, args=(job_id, src, zip_path), daemon=True).start()
    return JSONResponse({"ok": True, "job_id": job_id, "name": zip_name})


@router.get("/zip/status/{job_id}")
def zip_status(job_id: str, admin: User = Depends(require_admin)):
    with ZIP_LOCK:
        job = ZIP_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return JSONResponse(job)


@router.get("/zip/download/{job_id}")
def zip_download(job_id: str, admin: User = Depends(require_admin)):
    with ZIP_LOCK:
        job = ZIP_JOBS.get(job_id)
    if not job or job.get("state") != "done":
        raise HTTPException(404, "Zip not ready.")
    p = Path(job["path"])
    if not p.is_file():
        raise HTTPException(404, "Zip file missing.")
    return FileResponse(p, filename=p.name, media_type="application/zip")
