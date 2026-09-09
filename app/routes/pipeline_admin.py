"""
Admin-facing pipeline routes — everything the dashboard needs.

Mounted at /admin/pipeline. Session-authenticated as admin; the machine-facing
counterpart lives in routes/pipeline_api.py and uses node tokens instead.

────────────────────────────────────────────────────────────────────────────
WHAT THIS EXPOSES
────────────────────────────────────────────────────────────────────────────
  Pipeline overview   funnel counts, greenlight queue, live job feed
  Greenlight          promote titles / date ranges into the pipeline
  Processing          edit the JSX + Photoshop settings, no deploy needed
  Upload accounts     CRUD, credentials, per-account timings and quota
  Upload settings     selector map, title/keyword templates, batch sizes
  Needs Attention     everything stopped or stuck, grouped by what fixes it
  Test & Debug        run one stage on one image and watch the log
  Worker nodes        register, rotate tokens, health

────────────────────────────────────────────────────────────────────────────
CONVENTIONS TO KEEP
────────────────────────────────────────────────────────────────────────────
Settings are written through pipeline.set_setting(), which validates the key
against pipeline.DEFAULTS. That's what stops the dashboard from silently
persisting a typo that then resolves to nothing at runtime. If you add a
knob, add it to DEFAULTS first — the UI will pick it up automatically.

Everything accepts an optional project_id and passes it down. Today there's
one project; the day there are three, none of these handlers change.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from .. import pipeline as P
from ..audit import log as log_activity
from ..auth import require_admin
from ..config import WORKSPACE_DIR
from ..db import get_db
from ..models import (
    AccountProject, LedgerEntry, MasterTitle, PipelineJob, ProcessedImage,
    Project, SavedPoster, UploadAccount, UploadTracking, User, WorkerNode,
)
from ..templating import templates
from ..timeutil import fmt_local, local_today


router = APIRouter(prefix="/admin/pipeline", tags=["pipeline-admin"])


# ═══════════════════════════════════════════════════════════════════════════
#  PAGE
# ═══════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────
# ONE TEMPLATE, THREE DOORS (2026-09-06, UI Revamp Part 1).
#
# The old Pipeline page mixed three activities nobody does at the same time:
# WATCHING the machinery, DECIDING what to promote, and CONFIGURING it. They
# are now three nav entries — Pipeline, Greenlight, Settings — but ONE
# template and ONE script, because the sections already live behind tabs and
# the script switches them by name. Each door renders only its own tab
# buttons; every section's markup still renders on every door, so the script
# finds every hook it queries wherever it runs, and clicking through to a
# section another door owns is a normal navigation (the script redirects).
#
# A real three-file split was considered and rejected: cutting an 874-line
# template three ways is the most expensive class of edit in this project's
# history, for zero behaviour the mode flag does not already buy.
# ─────────────────────────────────────────────────────────────────────────

def _pipeline_shell(request, admin, db, *, page_mode, active_tab,
                    default_section):
    P.ensure_default_project(db)
    db.commit()

    # Which processing panel this project gets. A GPT project has no
    # Photoshop executable, no JSX script and no sharpen radius; showing
    # those to it is worse than clutter, it implies settings that do nothing.
    from ..routes.admin import current_project
    project = current_project(request, admin, db)

    if default_section == "_settings_first":
        # The settings door opens on the first settings tab this project
        # actually has: image search only exists for in-page projects.
        default_section = ("search" if project.search_mode == "inpage"
                          else "processing")

    return templates.TemplateResponse(
        request, "admin_pipeline.html",
        {"user": admin, "active_tab": active_tab,
         "page_mode": page_mode,
         "default_section": default_section,
         "project": project,
         "processor": project.processor,
         # 'inpage' means the workers search inside the site, so the Brave
         # phrasing boxes have something to govern. An 'external' project
         # sends its workers to another site and must not be shown search
         # settings that would do nothing — the same reasoning as the
         # processor panels below it.
         "search_mode": project.search_mode,
         "has_review_gate": bool(project.has_review_gate),
         "item_noun": project.item_noun,
         "item_nouns": project.item_noun_plural},
    )


@router.get("", response_class=HTMLResponse)
def pipeline_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The live-operations door: overview, needs attention, nodes."""
    return _pipeline_shell(request, admin, db, page_mode="ops",
                           active_tab="pipeline",
                           default_section="overview")


@router.get("/greenlight", response_class=HTMLResponse)
def greenlight_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The deciding door: a work queue, like Worker Images — its own tab."""
    return _pipeline_shell(request, admin, db, page_mode="greenlight",
                           active_tab="greenlight",
                           default_section="greenlight")


@router.get("/settings", response_class=HTMLResponse)
def pipeline_settings_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The configuring door: image search, processing, upload, test."""
    return _pipeline_shell(request, admin, db, page_mode="settings",
                           active_tab="pipe_settings",
                           default_section="_settings_first")



# Encrypted at rest with the same Fernet key that protects marketplace
# passwords, and never echoed back to the browser. Listed explicitly rather
# than guessed from the field name: a rule like "anything containing
# 'password'" would silently stop protecting `openai_api_key`, which is just
# as much a credential.
SECRET_KEYS = {
    "storage_sftp_password",
    "openai_api_key", "openai_admin_key",
    "brave_api_key_free", "brave_api_key_paid",
}


def _project(request: Request, admin: User, db: Session, explicit=None):
    """
    Which project a pipeline API call operates on.

    ════════════════════════════════════════════════════════════════════════
    WHY NOT _project(request, admin, db, project_id)
    ════════════════════════════════════════════════════════════════════════
    Every endpoint here used to do exactly that, with `project_id` coming
    from a query parameter the dashboard never actually sends. So it always
    resolved to None -> ensure_default_project() -> the MOVIE project.

    The visible symptom: standing inside MUSIK, the Greenlight tab listed
    Inception and Fight Club. The dangerous version of the same bug: pressing
    GREENLIGHT there would have promoted movie posters from a page that said
    MUSIK at the top.

    Falling back to the ACTIVE project instead of the default fixes every
    endpoint at once, because they all funnel through here. An explicit
    project_id still wins, so a caller that genuinely wants another project
    (or a future cross-project view) can still ask for one.
    """
    if explicit:
        return P.resolve_project(db, explicit)
    from ..routes.admin import current_project
    return current_project(request, admin, db)


def _title_scope(db: Session, project):
    """
    Scope a MasterTitle query to one project, honouring the NULL rule.

    Three endpoints here hand-rolled `or_(project_id == X, project_id IS
    NULL)`, which reads as "this project plus anything unassigned" and is
    wrong for every project except the default one — MUSIK would have
    inherited all 101,605 NULL movie rows. `P.project_scope()` already knows
    the rule; use it rather than repeating it.
    """
    from ..pipeline import _default_project_id
    return P.project_scope(project.id, default_project_id=_default_project_id(db))


# ═══════════════════════════════════════════════════════════════════════════
#  OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/overview")
def api_overview(
    request: Request,
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Everything the Pipeline tab renders on load: the funnel, per-account
    quota, node health, recent jobs, and failure counts.

    One endpoint rather than five so the page has a single source of truth
    per refresh and can't display internally inconsistent numbers.
    """
    project = _project(request, admin, db, project_id)
    funnel = P.funnel_counts(db, project_id=project.id)

    accounts = []
    for account in P.accounts_for_project(db, project.id):
        quota = P.account_quota(db, account)
        pending = (
            db.query(func.count(UploadTracking.id))
              .filter(UploadTracking.account_id == account.id,
                      # Scoped to THIS project: a shared account's queue for
                      # another niche is not this screen's business.
                      UploadTracking.project_id == project.id,
                      UploadTracking.status.in_(("pending", "failed")))
              .scalar() or 0
        )
        accounts.append({
            **P.account_payload(db, account),
            "quota": quota,
            "pending": pending,
            "available": P.account_is_available(account),
        })

    nodes = []
    now = datetime.utcnow()
    stale_after = now - timedelta(minutes=5)
    for node in db.query(WorkerNode).order_by(WorkerNode.id.asc()).all():
        nodes.append({
            "id": node.id,
            "name": node.name,
            "capabilities": [c.strip() for c in (node.capabilities or "").split(",") if c.strip()],
            "is_enabled": bool(node.is_enabled),
            "hostname": node.hostname,
            "agent_version": node.agent_version,
            "last_seen_at": fmt_local(node.last_seen_at, "%Y-%m-%d %H:%M") if node.last_seen_at else None,
            # HOW LONG AGO, not just a timestamp. ONLINE means "seen within
            # five minutes", a window that stops the label flickering between
            # 30-second polls — but it also means a node that died two
            # minutes ago still reads ONLINE next to a stale clock time, and
            # you are left comparing timestamps in your head to notice.
            # Reading the age out loud makes a stopping node obvious before
            # the label catches up.
            "last_seen_age_s": (
                int((now - node.last_seen_at).total_seconds())
                if node.last_seen_at else None
            ),
            "online": bool(node.last_seen_at and node.last_seen_at > stale_after),
        })

    jobs = [
        _job_summary(j) for j in
        db.query(PipelineJob)
          .order_by(PipelineJob.created_at.desc())
          .limit(15)
          .all()
    ]

    # ── Work in flight right now ────────────────────────────────────────
    # Which exact images a node is holding, and for how long. Without this the
    # only signal during a five-minute Photoshop run is a stage counter ticking
    # up, which is indistinguishable from a hang.
    now = datetime.utcnow()
    in_flight = []

    processing_rows = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.deleted_at.is_(None),
                  # Scoped like every other query on this page. Unscoped, the
                  # MUSIK overview showed movie posters moving through
                  # Photoshop and read as if its own stage were running.
                  _title_scope(db, project))
          .order_by(SavedPoster.claimed_at.asc().nullslast())
          .limit(50)
          .all()
    )
    for poster, title in processing_rows:
        elapsed = int((now - poster.claimed_at).total_seconds()) if poster.claimed_at else None
        in_flight.append({
            "stage": "processing",
            "poster_id": poster.id,
            "title": title.title,
            "year": title.year,
            "external_id": title.external_id,
            "filename": poster.filename,
            "node": poster.claimed_by,
            "elapsed_s": elapsed,
            # The dispatcher reaps a claim once it goes stale; surface that so a
            # long-running image reads differently from an abandoned one.
            "stale": bool(elapsed and elapsed > int(P.get_setting(db, "claim_timeout_min")) * 60),
        })

    uploading_rows = (
        db.query(UploadTracking, SavedPoster, MasterTitle, UploadAccount)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .join(UploadAccount, UploadTracking.account_id == UploadAccount.id)
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.project_id == project.id)
          .order_by(UploadTracking.claimed_at.asc().nullslast())
          .limit(50)
          .all()
    )
    for tracking, poster, title, account in uploading_rows:
        elapsed = int((now - tracking.claimed_at).total_seconds()) if tracking.claimed_at else None
        in_flight.append({
            "stage": "uploading",
            "poster_id": poster.id,
            "tracking_id": tracking.id,
            "title": title.title,
            "year": title.year,
            "external_id": title.external_id,
            "filename": poster.filename,
            "remote_title": tracking.remote_title,
            "account": account.name,
            "node": tracking.claimed_by,
            "elapsed_s": elapsed,
            "stale": bool(elapsed and elapsed > int(P.get_setting(db, "claim_timeout_min")) * 60),
        })

    process_failures = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "failed_processing",
                  SavedPoster.deleted_at.is_(None),
                  MasterTitle.project_id == project.id)
          .scalar() or 0
    )
    upload_failures = (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.status == "failed",
                  UploadTracking.project_id == project.id)
          .scalar() or 0
    )

    # The tab badge. Stale claims are included because they are exactly the
    # kind of stoppage a failure count misses — nothing is marked failed, and
    # nothing is moving either.
    stale_cutoff = now - timedelta(
        minutes=int(P.get_setting(db, "claim_timeout_min", project=project) or 30))
    stalled_count = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.claimed_at.isnot(None),
                  SavedPoster.claimed_at < stale_cutoff,
                  SavedPoster.deleted_at.is_(None),
                  _title_scope(db, project))
          .scalar() or 0
    ) + (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.claimed_at.isnot(None),
                  UploadTracking.claimed_at < stale_cutoff,
                  UploadTracking.project_id == project.id)
          .scalar() or 0
    )

    return JSONResponse({
        "ok": True,
        "project": {"id": project.id, "slug": project.slug, "name": project.name},
        "attention": process_failures + upload_failures + stalled_count,
        "projects": [
            {"id": p.id, "slug": p.slug, "name": p.name, "is_active": bool(p.is_active)}
            for p in db.query(Project).order_by(Project.id.asc()).all()
        ],
        "funnel": funnel,
        "in_flight": in_flight,
        "accounts": accounts,
        "nodes": nodes,
        "jobs": jobs,
        "failures": {"processing": process_failures, "upload": upload_failures},
        "greenlight_mode": P.get_setting(db, "greenlight_mode", project=project),
        # Whether this project is running, draining or halted, plus why.
        "run_mode": P.run_mode_state(db, project),
        "history": P.upload_history(db, days=30),
        "today": local_today().isoformat(),
    })


def _job_summary(job: PipelineJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "progress_note": job.progress_note,
        "requested_by": job.requested_by,
        "claimed_by": job.claimed_by,
        "error": job.error,
        "created_at": fmt_local(job.created_at, "%Y-%m-%d %H:%M"),
        "finished_at": fmt_local(job.finished_at, "%Y-%m-%d %H:%M") if job.finished_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  GREENLIGHT
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/greenlight/queue")
def api_greenlight_queue(
    request: Request,
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Completed-but-not-greenlit work, grouped by save date.

    Date-grouped because that's how payment works and therefore how you
    actually think about batches — "greenlight last week" rather than
    picking 200 titles by hand. Also reports whether each date is fully
    paid, so auto-greenlight and manual greenlight agree on what's safe.
    """
    project = _project(request, admin, db, project_id)

    # Poster-based (not MasterTitle.greenlit_at) so this agrees exactly with
    # what greenlight_titles would promote, including a title that was already
    # greenlit but has since gained a new poster.
    rows = (
        db.query(
            MasterTitle.original_save_date,
            func.count(func.distinct(MasterTitle.id)),
            func.count(SavedPoster.id),
        )
        .join(SavedPoster, (SavedPoster.master_title_id == MasterTitle.id) &
                           (SavedPoster.deleted_at.is_(None)))
        .filter(MasterTitle.status == "complete",
                P.awaiting_greenlight_poster_filter(),
                _title_scope(db, project))
        .group_by(MasterTitle.original_save_date)
        .order_by(MasterTitle.original_save_date.asc())
        .all()
    )

    # Which posters are already covered by a payment run — lets the UI mark a
    # date as safe to greenlight without a second round trip.
    from ..payments import _already_paid_poster_ids
    paid_ids: set[int] = set()
    for (worker_id,) in db.query(User.id).filter(User.role == "worker").all():
        paid_ids |= _already_paid_poster_ids(db, worker_id)

    dates = []
    for save_date, title_count, poster_count in rows:
        if save_date is None:
            continue
        day_poster_ids = {
            pid for (pid,) in
            db.query(SavedPoster.id)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(MasterTitle.status == "complete",
                      P.awaiting_greenlight_poster_filter(),
                      MasterTitle.original_save_date == save_date,
                      SavedPoster.deleted_at.is_(None))
              .all()
        }
        paid_count = len(day_poster_ids & paid_ids)
        dates.append({
            "date": save_date.isoformat(),
            "titles": title_count,
            "posters": poster_count,
            "paid_posters": paid_count,
            "fully_paid": bool(day_poster_ids) and paid_count == len(day_poster_ids),
        })

    return JSONResponse({
        "ok": True,
        "dates": dates,
        "total_titles": sum(d["titles"] for d in dates),
        "total_posters": sum(d["posters"] for d in dates),
    })


@router.post("/api/greenlight")
def api_greenlight(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Greenlight by explicit title ids, by date range, or by a list of dates.

    Idempotent: already-greenlit titles are counted as skipped, so
    double-clicking or re-running a payment hook is harmless.
    """
    project = _project(request, admin, db, payload.get("project_id"))
    result = {"greenlit": 0, "skipped": 0, "posters": 0}

    if payload.get("title_ids"):
        result = P.greenlight_titles(
            db, payload["title_ids"], by=admin.username, reason="manual",
        )
    elif payload.get("dates"):
        for raw in payload["dates"]:
            try:
                day = date.fromisoformat(raw)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Bad date: {raw}")
            part = P.greenlight_date_range(db, day, day, by=admin.username)
            for key in result:
                result[key] += part.get(key, 0)
    elif payload.get("start") and payload.get("end"):
        try:
            start = date.fromisoformat(payload["start"])
            end = date.fromisoformat(payload["end"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Bad start/end date.")
        if end < start:
            raise HTTPException(400, "End is before start.")
        result = P.greenlight_date_range(db, start, end, by=admin.username)
    elif payload.get("all_paid"):
        # Everything already paid for — the common "catch me up" action.
        from ..payments import _already_paid_poster_ids
        paid_ids: set[int] = set()
        for (worker_id,) in db.query(User.id).filter(User.role == "worker").all():
            paid_ids |= _already_paid_poster_ids(db, worker_id)
        if paid_ids:
            title_ids = [
                tid for (tid,) in
                db.query(SavedPoster.master_title_id)
                  .filter(SavedPoster.id.in_(paid_ids))
                  .distinct()
                  .all()
            ]
            result = P.greenlight_titles(db, title_ids, by=admin.username,
                                         reason="all_paid")
    else:
        raise HTTPException(400, "Provide title_ids, dates, start+end, or all_paid.")

    log_activity(db, user=admin, action="pipeline_greenlight", target_type="pipeline",
                 details=result)
    db.commit()
    return JSONResponse({"ok": True, **result})


@router.post("/api/ungreenlight")
def api_ungreenlight(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Pull titles back out of the queue. Only unprocessed images are affected —
    anything already in storage or uploaded is left alone, because this is a
    scheduling decision, not a retraction.
    """
    title_ids = payload.get("title_ids") or []
    if not title_ids:
        raise HTTPException(400, "title_ids is required.")
    count = P.ungreenlight_titles(db, title_ids)
    log_activity(db, user=admin, action="pipeline_ungreenlight",
                 target_type="pipeline", details={"titles": count})
    db.commit()
    return JSONResponse({"ok": True, "titles": count})


# ═══════════════════════════════════════════════════════════════════════════
#  TITLE BROWSER
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/titles")
def api_titles(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
    status: str = Query(""),
    q: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    only_actionable: int = Query(0),
    ids_only: int = Query(0),
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Paged title list filtered by pipeline stage — the drill-down behind every
    funnel number, and where per-title greenlight selection happens.

    `ids_only=1` returns just the matching ids with no per-title poster
    hydration. That's what powers "select all N matching" in the browser:
    selecting across pages needs the full id set, but building poster payloads
    for thousands of titles would be pointlessly expensive.

    `only_actionable=1` narrows to titles that actually have something to
    promote — used so the greenlight buttons can't act on rows where every
    poster is already in the pipeline.
    """
    project = _project(request, admin, db, project_id)
    query = (
        db.query(MasterTitle)
          .filter(_title_scope(db, project))
    )

    # "Awaiting greenlight" is poster-based, matching greenlight_titles'
    # own rule — a title already greenlit but holding a new poster belongs here.
    if status == "awaiting_greenlight" or only_actionable:
        query = (
            query.filter(MasterTitle.status == "complete")
                 .filter(MasterTitle.id.in_(
                     db.query(SavedPoster.master_title_id)
                       .filter(SavedPoster.deleted_at.is_(None),
                               P.awaiting_greenlight_poster_filter())
                 ))
        )
    elif status:
        query = query.filter(MasterTitle.pipeline_status == status)

    if q.strip():
        query = query.filter(MasterTitle.title.ilike(f"%{q.strip()}%"))
    if date_from:
        try:
            query = query.filter(MasterTitle.original_save_date >= date.fromisoformat(date_from))
        except ValueError:
            raise HTTPException(400, "Bad date_from.")
    if date_to:
        try:
            query = query.filter(MasterTitle.original_save_date <= date.fromisoformat(date_to))
        except ValueError:
            raise HTTPException(400, "Bad date_to.")

    total = query.count()

    if ids_only:
        # Cap generously — enough to select an entire realistic backlog in one
        # action, low enough that a stray call can't build a million-item list.
        ids = [
            r[0] for r in
            query.with_entities(MasterTitle.id)
                 .order_by(MasterTitle.original_save_date.asc().nullslast(),
                           MasterTitle.external_id.asc().nullslast())
                 .limit(20_000)
                 .all()
        ]
        return JSONResponse({"ok": True, "total": total, "ids": ids,
                             "truncated": total > len(ids)})

    rows = (
        query.order_by(MasterTitle.original_save_date.asc().nullslast(),
                       MasterTitle.external_id.asc().nullslast())
             .offset((page - 1) * page_size)
             .limit(page_size)
             .all()
    )

    items = []
    for title in rows:
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.master_title_id == title.id,
                      SavedPoster.deleted_at.is_(None))
              .order_by(SavedPoster.created_at.asc())
              .all()
        )
        uploaded = (
            db.query(func.count(UploadTracking.id))
              .filter(UploadTracking.saved_poster_id.in_([p.id for p in posters] or [0]),
                      UploadTracking.status == "uploaded")
              .scalar() or 0
        ) if posters else 0

        # How many posters greenlighting this title would actually promote, and
        # how many are already moving. Surfaced so the browser can show
        # "2 of 3 already in pipeline" instead of the operator guessing why a
        # greenlight click reported fewer titles than they selected.
        pending = sum(1 for p in posters
                      if p.pipeline_status in (None, "", "skipped"))
        in_pipeline = len(posters) - pending

        items.append({
            "id": title.id,
            "external_id": title.external_id,
            "title": title.title,
            "year": title.year,
            "content_type": title.content_type,
            "status": title.status,
            "save_date": title.original_save_date.isoformat() if title.original_save_date else None,
            "pipeline_status": title.pipeline_status,
            "greenlit_at": fmt_local(title.greenlit_at, "%Y-%m-%d %H:%M") if title.greenlit_at else None,
            "greenlit_by": title.greenlit_by,
            "poster_count": len(posters),
            "uploaded_count": uploaded,
            "pending_count": pending,
            "in_pipeline_count": in_pipeline,
            # True when a greenlight click would do something on this row.
            # Selectable if there is ANYTHING to act on — greenlightable
            # OR already in the pipeline so PULL BACK applies. Was only the
            # greenlight case, which left uploaded rows un-tickable so you
            # could only "select all matching" (owner's find, 2026-09-06).
            # Both bulk endpoints filter server-side, so selecting an
            # ineligible row is a safe no-op.
            "actionable": len(posters) > 0,
            "posters": [
                {"id": p.id, "filename": p.filename, "status": p.pipeline_status,
                 "attempts": p.process_attempts, "error": p.process_error}
                for p in posters
            ],
        })

    return JSONResponse({
        "ok": True, "page": page, "page_size": page_size, "total": total,
        "pages": (total + page_size - 1) // page_size, "items": items,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  SETTINGS  (processing + upload, incl. JSX and selectors)
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/search_cache/clear")
def api_clear_search_cache(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Forget every stored search result (owner's request, 2026-09-06).

    The cache exists to stop the same query spending the Brave quota twice,
    but while the owner is EXPERIMENTING with wording he sometimes wants
    the same button to genuinely re-ask. Editing a phrasing already misses
    the cache (the key is the words); this is for re-running the SAME
    words. All rows, all projects: the cache is cheap to refill and a
    partial clear would need explaining on the screen.
    """
    from ..models import SearchCache
    n = db.query(SearchCache).delete(synchronize_session=False)
    log_activity(db, user=admin, action="search_cache_cleared",
                 target_type="pipeline", target_id=None, details={"rows": n})
    db.commit()
    return JSONResponse({"ok": True, "cleared": n})


@router.get("/api/settings")
def api_get_settings(
    request: Request,
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Every knob with its effective value, plus the code defaults so the UI can
    show "modified" badges and offer a per-field reset.
    """
    project = _project(request, admin, db, project_id)
    effective = P.all_settings(db, project=project)

    # Secrets are NEVER sent to the browser. Two reasons, one of which was a
    # real bug: the field rendered the stored value back into the form, so
    # re-saving the panel encrypted the already-encrypted string a second time
    # and the key silently stopped working. And a credential that never leaves
    # the server cannot leak from a screenshot or a shoulder.
    #
    # The UI shows an empty box plus "a value is saved", and a blank
    # submission means "leave it alone" (see api_set_settings).
    for key in SECRET_KEYS:
        if key in effective:
            effective[key] = ""

    # Which keys have an explicit override, and at what scope — this is what
    # lets the UI distinguish "inherited" from "set for this project".
    overrides = {}
    for key in P.DEFAULTS:
        global_row = db.query(P.AppSetting).filter_by(key=f"{P.SETTINGS_ROOT}.{key}").first()
        project_row = db.query(P.AppSetting).filter_by(
            key=f"{P.SETTINGS_ROOT}.{project.slug}.{key}").first()
        overrides[key] = {
            "global": global_row is not None,
            "project": project_row is not None,
            # For secrets the UI cannot tell "set" from "empty" by looking at
            # the value, because it never receives it.
            "has_value": bool(
                (project_row and project_row.value) or (global_row and global_row.value)
            ) if key in SECRET_KEYS else None,
        }

    return JSONResponse({
        "ok": True,
        "project": {"id": project.id, "slug": project.slug, "name": project.name},
        "settings": effective,
        "defaults": P.DEFAULTS,
        "overrides": overrides,
        "script_version": P.script_version(db, project=project),
    })


@router.post("/api/settings")
def api_set_settings(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Persist one or more settings.

    `scope` decides whether the change is global or project-specific — the
    mechanism that lets the celebrity niche override just its JSX and keyword
    list while inheriting everything else.

    Unknown keys are rejected by pipeline.set_setting() rather than silently
    stored, which is what keeps a UI typo from becoming a runtime mystery.
    """
    updates = payload.get("settings") or {}
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(400, "settings object is required.")

    scope = payload.get("scope", "global")
    project = _project(request, admin, db, payload.get("project_id"))
    target = project if scope == "project" else None

    applied = []
    for key, value in updates.items():
        try:
            if key in SECRET_KEYS and value:
                # A blank submission means "leave it alone", not "erase it" —
                # the form renders secrets masked, so an untouched field
                # arrives empty and must not wipe a working credential.
                value = P.encrypt_secret(str(value))
            elif key in SECRET_KEYS and not value:
                applied.append(f"{key} (unchanged)")
                continue
            P.set_setting(db, key, value, project=target, by=admin.username)
        except KeyError as e:
            raise HTTPException(400, str(e))
        applied.append(key)

    log_activity(db, user=admin, action="pipeline_settings", target_type="pipeline",
                 details={"keys": applied, "scope": scope, "project": project.slug})
    db.commit()
    return JSONResponse({
        "ok": True, "applied": applied,
        "script_version": P.script_version(db, project=project),
    })


@router.post("/api/settings/reset")
def api_reset_setting(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Drop an override so the next tier down (global, then code default) applies."""
    key = payload.get("key")
    if not key:
        raise HTTPException(400, "key is required.")
    scope = payload.get("scope", "global")
    project = _project(request, admin, db, payload.get("project_id"))
    P.clear_setting(db, key, project=project if scope == "project" else None)
    # A settings change is exactly what you want to find when behaviour
    # changes and nobody remembers touching anything.
    log_activity(db, user=admin, action="setting_reset", target_type="setting",
                 details={"key": key, "scope": scope,
                          "project": project.slug if project and scope == "project" else None})
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/settings/script_preview")
def api_script_preview(
    request: Request,
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The JSX exactly as the node will receive it, with numeric placeholders
    substituted. Lets you confirm a template edit produced valid-looking
    script before spending a Photoshop run on it.
    """
    project = _project(request, admin, db, project_id)
    return JSONResponse({
        "ok": True,
        "script": P.render_process_script(db, project=project),
        "version": P.script_version(db, project=project),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  UPLOAD ACCOUNTS
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/accounts")
def api_accounts(
    request: Request,
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    project = _project(request, admin, db, project_id)
    out = []
    for account in P.accounts_for_project(db, project.id):
        stats = {
            status: count for status, count in
            db.query(UploadTracking.status, func.count(UploadTracking.id))
              .filter(UploadTracking.account_id == account.id,
                      UploadTracking.project_id == project.id)
              .group_by(UploadTracking.status)
              .all()
        }
        out.append({
            **P.account_payload(db, account),          # never includes password
            "quota": P.account_quota(db, account),
            "available": P.account_is_available(account),
            "stats": stats,
        })
    return JSONResponse({"ok": True, "accounts": out})


@router.get("/api/accounts/available")
def api_accounts_available(
    request: Request,
    project_id: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Accounts that exist but are NOT yet attached to this project.

    This is the list behind ADD EXISTING ACCOUNT. Its whole reason for
    existing is that the same FineArtAmerica account carries both niches:
    without it the only way to upload MUSIK through the account already
    serving movies was to create it a second time, which meant two Chrome
    profiles, two copies of the password, and a daily limit the marketplace
    applies once being counted twice.
    """
    project = _project(request, admin, db, project_id)
    attached = {a.id for a in P.accounts_for_project(db, project.id)}

    out = []
    for account in db.query(UploadAccount).order_by(UploadAccount.name).all():
        if account.id in attached:
            continue
        others = [
            n for (n,) in db.query(Project.name)
                            .join(AccountProject,
                                  AccountProject.project_id == Project.id)
                            .filter(AccountProject.account_id == account.id).all()
        ]
        out.append({
            "id": account.id,
            "name": account.name,
            "target_site": account.target_site,
            "email": account.email,
            "banned": bool(account.banned_at),
            # So the picker can say "already used by MUSIK" rather than
            # presenting an account with no context.
            "used_by": others,
        })
    return JSONResponse({"ok": True, "accounts": out, "project": project.name})


@router.post("/api/accounts/{account_id}/attach")
def api_attach_account(
    account_id: int,
    request: Request,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Let this project upload through an account that already exists.

    Deliberately does NOT queue the back catalogue — same rule as creating an
    account. Attaching is a safe, reversible act; use REQUEUE BACK CATALOGUE
    when you actually want the existing work sent there.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")
    if account.banned_at is not None:
        raise HTTPException(400, f"{account.name} is banned — attaching it "
                                 f"would queue work that can never upload.")

    project = _project(request, admin, db, payload.get("project_id"))
    added = P.attach_account(db, account_id=account_id, project_id=project.id,
                             by=admin.username)
    if added:
        log_activity(db, user=admin, action="pipeline_account_attached",
                     target_type="upload_account", target_id=account_id,
                     details={"project": project.name, "name": account.name})
    db.commit()
    return JSONResponse({"ok": True, "attached": added})


@router.post("/api/accounts/{account_id}/detach")
def api_detach_account(
    account_id: int,
    request: Request,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stop sending THIS project's work to this account.

    The account, its credentials and its entire upload history all survive —
    this is not a delete. Anything already queued for the pair is left in
    place rather than removed, because a half-finished set that silently
    disappeared would be unexplainable a week later. It simply stops being
    handed out, because the dispatcher only walks attached accounts.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")

    project = _project(request, admin, db, payload.get("project_id"))
    queued = (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.account_id == account_id,
                  UploadTracking.project_id == project.id,
                  UploadTracking.status.in_(("pending", "failed")))
          .scalar() or 0
    )
    removed = P.detach_account(db, account_id=account_id, project_id=project.id)
    if removed:
        log_activity(db, user=admin, action="pipeline_account_detached",
                     target_type="upload_account", target_id=account_id,
                     details={"project": project.name, "name": account.name,
                              "left_queued": queued})
    db.commit()
    return JSONResponse({"ok": True, "detached": removed, "left_queued": queued})


@router.post("/api/accounts")
def api_create_account(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a marketplace account.

    The password is Fernet-encrypted immediately and only ever decrypted for
    an authenticated worker node. Creating an account does NOT retroactively
    queue the back catalogue — use /api/accounts/{id}/requeue for that, so
    adding an account is a safe, reversible act.
    """
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    if not name or not email or not password:
        raise HTTPException(400, "name, email and password are required.")

    # `attach_to_project` false means an earn-only account: it will appear on
    # Earnings and nothing will ever be uploaded to it. That is how the
    # TeePublic accounts are added.
    attach = payload.get("attach_to_project", True)
    project = _project(request, admin, db, payload.get("project_id")) if attach else None

    # An account name is unique per MARKETPLACE now, not per project. It used
    # to be per project, which is exactly what forced the same FineArtAmerica
    # account to be created twice under two names.
    target_site = (payload.get("target_site") or "fineartamerica").strip().lower()
    # Rejected loudly rather than stored. An unknown marketplace name creates
    # an account that nothing can read, that appears in no total, and that
    # gives no clue why — the worst kind of failure for a value you typed by
    # hand.
    if target_site not in P.MARKETPLACES:
        raise HTTPException(
            400, f"'{target_site}' is not a marketplace this app knows. "
                 f"Use one of: {', '.join(P.MARKETPLACES)}.")

    if db.query(UploadAccount).filter_by(target_site=target_site, name=name).first():
        raise HTTPException(
            400, f"An account named '{name}' already exists on {target_site}. "
                 f"Use ADD EXISTING ACCOUNT to attach it to this project "
                 f"instead of creating a second copy.")

    account = UploadAccount(
        project_id=project.id if project else None,
        name=name,
        target_site=target_site,
        email=email,
        password_enc=P.encrypt_secret(password),
        profile_url=(payload.get("profile_url") or "").strip() or None,
        chrome_profile_dir=(payload.get("chrome_profile_dir") or "").strip() or None,
        daily_limit=int(payload.get("daily_limit") or 100),
        rotation_order=int(payload.get("rotation_order") or 100),
        rotation_size=(int(payload["rotation_size"])
                       if payload.get("rotation_size") else None),
        is_enabled=1 if payload.get("is_enabled", True) else 0,
        timing_json=json.dumps(payload["timings"]) if payload.get("timings") else None,
        selectors_json=json.dumps(payload["selectors"]) if payload.get("selectors") else None,
        created_by=admin.username,
    )
    db.add(account)
    db.flush()
    if project is not None:
        P.attach_account(db, account_id=account.id, project_id=project.id,
                         by=admin.username)
    log_activity(db, user=admin, action="pipeline_account_created",
                 target_type="upload_account", target_id=account.id,
                 details={"name": name, "target": account.target_site})
    db.commit()
    return JSONResponse({"ok": True, "account_id": account.id})


@router.post("/api/accounts/{account_id}")
def api_update_account(
    account_id: int,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update an account. A blank/absent password leaves the stored one intact,
    so editing timings doesn't require retyping credentials.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")

    if payload.get("name"):
        account.name = payload["name"].strip()
    if payload.get("email"):
        account.email = payload["email"].strip()
    if payload.get("password"):
        account.password_enc = P.encrypt_secret(payload["password"])
    if "profile_url" in payload:
        account.profile_url = (payload.get("profile_url") or "").strip() or None
    if "chrome_profile_dir" in payload:
        account.chrome_profile_dir = (payload.get("chrome_profile_dir") or "").strip() or None
    if "target_site" in payload and payload["target_site"]:
        account.target_site = payload["target_site"].strip()
    if "daily_limit" in payload:
        account.daily_limit = int(payload["daily_limit"] or 100)
    if "rotation_order" in payload:
        account.rotation_order = int(payload["rotation_order"] or 100)
    if "rotation_size" in payload:
        account.rotation_size = (int(payload["rotation_size"])
                                 if payload["rotation_size"] else None)
    if "is_enabled" in payload:
        account.is_enabled = 1 if payload["is_enabled"] else 0
    if "timings" in payload:
        account.timing_json = json.dumps(payload["timings"]) if payload["timings"] else None
    if "selectors" in payload:
        account.selectors_json = json.dumps(payload["selectors"]) if payload["selectors"] else None

    log_activity(db, user=admin, action="pipeline_account_updated",
                 target_type="upload_account", target_id=account.id)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/accounts/{account_id}/resume")
def api_resume_account(
    account_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Clear a pause set by the node after a bot-check or login failure."""
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")
    account.paused_until = None
    account.pause_reason = None
    log_activity(db, user=admin, action="pipeline_account_resumed",
                 target_type="upload_account", target_id=account.id)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/run_mode")
def api_set_run_mode(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stop or resume this project's pipeline.

    'drain' and 'halt' both stop NEW work being handed out and both let work
    already claimed finish — the difference is only what the dashboard tells
    you, and that difference matters at 2am when you are trying to remember
    whether you stopped this on purpose.

    There is no mode that abandons work in flight. An image mid-generation
    has already been paid for and an image mid-upload is halfway into a form;
    stopping the intake and waiting a few minutes is always cheaper than
    unpicking either.
    """
    mode = str(payload.get("mode") or "run").strip()
    if mode not in ("run", "drain", "halt"):
        raise HTTPException(400, "mode must be run, drain or halt.")

    project = _project(request, admin, db, payload.get("project_id"))
    reason = (payload.get("reason") or "").strip()

    P.set_setting(db, "run_mode", mode, project=project, by=admin.username)
    P.set_setting(db, "run_mode_reason",
                  "" if mode == "run" else reason, project=project, by=admin.username)

    log_activity(db, user=admin, action="pipeline_run_mode", target_type="pipeline",
                 details={"mode": mode, "reason": reason, "project": project.slug})
    db.commit()

    # What is still out there, so the UI can say "draining — 3 to go" rather
    # than implying everything has already stopped.
    in_flight = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.deleted_at.is_(None), _title_scope(db, project))
          .scalar() or 0
    ) + (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.project_id == project.id)
          .scalar() or 0
    )
    return JSONResponse({"ok": True, "mode": mode, "in_flight": in_flight})


@router.post("/api/accounts/{account_id}/ban")
def api_ban_account(
    account_id: int,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Record that a marketplace has closed this account.

    This is the destructive one, and the destruction has already happened
    elsewhere — the button only makes the database agree with reality. What
    it changes is that several thousand rows stop claiming to be live on a
    site where the listings no longer exist.

    A reason is required. In a year the only thing distinguishing "banned for
    copyright" from "banned for uploading too fast" will be this field, and
    they lead to completely different decisions about the replacement.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")
    if account.banned_at is not None:
        raise HTTPException(400, f"{account.name} is already marked banned.")

    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "A reason is required — it is the only record of why.")

    counts = P.ban_account(db, account, reason=reason, by=admin.username)
    log_activity(db, user=admin, action="pipeline_account_banned",
                 target_type="upload_account", target_id=account.id,
                 details={"reason": reason, **counts})
    db.commit()
    return JSONResponse({"ok": True, "account": account.name, **counts})


@router.post("/api/accounts/{account_id}/handover")
def api_handover_account(
    account_id: int,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Rebuild a banned account's catalogue on a replacement account.

    Separate from the ban on purpose: the replacement usually does not exist
    yet when the ban is discovered, and forcing both decisions at once would
    mean either delaying the ban or picking the wrong destination.

    Safe to run more than once — the underlying requeue skips images the
    target already has a row for, so a second press adds only what is new.
    """
    replacement_id = payload.get("replacement_id")
    if not replacement_id:
        raise HTTPException(400, "replacement_id is required.")

    try:
        counts = P.hand_over_account(db, dead_id=account_id,
                                     replacement_id=int(replacement_id))
    except ValueError as e:
        raise HTTPException(400, str(e))

    log_activity(db, user=admin, action="pipeline_account_handover",
                 target_type="upload_account", target_id=account_id,
                 details={"replacement_id": replacement_id, **counts})
    db.commit()
    return JSONResponse({"ok": True, **counts})


@router.post("/api/accounts/{account_id}/requeue")
def api_requeue_account(
    account_id: int,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Queue the processed back catalogue onto this account — the ban-recovery
    path.

    Pass `source_account_id` to mirror only what a specific dead account had
    live, so a replacement rebuilds exactly that catalogue. No reprocessing
    happens: everything is already in storage.
    """
    try:
        created = P.requeue_for_account(
            db, account_id=account_id,
            source_account_id=payload.get("source_account_id"),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    log_activity(db, user=admin, action="pipeline_account_requeued",
                 target_type="upload_account", target_id=account_id,
                 details={"queued": created})
    db.commit()
    return JSONResponse({"ok": True, "queued": created})


@router.post("/api/accounts/{account_id}/delete")
def api_delete_account(
    account_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete an account, and release the work that was queued against it.

    FINISHED history is preserved — uploaded / removed / skipped rows are the
    record of what went live where, and that record is what makes rebuilding
    onto a replacement account possible.

    UNFINISHED rows are discarded, and this is the important half. A queued
    row is only ever handed out by walking the list of live, enabled accounts
    (`claim_upload_batch`), so once its account no longer exists the row can
    never be claimed by anything — while the poster it belongs to sits at
    'uploading' forever, counted as in-flight on the funnel. Deleting an
    account to re-add it therefore used to strand every design that had
    reached the upload stage, permanently and silently.

    The posters go back to 'processed', which is the state the greenlight and
    REQUEUE BACK CATALOGUE paths both seed from, so adding the replacement
    account picks them straight back up.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")
    name = account.name

    unfinished = (
        db.query(UploadTracking)
          .filter(UploadTracking.account_id == account_id,
                  UploadTracking.status.in_(
                      ("pending", "uploading", "failed")))
          .all()
    )
    released = 0
    for row in unfinished:
        poster = db.query(SavedPoster).filter_by(id=row.saved_poster_id).first()
        if poster is not None and poster.pipeline_status in ("uploading", "failed_upload"):
            poster.pipeline_status = "processed"
            released += 1
        db.delete(row)

    # ── Earnings rows go with it ─────────────────────────────────────────
    # A ledger row belongs to the ACCOUNT it was read from. Leaving them
    # behind was how deleting the duplicate FineArtAmerica account would have
    # left its 70 sales in the totals forever — every figure on the Earnings
    # page double-counted, with nothing on screen to explain it.
    #
    # Safe to delete rather than soft-delete: unlike a poster, nothing here is
    # our own work. It is a copy of a page on someone else's site and one
    # READ NOW brings it all back.
    ledger_rows = (
        db.query(LedgerEntry)
          .filter(LedgerEntry.account_id == account_id).delete()
    )
    # NOTE: title aliases are deliberately NOT deleted. An alias belongs to a
    # MARKETPLACE, not to an account — another account on the same site still
    # needs it, and re-teaching those matches by hand would be real work lost.

    # Which projects it served, and anything pointing at it.
    db.query(AccountProject).filter(
        AccountProject.account_id == account_id).delete()
    db.query(UploadAccount).filter(
        UploadAccount.replaced_by_id == account_id
    ).update({"replaced_by_id": None}, synchronize_session=False)

    # ── Ask the node to remove this account's Chrome profile ─────────────
    #
    # The website cannot reach the node's disk, so this is a job like any
    # other. Queued BEFORE the row disappears, because the folder name is
    # derived from the account's id and name and there is no way to work it
    # out afterwards.
    #
    # Deliberately names one folder rather than telling the node to tidy up.
    # The worst this can do is leave a folder behind — which is where things
    # already stood — whereas "delete anything that looks unused" could wipe
    # live sessions if it were ever told the wrong thing.
    P.create_job(db, kind="profile_cleanup", requested_by=admin.username,
                 payload={"account_id": account_id, "name": name,
                          "chrome_profile_dir": account.chrome_profile_dir or ""})

    db.delete(account)
    log_activity(db, user=admin, action="pipeline_account_deleted",
                 target_type="upload_account", target_id=account_id,
                 details={"name": name,
                          "queued_rows_discarded": len(unfinished),
                          "posters_released": released,
                          "earnings_rows_removed": ledger_rows})
    db.commit()
    return JSONResponse({"ok": True, "released": released,
                         "discarded": len(unfinished),
                         "earnings_removed": ledger_rows})


# ═══════════════════════════════════════════════════════════════════════════
#  FAILURES
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/inflight/release")
def api_release_inflight(
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Hand claimed work back to the queue immediately.

    The dispatcher reaps stale claims automatically, but only after
    `claim_timeout_min` AND only when a node next asks for work. If you close
    the agent window mid-batch — or a node dies — those items sit visibly
    "processing" with nothing working on them until both conditions are met.
    Waiting 45 minutes to undo a Ctrl+C is not a reasonable operator
    experience, so this releases them on demand.

    Safe by construction: it only touches rows still in a *claimed* state
    (processing / uploading). Anything that actually completed has already
    moved on and is left alone.

    Pass `all_stale: true` to release everything past the timeout, or explicit
    `poster_ids` / `tracking_ids`.
    """
    released_posters = released_uploads = 0
    now = datetime.utcnow()

    if payload.get("all_stale"):
        timeout = int(P.get_setting(db, "claim_timeout_min"))
        cutoff = now - timedelta(minutes=timeout)
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.pipeline_status == "processing",
                      SavedPoster.claimed_at.isnot(None),
                      SavedPoster.claimed_at < cutoff)
              .all()
        )
        rows = (
            db.query(UploadTracking)
              .filter(UploadTracking.status == "uploading",
                      UploadTracking.claimed_at.isnot(None),
                      UploadTracking.claimed_at < cutoff)
              .all()
        )
    else:
        poster_ids = payload.get("poster_ids") or []
        tracking_ids = payload.get("tracking_ids") or []
        if not poster_ids and not tracking_ids:
            raise HTTPException(400, "Provide poster_ids, tracking_ids, or all_stale.")
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.id.in_(poster_ids),
                      SavedPoster.pipeline_status == "processing")
              .all()
        ) if poster_ids else []
        rows = (
            db.query(UploadTracking)
              .filter(UploadTracking.id.in_(tracking_ids),
                      UploadTracking.status == "uploading")
              .all()
        ) if tracking_ids else []

    for poster in posters:
        # Back to greenlit, not failed — nothing went wrong with the image,
        # it just lost its worker. Attempts are untouched so this can't be
        # used to dodge the retry cap.
        poster.pipeline_status = "greenlit"
        poster.claimed_at = None
        poster.claimed_by = None
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title:
            P.recompute_title_status(db, title)
        released_posters += 1

    for row in rows:
        row.status = "pending"
        row.claimed_at = None
        row.claimed_by = None
        poster = db.query(SavedPoster).filter_by(id=row.saved_poster_id).first()
        if poster is not None and poster.pipeline_status == "uploading":
            poster.pipeline_status = "processed"
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                P.recompute_title_status(db, title)
        released_uploads += 1

    log_activity(db, user=admin, action="pipeline_release_claims",
                 target_type="pipeline",
                 details={"posters": released_posters, "uploads": released_uploads})
    db.commit()
    return JSONResponse({
        "ok": True,
        "released_posters": released_posters,
        "released_uploads": released_uploads,
    })


@router.get("/api/failures")
def api_failures(
    request: Request,
    kind: str = Query("upload"),
    project_id: Optional[int] = Query(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Failure list with everything needed to diagnose without leaving the page:
    the error text, attempt count, and the failure screenshot the node
    captured at the moment things broke.
    """
    project = _project(request, admin, db, project_id)

    if kind == "processing":
        rows = (
            db.query(SavedPoster, MasterTitle)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.pipeline_status == "failed_processing",
                      SavedPoster.deleted_at.is_(None),
                      _title_scope(db, project))
              .order_by(SavedPoster.claimed_at.desc().nullslast())
              .limit(200)
              .all()
        )
        max_attempts = int(P.get_setting(db, "process_max_attempts", project=project))
        return JSONResponse({
            "ok": True, "kind": "processing",
            "items": [
                {
                    "poster_id": p.id, "master_id": t.id,
                    "title": t.title, "year": t.year,
                    "filename": p.filename,
                    "attempts": p.process_attempts,
                    "exhausted": (p.process_attempts or 0) >= max_attempts,
                    "error": p.process_error,
                    "save_date": p.original_save_date.isoformat() if p.original_save_date else None,
                }
                for p, t in rows
            ],
        })

    rows = (
        db.query(UploadTracking, SavedPoster, MasterTitle, UploadAccount)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .join(UploadAccount, UploadTracking.account_id == UploadAccount.id)
          .filter(UploadTracking.status == "failed",
                  UploadTracking.project_id == project.id)
          .order_by(UploadTracking.claimed_at.desc().nullslast())
          .limit(200)
          .all()
    )
    max_attempts = int(P.get_setting(db, "upload_max_attempts", project=project))
    return JSONResponse({
        "ok": True, "kind": "upload",
        "items": [
            {
                "tracking_id": tr.id, "poster_id": p.id, "master_id": t.id,
                "title": t.title, "year": t.year,
                "remote_title": tr.remote_title,
                "account": acc.name, "account_id": acc.id,
                "target_site": tr.target_site,
                "attempts": tr.attempts,
                "exhausted": (tr.attempts or 0) >= max_attempts,
                "error": tr.last_error,
                "screenshot": tr.last_screenshot,
            }
            for tr, p, t, acc in rows
        ],
    })


@router.post("/api/failures/retry")
def api_retry_failures(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Requeue failures. Attempt counters reset, so an exhausted row gets a real
    fresh start after you've fixed whatever broke it.
    """
    kind = payload.get("kind", "upload")

    if kind == "processing":
        poster_ids = payload.get("poster_ids") or []
        if not poster_ids:
            raise HTTPException(400, "poster_ids is required.")
        posters = db.query(SavedPoster).filter(SavedPoster.id.in_(poster_ids)).all()
        for poster in posters:
            poster.pipeline_status = "greenlit"
            poster.process_attempts = 0
            poster.process_error = None
            poster.claimed_at = None
            poster.claimed_by = None
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                P.recompute_title_status(db, title)
        db.commit()
        return JSONResponse({"ok": True, "requeued": len(posters)})

    tracking_ids = payload.get("tracking_ids") or []
    if not tracking_ids:
        raise HTTPException(400, "tracking_ids is required.")
    count = P.retry_uploads(db, tracking_ids)
    log_activity(db, user=admin, action="pipeline_retry", target_type="pipeline",
                 details={"kind": kind, "count": count})
    db.commit()
    return JSONResponse({"ok": True, "requeued": count})


@router.post("/api/failures/skip")
def api_skip_failures(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Permanently exclude items from the pipeline — for images that genuinely
    shouldn't be published rather than ones that failed transiently.
    """
    if payload.get("tracking_ids"):
        rows = (
            db.query(UploadTracking)
              .filter(UploadTracking.id.in_(payload["tracking_ids"]))
              .all()
        )
        for row in rows:
            row.status = "skipped"
            row.claimed_at = None
            row.claimed_by = None
        # RETRY next door logs and this did not — and this is the one that
        # cannot be undone. Without a record, "why was this never uploaded"
        # has no answer at all.
        log_activity(db, user=admin, action="pipeline_skip", target_type="pipeline",
                     details={"kind": "upload", "count": len(rows),
                              "tracking_ids": payload["tracking_ids"][:50]})
        db.commit()
        return JSONResponse({"ok": True, "skipped": len(rows)})

    if payload.get("poster_ids"):
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.id.in_(payload["poster_ids"]))
              .all()
        )
        for poster in posters:
            poster.pipeline_status = "skipped"
            poster.claimed_at = None
            poster.claimed_by = None
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                P.recompute_title_status(db, title)
        log_activity(db, user=admin, action="pipeline_skip", target_type="pipeline",
                     details={"kind": "process", "count": len(posters),
                              "poster_ids": payload["poster_ids"][:50]})
        db.commit()
        return JSONResponse({"ok": True, "skipped": len(posters)})

    raise HTTPException(400, "Provide tracking_ids or poster_ids.")


@router.post("/api/failures/mark_removed")
def api_mark_removed(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Record a marketplace takedown (copyright/DMCA).

    Distinct from 'failed' because it's a permanent outcome for that account
    and must not be retried — but the processed file stays in storage so the
    image can still be listed elsewhere.
    """
    tracking_ids = payload.get("tracking_ids") or []
    if not tracking_ids:
        raise HTTPException(400, "tracking_ids is required.")
    count = P.mark_removed(db, tracking_ids, reason=payload.get("reason", ""))
    log_activity(db, user=admin, action="pipeline_mark_removed",
                 target_type="pipeline", details={"count": count})
    db.commit()
    return JSONResponse({"ok": True, "marked": count})


@router.get("/api/artifacts")
def api_artifacts(
    limit: int = 40,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The newest screenshots and page dumps, newest first.

    A plain listing of the folder, deliberately, rather than a lookup keyed on
    an upload row. Evidence is captured by anything that drives a browser —
    including an earnings read for an account attached to no project — and
    those were being written to disk with nothing on any screen pointing at
    them. A file nobody can find is a file that was not really saved.
    """
    base = (WORKSPACE_DIR / "_pipeline_artifacts").resolve()
    if not base.is_dir():
        return JSONResponse({"ok": True, "artifacts": []})

    rows = []
    for kind_dir in base.iterdir():
        if not kind_dir.is_dir():
            continue
        for f in kind_dir.iterdir():
            if not f.is_file():
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            rows.append({
                "kind": kind_dir.name,
                "name": f.name,
                "path": f"_pipeline_artifacts/{kind_dir.name}/{f.name}",
                "bytes": stat.st_size,
                "when": datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
            })

    rows.sort(key=lambda r: r["when"], reverse=True)
    return JSONResponse({"ok": True, "artifacts": rows[:max(1, min(limit, 200))]})


@router.get("/api/artifact")
def api_artifact(
    path: str = Query(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Serve a failure screenshot / page dump.

    Confined to the artifacts directory and resolved before comparison, so a
    crafted `path` can't escape into the rest of the filesystem.
    """
    base = (WORKSPACE_DIR / "_pipeline_artifacts").resolve()
    target = (WORKSPACE_DIR / path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(403, "Path outside the artifact directory.")
    if not target.is_file():
        raise HTTPException(404, "Artifact not found.")
    return FileResponse(target)


# ═══════════════════════════════════════════════════════════════════════════
#  TEST & DEBUG
# ═══════════════════════════════════════════════════════════════════════════

# NOTE: the two literal /api/test routes below MUST stay above
# /api/test/{kind}. FastAPI matches in declaration order, so the
# wildcard would otherwise capture "gpt_process" as a kind and reject
# it as invalid — a 400 that reads like the test is broken.
@router.post("/api/test/gpt_process")
def api_test_gpt_process(
    request: Request,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Run the image model on ONE image, right now, and hand back the result.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS NOT /api/test/process
    ════════════════════════════════════════════════════════════════════════
    That one queues a job for the Windows node, because Photoshop lives
    there. Generation lives HERE, in this process. Routing a GPT test through
    the node queue would mean waiting for a machine that has no part in the
    stage being tested — and would report "no node available" as though the
    thing you were testing were broken.

    So it runs inline and answers in one request. Roughly 60 seconds, which
    is why the browser is told not to give up early.

    ════════════════════════════════════════════════════════════════════════
    WHAT IT DELIBERATELY DOES NOT DO
    ════════════════════════════════════════════════════════════════════════
    No ProcessedImage row, no change to the poster's state, no review-gate
    entry. Output goes under a `_tests/` prefix. You can run it fifty times
    on the same image while tuning the prompt and the pipeline neither
    notices nor ships any of it.

    It DOES record the spend, because the money is just as real as in a batch
    run and a test that quietly under-reports cost is worse than no test.
    """
    from .. import gpt_images as G
    from ..config import WORKSPACE_DIR
    from ..imagefetch import make_preview, upscale_to_width
    from ..storage_remote import StorageError, write_bytes
    from ..utils import saved_poster_path

    poster_id = payload.get("poster_id")
    if not poster_id:
        raise HTTPException(400, "poster_id is required.")

    project = _project(request, admin, db, payload.get("project_id"))
    if project.processor != "gpt":
        raise HTTPException(400, f"{project.name} does not use image generation.")

    poster = db.query(SavedPoster).filter_by(id=poster_id).first()
    if poster is None or poster.deleted_at is not None:
        raise HTTPException(404, "No such image.")

    title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
    if title is None:
        raise HTTPException(404, "That image has no title attached.")
    # A test that silently reaches into another project would be a very
    # confusing way to discover the settings you are tuning are not the ones
    # being applied.
    owner = P.project_for_title(db, title)
    if owner.id != project.id:
        raise HTTPException(400, f"Image {poster_id} belongs to {owner.name}, not {project.name}.")

    lines: list[str] = []

    def emit(msg, level="info"):
        lines.append(f"[{level}] {msg}")

    style_rel = str(P.get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / style_rel if style_rel else Path("")
    source = saved_poster_path(poster)
    emit(f"source: {source.name}")

    started = datetime.utcnow()
    try:
        gen = G.generate(db, source=source, style=style, project=project,
                         location=(title.title or ""), log_fn=emit)
    except G.PermanentFailure as e:
        return JSONResponse({"ok": False, "fatal": True, "kind": e.kind,
                             "categories": getattr(e, "categories", []),
                             "error": str(e), "log": lines}, status_code=200)
    except G.TransientFailure as e:
        return JSONResponse({"ok": False, "fatal": False,
                             "error": str(e), "log": lines}, status_code=200)

    G.record_spend(db, service="openai", operation="test_image_edit",
                   cost=gen.cost_usd(), project_id=project.id,
                   saved_poster_id=poster.id,
                   input_tokens=gen.input_tokens, output_tokens=gen.output_tokens)
    db.commit()
    emit(f"generated in {gen.duration_ms} ms, ${gen.cost_usd():.4f}")

    tmp = WORKSPACE_DIR / "_gpt_tmp" / f"test_{poster.id}.jpg"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(gen.image_bytes)

    width = int(P.get_setting(db, "upscale_width_px", project=project) or 4000)
    sharpen = int(P.get_setting(db, "upscale_sharpen", project=project) or 0)
    quality = int(P.get_setting(db, "upscale_jpeg_quality", project=project) or 92)
    out_w, out_h = upscale_to_width(tmp, width=width, sharpen=sharpen, quality=quality)
    emit(f"upscaled to {out_w}x{out_h} (sharpen {sharpen}, quality {quality})")

    preview_tmp = tmp.with_name(f"test_{poster.id}_preview.jpg")
    make_preview(tmp, preview_tmp)

    rel = f"_tests/{project.slug}/{poster.id}.jpg"
    preview_rel = f"_tests/{project.slug}/{poster.id}_preview.jpg"
    stored = True
    try:
        write_bytes(db, rel, tmp.read_bytes(), project=project)
        write_bytes(db, preview_rel, preview_tmp.read_bytes(), project=project)
        emit(f"written to {rel}")
    except StorageError as e:
        # Reported, not raised. The generation worked and you paid for it;
        # a storage problem is a separate finding and shouldn't read as
        # "the prompt failed".
        stored = False
        emit(f"storage failed: {e}", level="error")
    finally:
        tmp.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)

    return JSONResponse({
        "ok": True,
        "stored": stored,
        "width": out_w, "height": out_h,
        "bytes": len(gen.image_bytes),
        "duration_ms": gen.duration_ms,
        "total_ms": int((datetime.utcnow() - started).total_seconds() * 1000),
        # float(), not the Decimal itself — cost is carried as Decimal so the
        # spend ledger stays exact, but JSON has no Decimal.
        "cost_usd": round(float(gen.cost_usd()), 4),
        "input_tokens": gen.input_tokens, "output_tokens": gen.output_tokens,
        "preview_path": preview_rel if stored else None,
        "log": lines,
    })


@router.post("/api/test/{kind}")
def api_test(
    request: Request,
    kind: str,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Queue a single-stage diagnostic and return its job id to tail.

    This is the answer to "I don't want to run the whole pipeline to find out
    if one fix worked". Each kind exercises exactly one stage on exactly one
    item, using the same settings resolution a real batch would:

      test_download — pull one title's sources to the node
      test_process  — run the current JSX on one image, output to _tests/
      test_upload   — walk one image through login → form → submit,
                      logging each phase, without mutating tracking state

    Test jobs are prioritised over batch work so you get an answer in
    seconds rather than behind a queue.
    """
    if kind not in ("download", "process", "upload"):
        raise HTTPException(400, "kind must be download, process or upload.")

    job_kind = f"test_{kind}"
    project = _project(request, admin, db, payload.get("project_id"))

    if kind == "download":
        if not payload.get("master_id"):
            raise HTTPException(400, "master_id is required.")
    elif kind == "process":
        if not payload.get("poster_id"):
            raise HTTPException(400, "poster_id is required.")
    else:
        if not payload.get("tracking_id") and not (
            payload.get("poster_id") and payload.get("account_id")
        ):
            raise HTTPException(400, "Provide tracking_id, or poster_id + account_id.")

    job = P.create_job(
        db, kind=job_kind, payload=payload,
        project_id=project.id, requested_by=admin.username,
    )
    P.append_job_log(db, job, f"Queued {job_kind} by {admin.username}")
    db.commit()
    return JSONResponse({"ok": True, "job_id": job.id})


@router.get("/api/test/image")
def api_test_image(
    path: str = Query(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stream a test output back to the browser.

    Confined to the `_tests/` prefix. Without that check this would be an
    admin-authenticated read of any path on the Storage Box, which is a
    bigger door than a preview button needs.
    """
    from ..storage_remote import read_bytes, StorageError

    clean = (path or "").replace("\\", "/").lstrip("/")
    if not clean.startswith("_tests/") or ".." in clean:
        raise HTTPException(403, "Only test output can be served here.")
    try:
        data = read_bytes(db, clean)
    except StorageError as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=60"})


@router.get("/api/jobs")
def api_jobs(
    limit: int = Query(25, ge=1, le=100),
    kind: str = Query(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(PipelineJob)
    if kind:
        query = query.filter(PipelineJob.kind == kind)
    jobs = query.order_by(PipelineJob.created_at.desc()).limit(limit).all()
    return JSONResponse({"ok": True, "jobs": [_job_summary(j) for j in jobs]})


@router.get("/api/jobs/{job_id}")
def api_job_detail(
    job_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Full job detail including the log — what the Live Console polls.

    Returns the whole log each time rather than a delta: logs are capped at
    200KB and this keeps the client trivial and immune to missed offsets.
    """
    job = db.query(PipelineJob).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(404, "Job not found.")

    result = None
    if job.result_json:
        try:
            result = json.loads(job.result_json)
        except (TypeError, ValueError):
            result = {"raw": job.result_json}

    payload = None
    if job.payload_json:
        try:
            payload = json.loads(job.payload_json)
        except (TypeError, ValueError):
            payload = None
    # Credentials can appear in a resolved test payload; never echo them back
    # to a browser even for an admin.
    if isinstance(payload, dict):
        payload.pop("account", None)

    return JSONResponse({
        "ok": True,
        **_job_summary(job),
        "log": job.log_text or "",
        "result": result,
        "payload": payload,
    })


@router.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(
    request: Request,
    job_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Cancel a job. A queued job will simply never be claimed; a running one is
    marked cancelled and the node's next report is ignored.
    """
    job = db.query(PipelineJob).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.status in ("done", "error"):
        raise HTTPException(400, "Job already finished.")
    job.status = "cancelled"
    job.finished_at = datetime.utcnow()
    P.append_job_log(db, job, f"Cancelled by {admin.username}", level="warn")
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/run")
def api_trigger_run(
    request: Request,
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Ask a node to start a batch now rather than waiting for its schedule.

    Purely a convenience: the node would pick this work up on its own poll.
    Queuing a job makes the request visible and gives the run a log to tail.
    """
    kind = payload.get("kind", "process")
    if kind not in ("process", "upload"):
        raise HTTPException(400, "kind must be process or upload.")
    project = _project(request, admin, db, payload.get("project_id"))
    job = P.create_job(db, kind=kind, payload=payload,
                       project_id=project.id, requested_by=admin.username)
    P.append_job_log(db, job, f"Manual {kind} run requested by {admin.username}")
    db.commit()
    return JSONResponse({"ok": True, "job_id": job.id})


# ═══════════════════════════════════════════════════════════════════════════
#  WORKER NODES
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/nodes")
def api_create_node(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Register a worker node and return its token.

    The token is shown exactly once — only its hash is stored. Copy it into
    the node's config; if it's lost, rotate rather than recover.
    """
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required.")
    if db.query(WorkerNode).filter_by(name=name).first():
        raise HTTPException(400, f"A node named '{name}' already exists.")

    capabilities = payload.get("capabilities") or ["process", "upload"]
    if isinstance(capabilities, str):
        capabilities = [capabilities]

    node, token = P.create_node(db, name=name, capabilities=",".join(capabilities))
    log_activity(db, user=admin, action="pipeline_node_created",
                 target_type="worker_node", target_id=node.id,
                 details={"name": name, "capabilities": capabilities})
    db.commit()
    return JSONResponse({
        "ok": True, "node_id": node.id, "token": token,
        "note": "Copy this token now — it is not stored and cannot be shown again.",
    })


@router.post("/api/nodes/{node_id}/rotate")
def api_rotate_node(
    node_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    node = db.query(WorkerNode).filter_by(id=node_id).first()
    if node is None:
        raise HTTPException(404, "Node not found.")
    token = P.rotate_node_token(db, node)
    log_activity(db, user=admin, action="pipeline_node_rotated",
                 target_type="worker_node", target_id=node.id)
    db.commit()
    return JSONResponse({"ok": True, "token": token})


@router.post("/api/nodes/{node_id}")
def api_update_node(
    node_id: int,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    node = db.query(WorkerNode).filter_by(id=node_id).first()
    if node is None:
        raise HTTPException(404, "Node not found.")
    if "is_enabled" in payload:
        node.is_enabled = 1 if payload["is_enabled"] else 0
    if payload.get("capabilities"):
        caps = payload["capabilities"]
        node.capabilities = ",".join(caps) if isinstance(caps, list) else str(caps)
    # Its siblings — ban, delete and update ACCOUNT — all log. This did not,
    # and switching a node off or taking a capability away from it stops work
    # happening with nothing on record saying why.
    log_activity(db, user=admin, action="node_updated", target_type="node",
                 target_id=node.id,
                 details={"name": node.name, "is_enabled": node.is_enabled,
                          "capabilities": node.capabilities})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/nodes/{node_id}/delete")
def api_delete_node(
    node_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    node = db.query(WorkerNode).filter_by(id=node_id).first()
    if node is None:
        raise HTTPException(404, "Node not found.")
    db.delete(node)
    log_activity(db, user=admin, action="pipeline_node_deleted",
                 target_type="worker_node", target_id=node_id)
    db.commit()
    return JSONResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
#  PROJECTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/projects")
def api_create_project(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a niche.

    This is the multi-workflow entry point: a new project starts by
    inheriting every global setting, so you only override the parts that
    differ (source site, JSX, keyword list, images per title). No schema
    change, no code change.
    """
    slug = (payload.get("slug") or "").strip().lower()
    name = (payload.get("name") or "").strip()
    if not slug or not name:
        raise HTTPException(400, "slug and name are required.")
    if not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "slug must be alphanumeric with - or _ only.")
    if db.query(Project).filter_by(slug=slug).first():
        raise HTTPException(400, f"Project '{slug}' already exists.")

    project = Project(
        slug=slug,
        name=name,
        source_site=(payload.get("source_site") or "").strip() or None,
        images_per_title=int(payload["images_per_title"]) if payload.get("images_per_title") else None,
        notes=(payload.get("notes") or "").strip() or None,
    )
    db.add(project)
    db.flush()
    log_activity(db, user=admin, action="pipeline_project_created",
                 target_type="project", target_id=project.id,
                 details={"slug": slug, "name": name})
    db.commit()
    return JSONResponse({"ok": True, "project_id": project.id, "slug": slug})


# ═══════════════════════════════════════════════════════════════════════════
#  GPT PROJECTS — prompt, style reference, spend
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/api/gpt")
def api_gpt_state(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Everything the PROCESSING tab needs for a GPT project in one call."""
    from ..gpt_images import cap_state, month_to_date_usd
    project = _project(request, admin, db)
    style = str(P.get_setting(db, "openai_style_image", project=project) or "")
    state = cap_state(db, project=project)
    return JSONResponse({
        "prompt": P.get_setting(db, "openai_prompt", project=project),
        "style_image": style,
        "style_url": f"/admin/pipeline/style_image?v={int(datetime.utcnow().timestamp())}" if style else "",
        # The cache-buster matters: the file keeps ONE name per project, so
        # without it the browser shows the picture you replaced.
        "signature_url": (f"/admin/pipeline/signature_image"
                          f"?v={int(datetime.utcnow().timestamp())}"
                          if str(P.get_setting(db, "signature_image",
                                               project=project) or "") else ""),
        "spend": {
            "month_to_date": str(state["spent"]),
            "cap": str(state["cap"]),
            "over": state["over"],
            "action": state["action"],
            "openai": str(month_to_date_usd(db, "openai")),
            "brave": str(month_to_date_usd(db, "brave")),
        },
    })


@router.post("/api/gpt/prompt")
def api_save_gpt_prompt(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Save the generation prompt for this project.

    Editing it does NOT affect images already processed — each ProcessedImage
    records the model it was made with, and a rerun is the only thing that
    regenerates.
    """
    project = _project(request, admin, db)
    text = (payload.get("prompt") or "").strip()
    if not text:
        raise HTTPException(400, "The prompt cannot be empty.")
    P.set_setting(db, "openai_prompt", text, project=project, by=admin.username)
    log_activity(db, user=admin, action="pipeline_setting", target_type="pipeline",
                 details={"key": "openai_prompt", "project": project.slug})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/gpt/style")
async def api_upload_style(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Replace the style reference sent as the FIRST image on every request.

    Stored under the workspace rather than on the Storage Box because it is
    read on every single generation — a local file avoids an SFTP round trip
    per image, and it is small enough that backups don't care.
    """
    from ..imagefetch import sniff_format
    from ..config import WORKSPACE_DIR

    project = _project(request, admin, db)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")
    if sniff_format(raw[:16]) is None:
        raise HTTPException(400, "That file is not an image.")

    rel = f"_style/{project.slug}.png"
    target = WORKSPACE_DIR / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    P.set_setting(db, "openai_style_image", rel, project=project, by=admin.username)
    log_activity(db, user=admin, action="pipeline_setting", target_type="pipeline",
                 details={"key": "openai_style_image", "project": project.slug,
                          "bytes": len(raw)})
    db.commit()
    return JSONResponse({"ok": True, "path": rel})


@router.post("/api/gpt/signature")
async def api_upload_signature(
    request: Request,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Replace the signature painted onto every approved print file.

    Stored under the workspace beside the style reference, and for the same
    reason: it is opened on every build, so a local file avoids an SFTP
    round trip per poster and it is far too small for backups to care.

    Kept as PNG whatever arrives, because the transparency is the whole
    point — a JPEG signature would paint its own white box onto the poster.
    """
    from ..imagefetch import sniff_format
    from ..config import WORKSPACE_DIR

    project = _project(request, admin, db)
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")
    if sniff_format(raw[:16]) is None:
        raise HTTPException(400, "That file is not an image.")

    # REFUSED IF IT IS NOT SEE-THROUGH, and refusing is the kind answer. A
    # signature with a solid background paints a rectangle over the corner
    # of every poster, and the first time anybody would notice is on the
    # marketplace. The check is cheap and the message says what to do.
    from PIL import Image
    import io
    try:
        probe = Image.open(io.BytesIO(raw))
        probe.load()
    except Exception as e:              # noqa: BLE001
        raise HTTPException(400, f"That image could not be read: {e}")
    if probe.mode not in ("RGBA", "LA", "PA") and "transparency" not in probe.info:
        raise HTTPException(
            400, "That picture has no see-through background, so it would "
                 "paint a solid rectangle over the corner of every poster. "
                 "Save it as a PNG with transparency and upload it again.")

    out = io.BytesIO()
    probe.convert("RGBA").save(out, "PNG")

    rel = f"_signature/{project.slug}.png"
    target = WORKSPACE_DIR / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(out.getvalue())

    P.set_setting(db, "signature_image", rel, project=project, by=admin.username)
    log_activity(db, user=admin, action="pipeline_setting", target_type="pipeline",
                 details={"key": "signature_image", "project": project.slug,
                          "bytes": len(raw),
                          "size": f"{probe.width}x{probe.height}"})
    db.commit()
    return JSONResponse({"ok": True, "path": rel,
                         "width": probe.width, "height": probe.height})


@router.get("/signature_image")
def serve_signature_image(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..config import WORKSPACE_DIR
    project = _project(request, admin, db)
    rel = str(P.get_setting(db, "signature_image", project=project) or "")
    if not rel:
        raise HTTPException(404, "No signature set.")
    path = WORKSPACE_DIR / rel
    if not path.is_file():
        raise HTTPException(404, "The signature file is missing.")
    return FileResponse(path)


@router.get("/style_image")
def serve_style_image(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    from ..config import WORKSPACE_DIR
    project = _project(request, admin, db)
    rel = str(P.get_setting(db, "openai_style_image", project=project) or "")
    if not rel:
        raise HTTPException(404, "No style reference set.")
    path = WORKSPACE_DIR / rel
    if not path.is_file():
        raise HTTPException(404, "Style reference file is missing.")
    return FileResponse(path)


@router.get("/api/spend")
def api_spend(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Day-by-day spend, newest first, with today's figure first in the list.

    Computed from our own metering — for OpenAI that is real token usage the
    API reported, for Brave it is query count x the configured rate and is
    flagged as estimated.
    """
    from .. import gpt_images as G
    from .. import openai_costs as OC
    from ..models import ApiSpend

    project = _project(request, admin, db)

    since = datetime.utcnow().date() - timedelta(days=days - 1)
    rows = (
        db.query(ApiSpend)
          .filter(ApiSpend.created_at >= datetime.combine(since, datetime.min.time()),
                  # Scoped like everything else on this page. Unscoped, MUSIK
                  # would be shown the movie project's bill.
                  ApiSpend.project_id == project.id)
          .all()
    )
    by_day: dict[str, dict] = {}
    for r in rows:
        key = r.created_at.date().isoformat()
        bucket = by_day.setdefault(key, {"date": key, "openai": 0.0,
                                         "brave": 0.0, "total": 0.0, "calls": 0})
        try:
            amount = float(r.cost_usd or 0)
        except ValueError:
            amount = 0.0
        bucket[r.service] = round(bucket.get(r.service, 0.0) + amount, 6)
        bucket["total"] = round(bucket["total"] + amount, 6)
        bucket["calls"] += 1

    days_out = sorted(by_day.values(), key=lambda d: d["date"], reverse=True)

    # ── Month to date, against the cap ───────────────────────────────────
    cap = G.cap_state(db, project=project)
    month_start = datetime.utcnow().date().replace(day=1)

    # Cost PER IMAGE is the number that actually predicts the bill: the
    # backlog is counted in images, not dollars, and "$0.02 each" answers
    # "what will the remaining 3,000 cost" in a way a monthly total cannot.
    images_this_month = (
        db.query(func.count(ProcessedImage.id))
          .filter(ProcessedImage.project_id == project.id,
                  ProcessedImage.created_at >= datetime.combine(
                      month_start, datetime.min.time()))
          .scalar() or 0
    )
    spent_month = float(cap["spent"])
    per_image = round(spent_month / images_this_month, 4) if images_this_month else None

    remaining_backlog = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status.in_(("greenlit", "processing")),
                  SavedPoster.deleted_at.is_(None),
                  _title_scope(db, project))
          .scalar() or 0
    )

    return JSONResponse({
        "ok": True,
        "project": {"id": project.id, "name": project.name},
        "month": {
            "spent": round(spent_month, 4),
            "cap": float(cap["cap"]),
            "over": cap["over"],
            "action": cap["action"],
            "images": images_this_month,
            "per_image": per_image,
            # What finishing the queue would cost at the rate seen so far.
            # An estimate, and labelled as one — the model's price varies
            # with the size it picks for each source photo.
            "backlog": remaining_backlog,
            "backlog_cost": (round(per_image * remaining_backlog, 2)
                             if per_image and remaining_backlog else None),
        },
        # OpenAI's own figure, when an admin key is configured. Reported
        # beside ours rather than replacing it — see openai_costs.py for why
        # both numbers are worth keeping.
        "reconcile": OC.last_result(db),
        "days": days_out,
        "today": days_out[0] if days_out else None,
    })

# ═══════════════════════════════════════════════════════════════════════════
#  NEEDS ATTENTION
# ═══════════════════════════════════════════════════════════════════════════
#
# One page answering one question: what has stopped, and what do I press?
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS IS NOT JUST THE FAILURES TAB
# ─────────────────────────────────────────────────────────────────────────
# Failures lists rows whose status is literally 'failed'. That misses every
# way this pipeline stops WITHOUT anything being marked failed:
#
#   · spend cap reached          — the GPT stage simply stops claiming
#   · a bad API key              — every image fails identically, for one
#                                  reason, and 400 rows say so one at a time
#   · a title held pre-dispatch  — 'failed', but RETRY re-fails forever
#                                  because the fix is editing the title
#   · a stale claim              — status says 'processing', nothing is
#   · a half-finished title      — image 1 listed, image 2 retired
#
# The unifying idea is FINDINGS, the same shape diagnostics.py uses: a thing
# that is true, why it matters, and the specific action that resolves it.
# Silence here should mean the pipeline is genuinely fine — which is only
# worth anything if the checks cover the quiet failures too.
#
# ─────────────────────────────────────────────────────────────────────────
# GENERIC ACROSS PROJECTS
# ─────────────────────────────────────────────────────────────────────────
# Nothing below mentions GPT, Photoshop, Brave or FineArtAmerica. A check
# either applies to whatever this project's processor is, or it is skipped by
# asking the project what it does. Project three inherits this for free.

def _attention_finding(key, label, why, action, severity="warn", items=None, note=""):
    return {
        "key": key, "label": label, "why": why, "action": action,
        "severity": severity, "note": note,
        "items": items or [], "count": len(items or []),
    }


@router.get("/api/attention")
def api_attention(
    request: Request,
    project_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Everything stopped or stuck in this project, grouped by what fixes it.

    Item lists are capped at `limit` while COUNTS are exact — a check that
    materialises 3,000 rows to tell you there are 3,000 is how a diagnostics
    page becomes the thing you avoid opening.
    """
    project = _project(request, admin, db, project_id)
    scope = _title_scope(db, project)
    findings: list[dict] = []
    now = datetime.utcnow()

    def title_of(t):
        return {"master_id": t.id, "external_id": t.external_id,
                "title": t.title, "year": t.year}

    # ── 1 · Whole stage stopped ─────────────────────────────────────────
    # Checked FIRST and reported as one line, not N. When the spend cap trips
    # or a key is wrong, every image fails for the same reason; a per-image
    # list buries the single fact that matters.
    if project.processor == "gpt":
        from .. import gpt_images as G
        try:
            cap = G.cap_state(db, project=project)
        except Exception:
            cap = None
        if cap and cap.get("over"):
            findings.append(_attention_finding(
                "spend_capped",
                f"Image generation is {'paused' if cap['action'] == 'pause' else 'over budget'}",
                f"${cap['spent']} spent this month against a ${cap['cap']} cap. "
                + ("Nothing is being generated until the cap is raised or the "
                   "month rolls over." if cap["action"] == "pause"
                   else "Generation is continuing; this is a warning only."),
                "Raise or clear the cap under Processing → Spending.",
                severity="stop" if cap["action"] == "pause" else "warn",
                items=[{"kind": "note", "spent": cap["spent"], "cap": cap["cap"]}],
            ))

    # ── Is the stage that does the work actually running? ───────────────
    # Checked BEFORE the per-image failures, because when the answer is no,
    # every other number on this page is explained by it. Nothing is marked
    # failed when a worker stops — the queue just stops moving, which is the
    # hardest kind of stoppage to notice.
    if project.processor == "gpt":
        from .. import gpt_worker as GW

        h = GW.health()
        waiting = (
            db.query(func.count(SavedPoster.id))
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.pipeline_status == "greenlit",
                      SavedPoster.deleted_at.is_(None), scope)
              .scalar() or 0
        )
        # Only a problem if there is work it should be doing. A stopped
        # worker with an empty queue is just a quiet afternoon.
        if waiting and (not h["alive"] or h["stale"]):
            if not h["alive"]:
                why = ("The image generation worker is not running. It is "
                       "restarted automatically within a minute, so if this "
                       "persists it is failing to start rather than having "
                       "stopped.")
            else:
                why = (f"The worker is running but has done nothing for "
                       f"{h['age_s']} seconds. It is most likely stuck waiting "
                       f"on a request that never came back. It is NOT restarted "
                       f"automatically, because it may still be inside a call "
                       f"you have already paid for and a second worker could "
                       f"pay for the same image twice.")
            findings.append(_attention_finding(
                "generation_stopped",
                f"Image generation has stopped with {waiting} waiting",
                why + (f" Last error: {h['last_error']}" if h["last_error"] else ""),
                "Usually nothing — it revives itself. If it keeps happening, "
                "restart the server and tell whoever maintains this.",
                severity="stop",
                items=[{"kind": "worker", "alive": h["alive"], "age_s": h["age_s"],
                        "restarts": h["restarts"], "processed": h["processed"],
                        "waiting": waiting}],
                note=(f"Restarted {h['restarts']} time(s) since the server "
                      f"started." if h["restarts"] else ""),
            ))

    # ── Is there a machine to do this project's node work? ──────────────
    #
    # The node is SHARED and does two jobs: Photoshop processing, and
    # marketplace uploads for every project. So a project depends on it if it
    # processes there OR has anything queued to upload — MUSIK generates its
    # images on this server but still uploads through that Windows box.
    #
    # This originally checked `processor == 'photoshop'` only, which meant
    # standing inside MUSIK said nothing at all while the node its uploads
    # depend on was dead. The account-wide version of this lives on the
    # master dashboard; this one explains what it means for THIS project.
    stale_node_after = datetime.utcnow() - timedelta(minutes=10)
    nodes = db.query(WorkerNode).filter(WorkerNode.is_enabled == 1).all()
    online_nodes = [n for n in nodes
                    if n.last_seen_at and n.last_seen_at > stale_node_after]

    waiting_process = (
        db.query(func.count(SavedPoster.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "greenlit",
                  SavedPoster.deleted_at.is_(None), scope)
          .scalar() or 0
    ) if project.processor in P.NODE_PROCESSORS else 0

    waiting_upload = (
        db.query(func.count(UploadTracking.id))
          .filter(UploadTracking.status.in_(("pending", "failed")),
                  UploadTracking.project_id == project.id)
          .scalar() or 0
    )

    if nodes and not online_nodes and (waiting_process or waiting_upload):
        held = []
        if waiting_process:
            held.append(f"{waiting_process} waiting to be processed")
        if waiting_upload:
            held.append(f"{waiting_upload} waiting to upload")
        last = max((n.last_seen_at for n in nodes if n.last_seen_at), default=None)
        findings.append(_attention_finding(
            "node_offline",
            "No worker machine is running — " + " and ".join(held),
            "Every machine that can do this work is offline. The usual cause "
            "is that the Windows box rebooted — Windows updates do this on "
            "their own schedule — and came back without starting the agent. "
            + ("Image generation is unaffected; that runs on this server."
               if project.processor == "gpt" else ""),
            "Log into the machine over Remote Desktop. The agent starts itself "
            "once you do.",
            severity="stop",
            items=[{"kind": "node", "name": n.name,
                    "last_seen": fmt_local(n.last_seen_at, "%Y-%m-%d %H:%M")
                                 if n.last_seen_at else "never"}
                   for n in nodes],
            note=(f"Last contact {fmt_local(last, '%Y-%m-%d %H:%M')}." if last
                  else "No machine has ever checked in."),
        ))

    # ── Do our costs match the bill? ────────────────────────────────────
    # Surfaced here as well as on the spend panel, because the spend panel
    # is somewhere you go when you are already thinking about money, and
    # this is something you need told rather than something you go looking
    # for. The consequence is that the cap stops meaning anything.
    if project.processor == "gpt":
        from .. import openai_costs as OC

        rec = OC.last_result(db)
        if rec and rec.get("significant"):
            findings.append(_attention_finding(
                "spend_mismatch",
                "Our cost figures disagree with OpenAI's billing",
                "We calculate spend from the token counts each call reports, "
                "multiplied by prices written into the code. OpenAI's own "
                "billing says something different — usually because they "
                "changed their prices, which makes the per-image cost and the "
                "monthly cap wrong until those rates are updated.",
                "Compare against OpenAI's billing page. If their prices have "
                "changed, the rates in gpt_images.py need updating.",
                severity="warn",
                items=[{"kind": "note", "spent": rec.get("ours"),
                        "cap": rec.get("theirs")}],
                note=f"Last checked {rec.get('checked_at', '')}.",
            ))

    # A configuration failure repeats identically on every image. Group by the
    # error text so a wrong API key reads as one problem with a count, rather
    # than as 400 separate ones.
    config_rows = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "failed_processing",
                  SavedPoster.deleted_at.is_(None),
                  or_(SavedPoster.process_error.like("[auth]%"),
                      SavedPoster.process_error.like("[billing]%")),
                  scope)
          .limit(500)
          .all()
    )
    if config_rows:
        by_error: dict[str, int] = {}
        for poster, _t in config_rows:
            by_error[(poster.process_error or "")[:200]] = \
                by_error.get((poster.process_error or "")[:200], 0) + 1
        findings.append(_attention_finding(
            "config_blocked",
            "Images are failing for a configuration reason",
            "These are not bad images — the account, key or billing is the "
            "problem, and every image will fail the same way until it is fixed.",
            "Fix the setting, then RETRY these — they cost nothing so far.",
            severity="stop",
            items=[{"kind": "group", "error": e, "count": n} for e, n in by_error.items()],
            note=f"{len(config_rows)} images affected.",
        ))

    # ── 2 · The processor refused the image itself ──────────────────────
    # Distinct from a transient failure because retrying spends money to be
    # refused again. The decision here is edit-the-source or retire it.
    rejected_q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "failed_processing",
                  SavedPoster.deleted_at.is_(None),
                  SavedPoster.process_error.like("[rejected]%"),
                  scope)
    )
    rejected_count = rejected_q.count()
    if rejected_count:
        from ..gpt_images import extract_categories
        items = []
        for poster, t in rejected_q.order_by(SavedPoster.id.desc()).limit(limit).all():
            items.append({
                "kind": "poster", "poster_id": poster.id, **title_of(t),
                "filename": poster.filename,
                "error": poster.process_error,
                "categories": extract_categories(poster.process_error or ""),
                "attempts": poster.process_attempts,
            })
        findings.append(_attention_finding(
            "rejected",
            "Refused by the image model",
            "The source image tripped a content rule. Retrying spends again "
            "and is refused again unless the SOURCE changes — so the real "
            "choices are send it back to a worker for a different photo, or "
            "retire it.",
            "Replace the photo yourself (Worker Images → paste a new URL on the title), or MARK UNUSABLE. Nothing goes back to the worker — they were paid when they saved it.",
            items=items,
            note=(f"Showing {len(items)} of {rejected_count}." if rejected_count > len(items) else ""),
        ))

    # ── 3 · Titles the marketplace would refuse ─────────────────────────
    # Held BEFORE dispatch, so nothing was sent. RETRY is the wrong verb here
    # and is deliberately not offered: the row would fail identically.
    held_q = (
        db.query(UploadTracking, SavedPoster, MasterTitle)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(UploadTracking.status == "failed",
                  UploadTracking.project_id == project.id,
                  UploadTracking.last_error.like("title held:%"))
    )
    held_count = held_q.count()
    if held_count:
        items = []
        for tr, poster, t in held_q.order_by(UploadTracking.id.desc()).limit(limit).all():
            items.append({
                "kind": "tracking", "tracking_id": tr.id, "poster_id": poster.id,
                **title_of(t),
                "remote_title": tr.remote_title,
                "error": (tr.last_error or "").replace("title held: ", ""),
            })
        findings.append(_attention_finding(
            "title_held",
            "Held — the marketplace would reject this title",
            "The marketplace deletes characters it does not accept, and a "
            "title that survives as nothing is refused with an error PAGE "
            "rather than an error code. These were stopped before being sent, "
            "so no attempt was wasted.",
            "Type a title that works and press SAVE & QUEUE.",
            items=items,
            note=(f"Showing {len(items)} of {held_count}." if held_count > len(items) else ""),
        ))

    # ── 4 · Ordinary exhausted failures ─────────────────────────────────
    max_process = int(P.get_setting(db, "process_max_attempts", project=project) or 3)
    # THE CATCH-ALL, and it must stay one (owner's find, 2026-09-06).
    # This bucket used to exclude EVERY bracket-tagged error, on the
    # assumption the buckets above covered them all. They covered three of
    # four: a "[bad_request]" failure — a real kind the classifier emits —
    # fell through every filter, so six failed images showed a badge of 6
    # and a panel explaining none of them. The filter now excludes ONLY the
    # kinds a bucket above already displays, so a kind nobody anticipated
    # lands HERE with its full error text instead of vanishing.
    proc_q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "failed_processing",
                  SavedPoster.deleted_at.is_(None),
                  # or_ with IS NULL, because in SQL `NOT LIKE` is
                  # unknown for a NULL — three NOT-LIKEs alone would
                  # silently drop every failure that has no error text,
                  # recreating the exact invisibility being fixed.
                  or_(SavedPoster.process_error.is_(None),
                      and_(~SavedPoster.process_error.like("[auth]%"),
                           ~SavedPoster.process_error.like("[billing]%"),
                           ~SavedPoster.process_error.like("[rejected]%"))),
                  scope)
    )
    proc_count = proc_q.count()
    if proc_count:
        items = [
            {"kind": "poster", "poster_id": p.id, **title_of(t),
             "filename": p.filename, "error": p.process_error,
             "attempts": p.process_attempts,
             "exhausted": (p.process_attempts or 0) >= max_process}
            for p, t in proc_q.order_by(SavedPoster.id.desc()).limit(limit).all()
        ]
        findings.append(_attention_finding(
            "process_failed",
            "Processing gave up after retrying",
            "These failed for a reason that looked temporary — a network "
            "error, storage being unreachable — and used every attempt.",
            "Fix the cause, then RETRY. Attempts reset to zero.",
            items=items,
            note=(f"Showing {len(items)} of {proc_count}." if proc_count > len(items) else ""),
        ))

    max_upload = int(P.get_setting(db, "upload_max_attempts", project=project) or 3)
    up_q = (
        db.query(UploadTracking, SavedPoster, MasterTitle, UploadAccount)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .join(UploadAccount, UploadTracking.account_id == UploadAccount.id)
          .filter(UploadTracking.status == "failed",
                  UploadTracking.project_id == project.id,
                  or_(UploadTracking.last_error.is_(None),
                      ~UploadTracking.last_error.like("title held:%")))
    )
    up_count = up_q.count()
    if up_count:
        items = [
            {"kind": "tracking", "tracking_id": tr.id, "poster_id": p.id,
             **title_of(t), "account": acc.name, "account_id": acc.id,
             "remote_title": tr.remote_title, "error": tr.last_error,
             "screenshot": tr.last_screenshot, "attempts": tr.attempts,
             "exhausted": (tr.attempts or 0) >= max_upload}
            for tr, p, t, acc in up_q.order_by(UploadTracking.id.desc()).limit(limit).all()
        ]
        findings.append(_attention_finding(
            "upload_failed",
            "Upload failed",
            "The marketplace refused, timed out, or changed its page. A "
            "screenshot from the moment it broke is attached where the node "
            "managed to take one.",
            "RETRY after checking the screenshot, or MARK REMOVED if the "
            "listing was taken down.",
            items=items,
            note=(f"Showing {len(items)} of {up_count}." if up_count > len(items) else ""),
        ))

    # ── 5 · Claimed, but nothing is happening ───────────────────────────
    # Status says in-progress, so no failure check sees these. If the node
    # died between claiming and reporting, this is the only place it shows.
    timeout_s = int(P.get_setting(db, "claim_timeout_min", project=project) or 30) * 60
    cutoff = now - timedelta(seconds=timeout_s)
    stalled = []
    for poster, t in (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "processing",
                  SavedPoster.claimed_at.isnot(None),
                  SavedPoster.claimed_at < cutoff,
                  SavedPoster.deleted_at.is_(None), scope)
          .limit(limit).all()
    ):
        stalled.append({"kind": "poster", "poster_id": poster.id, **title_of(t),
                        "stage": "processing", "node": poster.claimed_by,
                        "held_min": int((now - poster.claimed_at).total_seconds() // 60)})
    for tr, poster, t in (
        db.query(UploadTracking, SavedPoster, MasterTitle)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(UploadTracking.status == "uploading",
                  UploadTracking.project_id == project.id,
                  UploadTracking.claimed_at.isnot(None),
                  UploadTracking.claimed_at < cutoff)
          .limit(limit).all()
    ):
        stalled.append({"kind": "tracking", "tracking_id": tr.id, "poster_id": poster.id,
                        **title_of(t), "stage": "uploading", "node": tr.claimed_by,
                        "held_min": int((now - tr.claimed_at).total_seconds() // 60)})
    if stalled:
        findings.append(_attention_finding(
            "stalled",
            "Claimed but not finished",
            "A machine took this work and never reported back — usually it "
            "was rebooted or lost its connection mid-item. The dispatcher "
            "frees these automatically; they are listed so a node that keeps "
            "doing it is visible.",
            "Usually nothing. RELEASE forces it back into the queue now.",
            severity="info", items=stalled,
        ))

    # ── 6 · Retired images ──────────────────────────────────────────────
    unusable_q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "unusable",
                  SavedPoster.deleted_at.is_(None), scope)
    )
    unusable_count = unusable_q.count()
    if unusable_count:
        items = [
            {"kind": "poster", "poster_id": p.id, **title_of(t),
             "reason": p.unusable_reason, "by": p.unusable_by,
             "at": fmt_local(p.unusable_at, "%Y-%m-%d") if p.unusable_at else None}
            for p, t in unusable_q.order_by(SavedPoster.unusable_at.desc().nullslast())
                                  .limit(limit).all()
        ]
        findings.append(_attention_finding(
            "unusable",
            "Retired by you",
            "Out of the pipeline on purpose, with the reason kept. Nothing "
            "was deleted and the worker was still paid. Listed so the "
            "decision stays reversible if the model improves.",
            "RETURN TO PIPELINE to try again.",
            severity="info", items=items,
            note=(f"Showing {len(items)} of {unusable_count}." if unusable_count > len(items) else ""),
        ))

    # ── 7 · Titles that will list short ─────────────────────────────────
    # Every image here is already counted above; the finding is about the
    # TITLE. An artist listing with one image instead of two is a decision
    # you may want to revisit, and no per-image row says that.
    expected = int(project.images_per_title or 0)
    short = []
    if expected > 1:
        rows = (
            db.query(MasterTitle.id, MasterTitle.external_id, MasterTitle.title,
                     MasterTitle.year,
                     func.count(SavedPoster.id).label("total"),
                     func.sum(
                         case((SavedPoster.pipeline_status.in_(
                             ("processed", "uploading", "uploaded")), 1), else_=0)
                     ).label("good"))
              .join(SavedPoster, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.deleted_at.is_(None), scope)
              .group_by(MasterTitle.id)
              .having(func.sum(
                  case((SavedPoster.pipeline_status.in_(
                      ("processed", "uploading", "uploaded")), 1), else_=0)) > 0)
              .having(func.sum(
                  case((SavedPoster.pipeline_status.in_(
                      ("greenlit", "processing", "uploading")), 1), else_=0)) == 0)
              .limit(limit * 4)
              .all()
        )
        for mid, ext, name, year, total, good in rows:
            if (good or 0) < expected:
                short.append({"kind": "title", "master_id": mid, "external_id": ext,
                              "title": name, "year": year,
                              "have": int(good or 0), "expected": expected})
    if short:
        findings.append(_attention_finding(
            "short_titles",
            f"Fewer than {expected} images will list",
            "Nothing is stuck — these titles have simply finished with fewer "
            "images than planned, because the rest were refused, retired or "
            "never found. Each individual image appears under one of the "
            "groups above; this says which ARTISTS come out thin.",
            "Send the title back to a worker for another source image, or "
            "accept it.",
            severity="info", items=short[:limit],
            note=(f"Showing {min(len(short), limit)} of {len(short)}."
                  if len(short) > limit else ""),
        ))

    return JSONResponse({
        "ok": True,
        "project": {"id": project.id, "slug": project.slug, "name": project.name,
                    "item_noun": project.item_noun,
                    "item_noun_plural": project.item_noun_plural},
        "findings": findings,
        "total": sum(f["count"] for f in findings if f["severity"] != "info"),
        "checked_at": fmt_local(now, "%Y-%m-%d %H:%M"),
    })


@router.post("/api/attention/retitle")
def api_attention_retitle(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Replace a held title with one the marketplace accepts, and queue it.

    The submitted text is put through the SAME normalisation the uploader
    would apply, and stored as the normalised result — not as typed. That is
    the whole reason the reconciliation scanner can later compare our
    remote_title against the live listing and expect them to be equal.

    Validated again after normalising, so an edit that still comes out empty
    is refused here rather than being queued to fail at the marketplace.
    """
    tracking_id = payload.get("tracking_id")
    raw = (payload.get("title") or "").strip()
    if not tracking_id:
        raise HTTPException(400, "tracking_id is required.")
    if not raw:
        raise HTTPException(400, "A title is required.")

    tracking = db.query(UploadTracking).filter_by(id=tracking_id).first()
    if tracking is None:
        raise HTTPException(404, "That upload row no longer exists.")

    cleaned = P.tidy_separators(P.clean_for_marketplace(raw))
    problem = P.validate_marketplace_title(raw, cleaned)
    if problem:
        raise HTTPException(400, problem)

    tracking.remote_title = cleaned
    tracking.status = "pending"
    tracking.attempts = 0
    tracking.last_error = None
    tracking.claimed_at = None
    tracking.claimed_by = None

    poster = db.query(SavedPoster).filter_by(id=tracking.saved_poster_id).first()
    if poster is not None and poster.pipeline_status == "failed_upload":
        poster.pipeline_status = "processed"

    log_activity(db, user=admin, action="pipeline_retitle", target_type="pipeline",
                 details={"tracking_id": tracking.id, "typed": raw, "stored": cleaned})
    db.commit()
    return JSONResponse({"ok": True, "remote_title": cleaned})


@router.post("/api/attention/preview_title")
def api_attention_preview_title(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
):
    """
    What the marketplace would store for this text — live, as you type.

    Worth an endpoint of its own so the fold table has exactly ONE
    implementation. A JavaScript copy would drift the first time a character
    was added to it, and the admin would be editing against a lie.
    """
    raw = (payload.get("title") or "")
    cleaned = P.tidy_separators(P.clean_for_marketplace(raw))
    return JSONResponse({
        "ok": True, "rendered": cleaned, "length": len(cleaned),
        "problem": P.validate_marketplace_title(raw, cleaned),
    })


@router.post("/api/attention/retry_group")
def api_attention_retry_group(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Retry every image affected by a single shared cause.

    Only offered for `config_blocked`, where by definition every row failed
    for the same reason — a wrong key, an unpaid account. Ticking 400 boxes
    to express "the thing I just fixed" is busywork, and the count is exact
    rather than limited to what the page happened to show.

    Deliberately NOT offered for rejections: those failed individually, on
    their own content, and a blanket retry there spends real money to be
    refused again.
    """
    if payload.get("key") != "config_blocked":
        raise HTTPException(400, "Only the configuration group can be retried as a whole.")

    project = _project(request, admin, db, payload.get("project_id"))
    rows = (
        db.query(SavedPoster)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status == "failed_processing",
                  SavedPoster.deleted_at.is_(None),
                  or_(SavedPoster.process_error.like("[auth]%"),
                      SavedPoster.process_error.like("[billing]%")),
                  _title_scope(db, project))
          .all()
    )
    for poster in rows:
        poster.pipeline_status = "greenlit"
        poster.process_attempts = 0
        poster.process_error = None
        poster.claimed_at = None
        poster.claimed_by = None
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title:
            P.recompute_title_status(db, title)

    log_activity(db, user=admin, action="pipeline_retry_group", target_type="pipeline",
                 details={"key": "config_blocked", "count": len(rows)})
    db.commit()
    return JSONResponse({"ok": True, "requeued": len(rows)})


@router.post("/api/attention/release")
def api_attention_release(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Free a stale claim now rather than waiting for the reaper.

    Only touches rows that are genuinely claimed — releasing something a node
    is actively working on would hand the same item to a second machine.
    """
    freed = 0
    for pid in payload.get("poster_ids") or []:
        poster = db.query(SavedPoster).filter_by(id=pid).first()
        if poster is not None and poster.pipeline_status == "processing":
            poster.pipeline_status = "greenlit"
            poster.claimed_at = None
            poster.claimed_by = None
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                P.recompute_title_status(db, title)
            freed += 1
    for tid in payload.get("tracking_ids") or []:
        tracking = db.query(UploadTracking).filter_by(id=tid).first()
        if tracking is not None and tracking.status == "uploading":
            tracking.status = "pending"
            tracking.claimed_at = None
            tracking.claimed_by = None
            freed += 1

    log_activity(db, user=admin, action="pipeline_release", target_type="pipeline",
                 details={"freed": freed})
    db.commit()
    return JSONResponse({"ok": True, "freed": freed})


@router.post("/api/images/unusable")
def api_mark_unusable(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Take an image out of the pipeline permanently — "the AI cannot render
    this acceptably, and paying to try again is throwing money away".

    ════════════════════════════════════════════════════════════════════════
    NOT A DELETION
    ════════════════════════════════════════════════════════════════════════
    Everything stays: the source file, the poster row, the worker's pay for
    finding it, every generation that was attempted, and the reason. The only
    thing that changes is that the pipeline stops offering it work.

    That matters because the alternative — deleting it — destroys the answer
    to the question you will actually ask in three years: "why is there no
    listing for this artist?" A row that says
    `unusable: AI keeps merging her with the background` answers itself.

    `unusable` is in IN_PIPELINE_STATES, so re-greenlighting a date range
    cannot quietly drag it back and re-spend on the same bad output. Only
    `return_to_pipeline` below reverses it, deliberately.
    """
    poster_ids = payload.get("poster_ids") or []
    reason = (payload.get("reason") or "").strip()
    if not poster_ids:
        raise HTTPException(400, "poster_ids is required.")
    if not reason:
        # Enforced, not optional. A blank reason makes this indistinguishable
        # from a bug three years from now, which is the whole thing we are
        # trying to avoid.
        raise HTTPException(400, "A reason is required — it is the only record of why.")

    posters = db.query(SavedPoster).filter(SavedPoster.id.in_(poster_ids)).all()
    now = datetime.utcnow()
    for poster in posters:
        poster.pipeline_status = "unusable"
        poster.unusable_reason = reason[:2000]
        poster.unusable_at = now
        poster.unusable_by = admin.username
        poster.claimed_at = None
        poster.claimed_by = None
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title:
            P.recompute_title_status(db, title)

    log_activity(db, user=admin, action="pipeline_unusable", target_type="pipeline",
                 details={"poster_ids": [p.id for p in posters], "reason": reason})
    db.commit()
    return JSONResponse({"ok": True, "count": len(posters)})


@router.post("/api/images/return_to_pipeline")
def api_return_to_pipeline(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Undo `unusable` — the model improved, or the judgement was wrong.

    Deliberately a separate, explicit action rather than something greenlight
    does by accident.
    """
    poster_ids = payload.get("poster_ids") or []
    if not poster_ids:
        raise HTTPException(400, "poster_ids is required.")

    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.id.in_(poster_ids),
                  SavedPoster.pipeline_status == "unusable")
          .all()
    )
    for poster in posters:
        poster.pipeline_status = "greenlit"
        poster.process_attempts = 0
        poster.process_error = None
        # The reason is kept, not cleared: the history of "this was once
        # judged unusable" is worth more than a tidy row.
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title:
            P.recompute_title_status(db, title)

    log_activity(db, user=admin, action="pipeline_return", target_type="pipeline",
                 details={"poster_ids": [p.id for p in posters]})
    db.commit()
    return JSONResponse({"ok": True, "count": len(posters)})


@router.post("/api/storage/test")
def api_storage_test(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Prove this server can actually WRITE to the archive.

    Writes a small file and reports the result, rather than only opening a
    connection. The failure that happens in practice is a path that is
    readable but not writable — a login test passes that and you find out
    when a batch of generated images has nowhere to go.
    """
    from ..storage_remote import check
    ok, message = check(db, project=_project(request, admin, db))
    return JSONResponse({"ok": ok, "message": message})


@router.get("/review", response_class=HTMLResponse)
def review_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    The review screen.

    Gated on the project's CAPABILITY, not on whether the gate is currently
    switched on. Turning it off releases nothing that was already waiting, so
    the page has to stay reachable to clear that backlog — the nav hides the
    tab once the backlog is empty, which is a display decision, not an
    access one.
    """
    project = _project(request, admin, db)
    if not project.has_review_gate:
        raise HTTPException(404, "This project has no review step.")
    return templates.TemplateResponse(
        request, "admin_review_images.html",
        {"user": admin, "admin": admin, "active_tab": "review",
         "project": project},
    )


# ═══════════════════════════════════════════════════════════════════════════
#  REVIEW GATE — admin approval of AI output
# ═══════════════════════════════════════════════════════════════════════════
#
# Only for projects that declare has_review_gate. Photoshop output is
# deterministic and has always gone straight to upload; GPT output varies, so
# it gets a look before anything is listed.
#
# The unit of review is the TITLE, not the image. You judge an artist's pair
# together — that is how a customer sees them, and it is the only way to spot
# "these two are basically the same picture".

@router.get("/api/review/dates")
def api_review_dates(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Which save-dates have images waiting, and how many.

    Grouped by date because that is how the work arrives and how you will
    want to sit down to it — "everything since Monday", not "the next 40".
    """
    project = _project(request, admin, db)
    rows = (
        db.query(SavedPoster.original_save_date,
                 func.count(func.distinct(MasterTitle.id)),
                 func.count(ProcessedImage.id))
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .join(ProcessedImage, ProcessedImage.saved_poster_id == SavedPoster.id)
          .filter(ProcessedImage.is_current == 1,
                  ProcessedImage.review_status == "pending",
                  ProcessedImage.project_id == project.id)
          .group_by(SavedPoster.original_save_date)
          .order_by(SavedPoster.original_save_date.desc())
          .all()
    )
    return JSONResponse({
        "dates": [
            {"date": d.isoformat() if d else None, "titles": t, "images": i}
            for d, t, i in rows
        ],
        # Fresh attempts AWAITING review, not the superseded originals —
        # those keep status 'rerun' for ever as evidence, so counting them
        # left this button reading 4 with nothing to review (owner's find,
        # 2026-09-06).
        "reruns": db.query(func.count(ProcessedImage.id))
                    .filter(ProcessedImage.review_status == "pending",
                            ProcessedImage.is_current == 1,
                            ProcessedImage.attempt > 1,
                            ProcessedImage.project_id == project.id).scalar() or 0,
    })


@router.get("/api/review/queue")
def api_review_queue(
    request: Request,
    start: str = Query(""),
    end: str = Query(""),
    status: str = Query("pending"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Every title awaiting review in a date range, with its images.

    Returns the WHOLE range in one call rather than paging. A review session
    is arrow-keyed at a couple of seconds per title, and a network round trip
    between each one would make it feel broken. Even 500 titles is a small
    JSON payload — the images themselves are fetched lazily as previews.
    """
    project = _project(request, admin, db)
    q = (
        db.query(ProcessedImage, SavedPoster, MasterTitle)
          .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(ProcessedImage.is_current == 1,
                  ProcessedImage.project_id == project.id)
    )
    if status == "rerun":
        # "Review reruns" means the FRESH attempts awaiting a verdict, not
        # the superseded originals (those keep status 'rerun' for ever, as
        # evidence). Asking for the originals gave the owner a button
        # reading 4 with nothing behind it (2026-09-06).
        q = q.filter(ProcessedImage.review_status == "pending",
                     ProcessedImage.attempt > 1)
    else:
        q = q.filter(ProcessedImage.review_status == status)
    if start:
        try:
            q = q.filter(SavedPoster.original_save_date >= date.fromisoformat(start))
        except ValueError:
            raise HTTPException(400, "Bad start date.")
    if end:
        try:
            q = q.filter(SavedPoster.original_save_date <= date.fromisoformat(end))
        except ValueError:
            raise HTTPException(400, "Bad end date.")

    rows = q.order_by(SavedPoster.original_save_date.asc(),
                      MasterTitle.external_id.asc().nullslast(),
                      SavedPoster.id.asc()).all()

    # ── EVERY GENERATION OF EACH POSTER, NOT ONLY THE NEWEST ────────────
    #
    # The queue still SELECTS on the current row — which poster needs a
    # verdict is unchanged. What travels with it is the whole history, so
    # the screen can offer "v1 · v2 · v3" and the admin can settle on the
    # third attempt after a fourth came out worse (owner's ask 2026-09-09).
    #
    # One query for the lot rather than one per poster: a 500-title review
    # would otherwise be 500 round trips before the first picture appeared.
    poster_ids = [poster.id for _p, poster, _t in rows]
    siblings: dict[int, list] = {}
    if poster_ids:
        for row in (db.query(ProcessedImage)
                      .filter(ProcessedImage.saved_poster_id.in_(poster_ids))
                      .order_by(ProcessedImage.saved_poster_id.asc(),
                                ProcessedImage.attempt.asc(),
                                ProcessedImage.id.asc()).all()):
            siblings.setdefault(row.saved_poster_id, []).append(row)

    def _signature_for_screen(db_, project_, row):
        """What the screen should draw, or None when nothing is painted."""
        from .. import signature as SIG
        place = SIG.placement(db_, project_, row)
        if place is None:
            return None
        return {
            "x_pct": place["x_pct"],
            "w_pct": place["w_pct"],
            "opacity": place["opacity"],
            "margin_pct": place["margin_pct"],
            "dark": bool(place["dark"]),
            "url": "/admin/pipeline/signature_image",
        }

    def _version(p) -> dict:
        return {
            "processed_id": p.id,
            "attempt": p.attempt or 1,
            "filename": p.filename,
            "width": p.output_width,
            "height": p.output_height,
            "is_current": bool(p.is_current),
            "review_status": p.review_status or "",
            # Whether this generation's PICTURE still exists. A row marked
            # 'discarded' had its files removed when another generation was
            # approved, so the screen must not offer it — the row is the
            # audit record, not something you can still look at.
            "file_kept": (p.review_status or "") != "discarded",
            "created_at": p.created_at.isoformat() if p.created_at else "",
            "preview_url": f"/admin/pipeline/review/image/{p.id}",
            # The colour already flattened in, and whether there is a
            # transparent master to re-flatten from. No master means the
            # generation was opaque and the colour control does nothing —
            # so the screen hides it rather than offering a dead knob.
            "background_color": p.background_color or "",
            # Where this poster's signature sits. The project's defaults with
            # the poster's own nudges over the top, worked out server-side so
            # the screen and the builder can never disagree about it — two
            # copies of one rule is two chances to drift.
            "signature": _signature_for_screen(db, project, p),
            "can_recolor": bool(p.master_path),
            "master_url": (f"/admin/pipeline/review/master/{p.id}"
                           if p.master_path else ""),
        }

    titles: dict = {}
    for processed, poster, title in rows:
        block = titles.setdefault(title.id, {
            "title_id": title.id,
            "external_id": title.external_id,
            "title": title.title,
            "date": poster.original_save_date.isoformat() if poster.original_save_date else "",
            "images": [],
        })
        # Only generations whose picture still exists can be chosen. One whose
        # files were deleted when an earlier approval settled the choice is
        # left out entirely rather than offered and then failing to load.
        versions = [_version(p) for p in siblings.get(poster.id, [processed])
                    if (p.review_status or "") != "discarded"
                    or p.id == processed.id]
        block["images"].append({
            **_version(processed),
            "poster_id": poster.id,
            "source_url": f"/admin/file/{poster.id}",
            "versions": versions,
        })

    return JSONResponse({"titles": list(titles.values()),
                         "count": len(titles), "status": status,
                         "default_background": str(
                             P.get_setting(db, "gpt_background_color",
                                           project=project) or "#000000")})


@router.get("/review/image/{processed_id}")
def serve_review_preview(
    processed_id: int,
    full: int = Query(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stream a processed image for the review screen.

    Serves the 1200px PREVIEW by default, not the 4000px print file: two
    images per screen at full resolution is ~6 MB, which makes arrow-keying
    through 250 titles unusable. `full=1` fetches the real thing for when you
    want to look closely at one.
    """
    from ..storage_remote import read_bytes, StorageError

    processed = db.query(ProcessedImage).filter_by(id=processed_id).first()
    if processed is None:
        raise HTTPException(404, "No such image.")
    rel = processed.storage_path if full else (processed.preview_path or processed.storage_path)
    cache = _review_cache_file(rel, "raw")
    if cache.is_file():
        return Response(content=cache.read_bytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})
    try:
        data = read_bytes(db, rel)
    except StorageError as e:
        raise HTTPException(404, str(e))
    if not full:
        # THE SCREEN NEVER GETS PRINT PIXELS, whatever the row says. A row
        # with no preview_path used to fall back to the 4000×6000 print
        # file — the owner watched it trickle in twice (2026-09-06). If
        # what came back is big, it is downscaled to 1200px HERE, once,
        # and the small version is what gets cached. Self-healing: it no
        # longer matters why a preview is missing.
        if len(data) > 400_000:
            try:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(data)) as img:
                    img.load()
                    if img.width > 1200 or img.height > 1200:
                        img.thumbnail((1200, 1200), Image.LANCZOS)
                    out = io.BytesIO()
                    img.convert("RGB").save(out, "JPEG", quality=85)
                    data = out.getvalue()
            except Exception:
                pass          # serving big beats serving a 500
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache.write_bytes(data)
        except OSError:
            pass
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/review/master/{processed_id}")
def serve_review_master(
    processed_id: int,
    # v155 SHIPPED WITHOUT THIS PARAMETER while the body referenced `full` —
    # a NameError on every request, so the poster pane rendered EMPTY. The
    # owner's screenshot found it within the hour. The lesson is recorded in
    # MEGA_AUDIT.md: the undefined-name check missed a name that exists in a
    # SIBLING route's signature, and a sabotage-test of the new cache path
    # would have caught it before deploy.
    full: int = Query(0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Stream the TRANSPARENT original for the review screen.

    The live colour preview is done in the browser: this picture sits on a
    coloured box and the browser composites it. That is the same arithmetic
    the server does when flattening, so what you see is what you get — and it
    costs no round trip, so dragging a colour picker is instant.

    Two costs were stacking up here (owner's find, 2026-09-06 — "this area
    loads quite slow"):

      * the transparent master went out at its FULL generated size, and a
        full-art PNG is megabytes — reviewing needs eyes, not print pixels;
      * every view re-fetched it from the Storage Box over SFTP, transport
        handshake included.

    So: the master is downscaled once to display size (1000px, alpha kept —
    the live recolour and the eyedropper need the alpha, not the pixels),
    and both the fetch and the downscale are cached on THIS server's disk.
    The flatten-on-approve still uses the full master server-side; `full=1`
    still serves it to the browser when you truly want to pore over one.
    """
    from ..storage_remote import read_bytes, StorageError

    processed = db.query(ProcessedImage).filter_by(id=processed_id).first()
    if processed is None or not processed.master_path:
        raise HTTPException(404, "No transparent original for this image.")

    # Display copies go out as WEBP: a full-art PNG at 1000px is still one
    # to three megabytes, and the owner measured the pane lagging behind
    # the arrow keys (2026-09-06). WebP keeps the alpha the live recolour
    # needs at roughly a tenth of the bytes. `full=1` still serves the
    # untouched PNG master.
    variant = "full" if full else "dispw"
    media = "image/png" if full else "image/webp"
    cache = _review_cache_file(processed.master_path, variant)
    if cache.is_file():
        return Response(content=cache.read_bytes(), media_type=media,
                        headers={"Cache-Control": "private, max-age=3600"})
    try:
        data = read_bytes(db, processed.master_path)
    except StorageError as e:
        raise HTTPException(404, str(e))

    if not full:
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img = img.convert("RGBA")
                if img.width > 1000 or img.height > 1000:
                    img.thumbnail((1000, 1000), Image.LANCZOS)
                out = io.BytesIO()
                img.save(out, "WEBP", quality=82)
                data = out.getvalue()
        except Exception:
            # A failed conversion serves the original PNG and does NOT
            # cache it — caching PNG bytes under the webp key would make
            # every later hit lie about its content type.
            return Response(content=data, media_type="image/png",
                            headers={"Cache-Control": "private, max-age=3600"})
    cache.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache.write_bytes(data)
    except OSError:
        pass              # a full disk must not break the review
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "private, max-age=3600"})


def _review_cache_file(rel_path: str, variant: str):
    """Where a review image's local copy lives — see app/review_cache.py."""
    from ..review_cache import cache_file
    return cache_file(rel_path, variant)


def _build_print_file(db: Session, processed, color: str, project,
                      title=None) -> str:
    """
    Make the finished print file: the colour and the signature, one encode.

    ════════════════════════════════════════════════════════════════════════
    THIS IS THE ONLY PLACE A PRINT FILE IS MADE FOR A GATED PROJECT
    ════════════════════════════════════════════════════════════════════════
    It used to be called `_reflatten` and it only ever RE-made a file that
    the generator had already built, when the admin changed the colour.
    Since 2026-09-09 the generator does not build that file at all for a
    project with a review gate — see gpt_worker.process_one() — so this both
    builds it the first time and rebuilds it when something changes.

    Nothing here talks to OpenAI. The see-through original was kept at
    generation time precisely so that the colour, and now the signature, are
    a local re-render instead of paying for the picture again.

    ════════════════════════════════════════════════════════════════════════
    WHEN IT DOES NOTHING, AND WHY THAT LIST IS EXPLICIT
    ════════════════════════════════════════════════════════════════════════
    Work this expensive must be skipped whenever it would change nothing, so
    the three reasons to DO it are named rather than implied:

      · the file does not exist yet (`storage_path` is empty)
      · the colour is different from the one baked in
      · the signature that would be painted differs from the one already on

    The last is why `signature_applied` is written onto the row. Without a
    record of what was painted, every approval would have to rebuild just in
    case, and a second approval of the same unchanged poster would redo
    several megabytes of work for nothing.

    Returns a note for the log, or "" if nothing needed doing.
    """
    import json as _json

    from ..imagefetch import flatten_onto, make_preview, upscale_to_width
    from ..storage_remote import read_bytes, write_bytes
    from ..config import WORKSPACE_DIR
    from .. import signature as SIG

    if not processed.master_path:
        # An opaque generation has no see-through original, so its print
        # file was built at generation time and cannot be rebuilt here.
        return ""

    place = SIG.placement(db, project, processed)
    mark = SIG.load_mark(WORKSPACE_DIR, place["image"]) if place else None
    if place and mark is None:
        # A signature was asked for and the file is not there. Refusing is
        # the honest answer: silently shipping an unsigned poster is the
        # kind of quiet wrong outcome that is only noticed on the
        # marketplace, weeks later.
        raise RuntimeError(
            "A signature is switched on but its file is missing. Upload it "
            "again on the Settings page, or switch the signature off.")

    wanted_sig = _json.dumps(
        {k: place[k] for k in ("x_pct", "w_pct", "opacity", "dark")},
        sort_keys=True) if place else ""

    needs_file = not (processed.storage_path or "").strip()
    needs_colour = (processed.background_color or "").lower() != (color or "").lower()
    needs_sig = (processed.signature_applied or "") != wanted_sig
    if not (needs_file or needs_colour or needs_sig):
        return ""

    rel = (processed.storage_path or "").strip()
    if not rel and title is not None and processed.saved_poster is not None:
        rel, _name = P.storage_path_for(db, title, processed.saved_poster,
                                        project=project,
                                        attempt=processed.attempt or 1)
    if not rel:
        raise RuntimeError("Cannot work out where this print file should go.")

    tmpdir = WORKSPACE_DIR / "_recolor"
    tmpdir.mkdir(parents=True, exist_ok=True)
    raw = tmpdir / f"{processed.id}_master.png"
    flat = tmpdir / f"{processed.id}_flat.png"
    out = tmpdir / f"{processed.id}.jpg"
    prev = tmpdir / f"{processed.id}_preview.jpg"
    try:
        raw.write_bytes(read_bytes(db, processed.master_path))
        # Flatten THEN enlarge, the same order as generation. Doing it the
        # other way drags dark fringes along every soft edge. The signature
        # goes on AFTER the enlargement — see imagefetch.upscale_to_width().
        flatten_onto(raw, flat, color)
        w = int(P.get_setting(db, "upscale_width_px", project=project) or 4000)
        sharpen = int(P.get_setting(db, "upscale_sharpen", project=project) or 0)
        quality = int(P.get_setting(db, "upscale_jpeg_quality", project=project) or 92)
        overlay = (lambda im: SIG.paint(im, mark, place)) if place else None
        out_w, out_h = upscale_to_width(flat, width=w, sharpen=sharpen,
                                        quality=quality, dest=out,
                                        overlay=overlay)
        make_preview(out, prev)

        preview_rel = processed.preview_path
        if not preview_rel:
            bits = rel.rsplit("/", 1)
            preview_rel = (bits[0] + "/previews/" + bits[1]) if len(bits) == 2 \
                else f"previews/{rel}"

        write_bytes(db, rel, out.read_bytes(), project=project)
        write_bytes(db, preview_rel, prev.read_bytes(), project=project)
        # Drop the stale cached copies, or the review screen keeps showing
        # the old picture after a rebuild.
        from ..review_cache import clear as _clear_review_cache
        _clear_review_cache([rel, preview_rel, processed.master_path])

        processed.storage_path = rel
        processed.preview_path = preview_rel
        processed.output_width, processed.output_height = out_w, out_h
        processed.file_size = out.stat().st_size
        processed.signature_applied = wanted_sig
        what = []
        if needs_file: what.append("built")
        if needs_colour: what.append(f"colour {color}")
        if needs_sig and place: what.append("signature")
        return " · ".join(what)
    finally:
        for f in (raw, flat, out, prev):
            f.unlink(missing_ok=True)


# ════════════════════════════════════════════════════════════════════════════
#  SEND WORK BACK TO THE START — a testing tool, and destructive
# ════════════════════════════════════════════════════════════════════════════

def _recall_targets(db: Session, project, scope: str, title_ids: list):
    """
    Which posters a recall would touch. Read-only, so the preview uses it too.

    The preview and the run ask the SAME function, which is what stops the
    number on the screen being a different number from what the button does.

    ════════════════════════════════════════════════════════════════════════
    THE SCOPING GOES THROUGH `_title_scope`, NOT THROUGH A SECOND COPY
    ════════════════════════════════════════════════════════════════════════
    The first version of this function called `P.project_scope()` with a
    QUERY as its first argument. That function takes a project id and hands
    back a filter CONDITION, so the call raised TypeError and every press of
    the count button returned a 500 (owner's find, 2026-09-09). `_title_scope`
    at the top of this file already wraps `project_scope` correctly and is
    what every other endpoint here uses — rule 3c-ter, reuse the path that
    already works instead of writing a second one.
    """
    from ..models import MasterTitle, SavedPoster

    scoped_titles = (db.query(MasterTitle.id)
                       .filter(_title_scope(db, project))
                       .scalar_subquery())

    q = (db.query(SavedPoster)
           .filter(SavedPoster.deleted_at.is_(None),
                   SavedPoster.master_title_id.in_(scoped_titles)))

    if scope == "selected":
        # The ids the owner ticked in the Title Browser. They are MasterTitle
        # ids, exactly as the greenlight and pull-back buttons send, so the
        # three bulk actions on that panel all speak the same language.
        wanted = [int(n) for n in (title_ids or []) if str(n).strip().isdigit()]
        if not wanted:
            return []
        q = q.filter(SavedPoster.master_title_id.in_(
            db.query(MasterTitle.id)
              .filter(MasterTitle.id.in_(scoped_titles),
                      MasterTitle.id.in_(wanted))
              .scalar_subquery()))
    elif scope == "uploaded":
        q = q.filter(SavedPoster.pipeline_status == "uploaded")
    elif scope == "processed":
        q = q.filter(SavedPoster.pipeline_status == "processed")
    elif scope == "all":
        q = q.filter(SavedPoster.pipeline_status.in_(
            ("processed", "uploaded", "uploading", "failed_upload")))
    else:
        return []
    return q.all()


@router.post("/api/recall")
def api_recall(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Forget that these posters were painted, and put them back in Greenlight.

    ════════════════════════════════════════════════════════════════════════
    WHAT THIS IS FOR, AND WHY IT IS ALLOWED TO BE DESTRUCTIVE
    ════════════════════════════════════════════════════════════════════════
    Asked for on 2026-09-09. While the owner is testing a change to the
    painting, the alternative is claiming a title as a worker, finding an
    image, saving it, approving it and greenlighting it — for every single
    picture, every time. This puts the same posters back at the painting
    step in one press.

    ════════════════════════════════════════════════════════════════════════
    IT DELETES, AND THAT IS THE POINT — SO IT SAYS SO AND ASKS
    ════════════════════════════════════════════════════════════════════════
    The house rule is soft deletes and an audit trail that survives. This
    breaks it deliberately, because a recall that LEFT the old generations
    and upload rows behind would not be a recall: the poster would come back
    carrying its old pictures, the version picker would offer them, and the
    uploader would find a pending row and send the same title again.

    So the guards are at the door instead of in the data:

      · the caller must send `confirm` exactly, typed by a person
      · nothing outside the ACTIVE project can be reached, ever
      · every recall is written to the activity log with its counts
      · the worker's own saved photograph is never touched, so nothing
        anybody was paid for is destroyed

    The one thing it cannot undo is on the marketplace. That warning lives
    on the panel, where it is read, rather than only here.
    """
    from ..models import MasterTitle, SavedPoster, UploadTracking

    if (payload.get("confirm") or "").strip().upper() != "SEND BACK":
        raise HTTPException(400, 'Type SEND BACK to confirm.')

    project = _project(request, admin, db)
    scope = str(payload.get("scope") or "selected")
    posters = _recall_targets(db, project, scope, payload.get("title_ids") or [])
    if not posters:
        return JSONResponse({"ok": True, "posters": 0, "images": 0,
                             "uploads": 0, "files": 0})

    ids = [p.id for p in posters]
    rows = (db.query(ProcessedImage)
              .filter(ProcessedImage.saved_poster_id.in_(ids)).all())

    doomed = []
    for r in rows:
        doomed += [r.storage_path, r.preview_path, r.master_path]
    files = 0
    if doomed:
        from ..storage_remote import delete_paths
        from ..review_cache import clear as _clear_cache
        files = delete_paths(db, doomed, project=project)
        _clear_cache(doomed)

    uploads = (db.query(UploadTracking)
                 .filter(UploadTracking.saved_poster_id.in_(ids)).delete(
                     synchronize_session=False))
    images = (db.query(ProcessedImage)
                .filter(ProcessedImage.saved_poster_id.in_(ids)).delete(
                    synchronize_session=False))

    touched_titles = set()
    for poster in posters:
        # CLEARED TO NOTHING, not to 'greenlit'. greenlight_titles() refuses
        # anything already in the pipeline, so a poster left on a pipeline
        # status would be silently skipped by the very button this tool
        # exists to feed.
        poster.pipeline_status = None
        poster.process_attempts = 0
        poster.process_error = None
        poster.claimed_at = None
        poster.claimed_by = None
        poster.unusable_reason = None
        poster.unusable_at = None
        poster.unusable_by = None
        touched_titles.add(poster.master_title_id)

    for tid in touched_titles:
        t = db.query(MasterTitle).filter_by(id=tid).first()
        if t is not None:
            t.pipeline_status = None
            t.greenlit_at = None
            t.greenlit_by = None
            t.greenlit_source = None
            P.recompute_title_status(db, t)

    log_activity(db, user=admin, action="pipeline_recall", target_type="pipeline",
                 details={"project": project.slug, "scope": scope,
                          "posters": len(posters), "titles": len(touched_titles),
                          "images": images, "uploads": uploads,
                          "files_removed": files})
    db.commit()
    return JSONResponse({"ok": True, "posters": len(posters),
                         "titles": len(touched_titles), "images": images,
                         "uploads": uploads, "files": files})


@router.post("/api/recall/preview")
def api_recall_preview(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    What this would throw away. Same query as the real thing, no writes.

    It exists so the numbers can be read BEFORE the confirmation is typed.
    The difference between "12 pictures" and "1,400 pictures" is the whole
    reason this is two presses instead of one.
    """
    project = _project(request, admin, db)
    posters = _recall_targets(db, project, str(payload.get("scope") or "selected"),
                              payload.get("title_ids") or [])
    ids = [p.id for p in posters]
    images = (db.query(func.count(ProcessedImage.id))
                .filter(ProcessedImage.saved_poster_id.in_(ids)).scalar() or 0) \
        if ids else 0
    return JSONResponse({"ok": True, "posters": len(posters),
                         "titles": len({p.master_title_id for p in posters}),
                         "images": images})


@router.post("/api/review/decide")
def api_review_decide(
    request: Request,
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Record decisions on one or more processed images.

        approve  — release for upload
        rerun    — discard and generate again; lands in the rerun list
        unusable — permanently out of the pipeline, reason required

    Batched because the reviewer approves a whole date range at the end of a
    session rather than one title at a time.
    """
    decisions = payload.get("decisions") or []
    if not decisions:
        raise HTTPException(400, "decisions is required.")

    now = datetime.utcnow()
    counts = {"approved": 0, "rerun": 0, "unusable": 0, "files_removed": 0}
    batch_doomed: list = []

    for item in decisions:
        processed = db.query(ProcessedImage).filter_by(id=item.get("processed_id")).first()
        if processed is None:
            continue
        poster = db.query(SavedPoster).filter_by(id=processed.saved_poster_id).first()
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first() if poster else None
        action = item.get("action")

        if action == "approve":
            # THE COLOUR IS SETTLED HERE, ON APPROVAL, AND NOWHERE ELSE.
            #
            # Approving is what releases the picture for upload, so it is the
            # last moment anybody looks at it. Recording the colour at the
            # same instant means an approved image can never be sitting in
            # the upload queue with nobody having decided what is behind it.
            # ── THE SIGNATURE PLACEMENT, RECORDED BEFORE THE BUILD ───────
            #
            # Written onto the row first, because `_build_print_file` reads
            # it back through `signature.placement()`. Doing it that way
            # means the builder has ONE source for where the mark goes,
            # whether it was called from here or from anywhere later —
            # rather than a placement passed in by one caller and read from
            # the row by another, which is two paths to one fact.
            sig = item.get("signature")
            if isinstance(sig, dict):
                keep = {k: sig[k] for k in ("x_pct", "w_pct", "opacity")
                        if isinstance(sig.get(k), (int, float))}
                keep["dark"] = bool(sig.get("dark"))
                processed.signature_json = json.dumps(keep, sort_keys=True)

            wanted = (item.get("background_color") or "").strip()
            if processed.master_path:
                if not wanted:
                    wanted = str(P.get_setting(
                        db, "gpt_background_color",
                        project=P.project_for_title(db, title) if title else None)
                        or "#000000")
                try:
                    _build_print_file(
                        db, processed, wanted,
                        P.project_for_title(db, title) if title else None,
                        title=title)
                except Exception as e:          # noqa: BLE001
                    # THE APPROVAL MUST FAIL, LOUDLY. Since 2026-09-09 this
                    # is not a cosmetic recolour of a file that already
                    # exists — for a gated project it is where the print
                    # file is MADE. Swallowing this would mark the poster
                    # approved, create its upload rows, and hand the
                    # marketplace a path with nothing behind it.
                    db.rollback()
                    raise HTTPException(
                        500, f"Could not build the print file for "
                             f"{processed.filename}: {e}")
                processed.background_color = wanted
            processed.review_status = "approved"

            # ── APPROVING AN OLDER GENERATION MAKES IT THE CURRENT ONE ───
            #
            # `is_current` is what the uploader reads, so choosing the third
            # attempt has to move that mark or the fourth one would be sent
            # to the marketplace instead. The losing generations are set
            # aside rather than deleted: their rows and (since 2026-09-09)
            # their files both stand, which is what the whole version picker
            # rests on. They are marked 'superseded' so they can never come
            # back round in the pending queue — a sibling left on 'pending'
            # would reappear as work with no picture anybody was waiting on.
            if poster is not None:
                losers = (db.query(ProcessedImage)
                            .filter(ProcessedImage.saved_poster_id == poster.id,
                                    ProcessedImage.id != processed.id).all())
                for other in losers:
                    other.is_current = 0
                    if (other.review_status or "") in ("pending", ""):
                        other.review_status = "superseded"
                processed.is_current = 1

                # ── THE PICTURES NOBODY CHOSE ARE DELETED HERE ───────────
                #
                # Approving is the moment the choice between generations is
                # settled, so it is the moment the losing pictures stop
                # being worth their space. Each generation costs a 4000px
                # print file plus its see-through original, and a poster
                # rerun four times would otherwise hold four sets for ever
                # (the owner asked for this on 2026-09-09).
                #
                # THE ROWS STAY. Only the FILES go. The row is the record
                # that a generation happened, what it cost and what was
                # decided about it, and that record is the audit trail —
                # deleting it would leave the money spent on OpenAI with
                # nothing to point at. `storage_path` on a losing row now
                # names a file that is gone, which the row's
                # 'superseded' status already says.
                #
                # Tidying must never be able to fail an approval: see
                # `delete_paths`, which swallows everything and reports a
                # count. Anything it leaves behind shows up in Diagnostics
                # as an orphan file, so the net is already there.
                for other in losers:
                    # A row already emptied on an earlier approval is skipped,
                    # so pressing approve twice does not count the same files
                    # again and does not have to reach the Storage Box at all.
                    if (other.review_status or "") == "discarded":
                        continue
                    batch_doomed += [other.storage_path, other.preview_path,
                                     other.master_path]
                    # 'discarded' rather than 'superseded' ON PURPOSE. The two
                    # look alike and mean different things: superseded is "set
                    # aside, the picture is still there", discarded is "the
                    # picture has been deleted". The version picker reads this
                    # to decide what it may still offer, and one word for both
                    # would offer a version whose file is gone — which is the
                    # exact family of bug that started this work.
                    other.review_status = "discarded"
                # COLLECTED, NOT DELETED YET. Every file operation opens its
                # own connection to the Storage Box, so deleting inside this
                # loop meant one extra sign-in per approved poster on top of
                # the download and two uploads the rebuild already costs.
                # The whole batch goes in one call after the loop.
            # RELEASING IS WHAT CREATES THE UPLOAD WORK.
            #
            # On the Photoshop path, report_processed() seeds an upload row
            # per enabled account the moment the derivative exists — there is
            # no gate to wait for. A gated project cannot do that: seeding at
            # generation time would queue an image for the marketplace before
            # anyone had looked at it, which is the one thing the gate is for.
            #
            # So the rows are created HERE, on approval. Without this the
            # review screen said "released", the funnel showed 'processed',
            # and nothing ever uploaded — because no upload row existed for
            # the dispatcher to find.
            if poster is not None and title is not None:
                P.ensure_upload_rows(db, poster=poster, title=title,
                                     processed=processed,
                                     project=P.project_for_title(db, title))
            # The review cache exists to make revisiting fast; once an image
            # is approved and heading for upload nobody reviews it again, so
            # this is the right moment — and the ONLY moment — to let it go.
            from ..review_cache import clear as _clear_review_cache
            _clear_review_cache([processed.storage_path, processed.preview_path,
                                 processed.master_path])
            counts["approved"] += 1

        elif action == "rerun":
            processed.review_status = "rerun"
            # The rejected generation is set aside, never deleted — its row
            # AND its file both stand, so the next review can offer it as
            # "v2" and you can settle on it if the new one comes out worse.
            processed.is_current = 0
            # No generation of this poster is the current one until the new
            # one lands. Any sibling still reading 'pending' is closed off
            # here: leaving one would put a picture nobody is waiting on
            # back in the queue the moment the fresh attempt supersedes it.
            if poster is not None:
                for other in (db.query(ProcessedImage)
                                .filter(ProcessedImage.saved_poster_id == poster.id,
                                        ProcessedImage.id != processed.id).all()):
                    other.is_current = 0
                    if (other.review_status or "") == "pending":
                        other.review_status = "superseded"
            if poster:
                poster.pipeline_status = "greenlit"
                poster.process_attempts = 0
                poster.process_error = None
            counts["rerun"] += 1

        elif action == "unusable":
            reason = (item.get("reason") or "").strip()
            if not reason:
                raise HTTPException(400, "A reason is required to mark an image unusable.")
            processed.review_status = "unusable"
            if poster:
                poster.pipeline_status = "unusable"
                poster.unusable_reason = reason[:2000]
                poster.unusable_at = now
                poster.unusable_by = admin.username
            counts["unusable"] += 1
        else:
            continue

        processed.reviewed_at = now
        processed.reviewed_by = admin.username
        if title:
            P.recompute_title_status(db, title)

    # ── THE TIDY-UP, ONCE, AT THE END ────────────────────────────────────
    #
    # One connection for the batch instead of one per poster. It runs after
    # every decision is recorded, so a Storage Box having a moment cannot
    # cost the owner an approval he had already made — the files simply
    # stay, and `check_orphan_files` in diagnostics.py reports them.
    if batch_doomed:
        from ..storage_remote import delete_paths
        from ..review_cache import clear as _clear_cache
        counts["files_removed"] = delete_paths(db, batch_doomed)
        _clear_cache(batch_doomed)

    log_activity(db, user=admin, action="pipeline_review", target_type="pipeline",
                 details=counts)
    db.commit()
    return JSONResponse({"ok": True, **counts})

@router.get("/api/stats")
def api_stats(
    request: Request,
    project_id: Optional[int] = Query(None),
    days: int = Query(30, ge=7, le=365),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Aggregates for the pipeline charts — per-day uploads plus all-time totals."""
    project = _project(request, admin, db, project_id)
    history = P.upload_history(db, days=days)

    totals = {
        "processed": db.query(func.count(ProcessedImage.id))
                       .filter(ProcessedImage.project_id == project.id,
                               ProcessedImage.is_current == 1).scalar() or 0,
        "uploaded": db.query(func.count(UploadTracking.id))
                      .filter(UploadTracking.project_id == project.id,
                              UploadTracking.status == "uploaded").scalar() or 0,
        "removed": db.query(func.count(UploadTracking.id))
                     .filter(UploadTracking.project_id == project.id,
                             UploadTracking.status == "removed").scalar() or 0,
        "pending": db.query(func.count(UploadTracking.id))
                     .filter(UploadTracking.project_id == project.id,
                             UploadTracking.status == "pending").scalar() or 0,
    }

    counts = [h["count"] for h in history]
    active = [c for c in counts if c > 0]
    return JSONResponse({
        "ok": True,
        "history": history,
        "totals": totals,
        "best_day": max(counts) if counts else 0,
        "avg_active_day": round(sum(active) / len(active), 1) if active else 0,
        "days_active": len(active),
    })
