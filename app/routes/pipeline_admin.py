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
  Failures            processing + upload failures with screenshots, retry
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
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import pipeline as P
from ..audit import log as log_activity
from ..auth import require_admin
from ..config import WORKSPACE_DIR
from ..db import get_db
from ..models import (
    MasterTitle, PipelineJob, ProcessedImage, Project, SavedPoster,
    UploadAccount, UploadTracking, User, WorkerNode,
)
from ..templating import templates
from ..timeutil import fmt_local, local_today


router = APIRouter(prefix="/admin/pipeline", tags=["pipeline-admin"])


# ═══════════════════════════════════════════════════════════════════════════
#  PAGE
# ═══════════════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
def pipeline_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    P.ensure_default_project(db)
    db.commit()

    # Which processing panel this project gets. A GPT project has no
    # Photoshop executable, no JSX script and no sharpen radius; showing
    # those to it is worse than clutter, it implies settings that do nothing.
    from ..routes.admin import current_project
    project = current_project(request, admin, db)

    return templates.TemplateResponse(
        request, "admin_pipeline.html",
        {"user": admin, "active_tab": "pipeline",
         "project": project,
         "processor": project.processor,
         "has_review_gate": bool(project.has_review_gate),
         "item_noun": project.item_noun,
         "item_nouns": project.item_noun_plural},
    )



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
    for account in (
        db.query(UploadAccount)
          .filter(UploadAccount.project_id == project.id)
          .order_by(UploadAccount.id.asc())
          .all()
    ):
        quota = P.account_quota(db, account)
        pending = (
            db.query(func.count(UploadTracking.id))
              .filter(UploadTracking.account_id == account.id,
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
    stale_after = datetime.utcnow() - timedelta(minutes=5)
    for node in db.query(WorkerNode).order_by(WorkerNode.id.asc()).all():
        nodes.append({
            "id": node.id,
            "name": node.name,
            "capabilities": [c.strip() for c in (node.capabilities or "").split(",") if c.strip()],
            "is_enabled": bool(node.is_enabled),
            "hostname": node.hostname,
            "agent_version": node.agent_version,
            "last_seen_at": fmt_local(node.last_seen_at, "%Y-%m-%d %H:%M") if node.last_seen_at else None,
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
                  SavedPoster.deleted_at.is_(None))
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
          .filter(UploadTracking.status == "uploading")
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

    return JSONResponse({
        "ok": True,
        "project": {"id": project.id, "slug": project.slug, "name": project.name},
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
            "actionable": pending > 0 and title.status == "complete",
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
    for account in (
        db.query(UploadAccount)
          .filter(UploadAccount.project_id == project.id)
          .order_by(UploadAccount.id.asc())
          .all()
    ):
        stats = {
            status: count for status, count in
            db.query(UploadTracking.status, func.count(UploadTracking.id))
              .filter(UploadTracking.account_id == account.id)
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

    project = _project(request, admin, db, payload.get("project_id"))
    if db.query(UploadAccount).filter_by(project_id=project.id, name=name).first():
        raise HTTPException(400, f"An account named '{name}' already exists in this project.")

    account = UploadAccount(
        project_id=project.id,
        name=name,
        target_site=(payload.get("target_site") or "faa").strip(),
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
    Delete an account.

    Its UploadTracking history is preserved — that's the record of what was
    uploaded where, and it's what makes rebuilding onto a new account
    possible. Only the credentials go away.
    """
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        raise HTTPException(404, "Account not found.")
    name = account.name
    db.delete(account)
    log_activity(db, user=admin, action="pipeline_account_deleted",
                 target_type="upload_account", target_id=account_id,
                 details={"name": name})
    db.commit()
    return JSONResponse({"ok": True})


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
    from ..models import ApiSpend
    from datetime import timedelta

    since = datetime.utcnow().date() - timedelta(days=days - 1)
    rows = (
        db.query(ApiSpend)
          .filter(ApiSpend.created_at >= datetime.combine(since, datetime.min.time()))
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
    return JSONResponse({"days": days_out,
                         "today": days_out[0] if days_out else None})

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
