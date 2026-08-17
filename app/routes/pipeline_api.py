"""
Machine-facing pipeline API — the only surface the remote worker node talks to.

Mounted at /api/pipeline. Authentication is a bearer token belonging to a
WorkerNode row (see pipeline.create_node); browser sessions are NOT accepted
here, and admin endpoints live in routes/pipeline_admin.py instead. Keeping
the two apart means a leaked node token can never reach admin functionality.

────────────────────────────────────────────────────────────────────────────
THE NODE HOLDS NO CONFIGURATION
────────────────────────────────────────────────────────────────────────────
Every endpoint that hands out work also hands out the settings needed to do
it — script source, selectors, timings, credentials, storage root. The node
is a dumb executor. Consequences you must preserve when extending:

  * Editing a selector or the JSX in the dashboard takes effect on the very
    next poll. No redeploy, no SSH, no file sync.
  * A node can be wiped and rebuilt with only its token restored.
  * Two nodes can run concurrently without divergent config.

────────────────────────────────────────────────────────────────────────────
WORK IS CLAIMED, NEVER JUST READ
────────────────────────────────────────────────────────────────────────────
Claim endpoints mutate state (status → processing/uploading, claimed_at set)
inside the same transaction that returns the payload, so two nodes polling
simultaneously can't pick up the same image. If a node dies holding claims,
pipeline.reap_stale_claims() returns them to the queue automatically.

Reporting is per-item, never per-batch: a node that crashes on image 30 of
40 keeps credit for the first 29.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from .. import pipeline as P
from ..audit import log as log_activity
from ..config import WORKSPACE_DIR
from ..db import get_db
from ..models import (
    MasterTitle, PipelineJob, ProcessedImage, SavedPoster, UploadAccount,
    UploadTracking, WorkerNode,
)
from ..utils import saved_poster_path


router = APIRouter(prefix="/api/pipeline", tags=["pipeline-worker"])


# ─── Auth ──────────────────────────────────────────────────────────────────

def require_node(
    authorization: Optional[str] = Header(None),
    x_worker_token: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> WorkerNode:
    """
    Resolve the calling worker node from its bearer token.

    Accepts either `Authorization: Bearer <token>` or the simpler
    `X-Worker-Token` header — the latter keeps the node's own HTTP client
    trivial and is equally safe over TLS.

    Side effect: stamps last_seen_at, which is what the dashboard's node
    health indicator reads. Committed by the endpoint's own commit.
    """
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_worker_token:
        token = x_worker_token.strip()

    node = P.authenticate_node(db, token)
    if node is None:
        raise HTTPException(401, "Invalid or disabled worker token.")
    return node


def _has_capability(node: WorkerNode, capability: str) -> bool:
    caps = {c.strip() for c in (node.capabilities or "").split(",") if c.strip()}
    return capability in caps


def _require_capability(node: WorkerNode, capability: str) -> None:
    if not _has_capability(node, capability):
        raise HTTPException(
            403, f"Node '{node.name}' is not provisioned for '{capability}' work."
        )


# ─── Handshake ─────────────────────────────────────────────────────────────

@router.post("/hello")
def hello(
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Called on node startup and then on every poll cycle.

    Returns the node's capabilities plus global poll/schedule hints, so the
    node's own loop timing is also dashboard-controlled rather than baked
    into its source.
    """
    node.hostname = (payload.get("hostname") or "")[:128] or node.hostname
    node.agent_version = (payload.get("agent_version") or "")[:32] or node.agent_version
    db.commit()

    return JSONResponse({
        "ok": True,
        "node": {
            "name": node.name,
            "capabilities": [c.strip() for c in (node.capabilities or "").split(",") if c.strip()],
        },
        "server_time": datetime.utcnow().isoformat(),
        "poll_interval_s": P.get_setting(db, "poll_interval_s"),
        "schedule_mode": P.get_setting(db, "schedule_mode"),
        "daily_start_hour": P.get_setting(db, "daily_start_hour"),
        # Idle back-off + local log retention. Delivered on every handshake so
        # the node holds no configuration of its own — change it on the
        # Pipeline page and it takes effect on the next cycle, with no RDP
        # session and no restart.
        "poll_interval_idle_s": P.get_setting(db, "poll_interval_idle_s"),
        "poll_idle_after_min": P.get_setting(db, "poll_idle_after_min"),
        "node_log_retention_days": P.get_setting(db, "node_log_retention_days"),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  PHOTOSHOP STAGE
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/process/settings")
def process_settings(
    project_id: Optional[int] = Query(None),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    The rendered JSX plus Photoshop-stage settings.

    The node re-fetches this before each batch and compares script_version;
    when it changes, it rewrites its local .jsx file. That's the whole
    mechanism behind editing the effect from the dashboard.
    """
    _require_capability(node, "process")
    project = P.resolve_project(db, project_id)
    payload = P.process_settings_payload(db, project=project)
    db.commit()
    return JSONResponse({"ok": True, "project": project.slug, **payload})


@router.post("/process/claim")
def process_claim(
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Claim the next Photoshop batch. Returns [] when there's nothing to do,
    which the node treats as "sleep and poll again".
    """
    _require_capability(node, "process")
    limit = payload.get("limit")
    project_id = payload.get("project_id")

    batch = P.claim_process_batch(
        db, node=node.name,
        limit=int(limit) if limit else None,
        project_id=int(project_id) if project_id else None,
    )
    db.commit()
    return JSONResponse({"ok": True, "count": len(batch), "items": batch})


@router.get("/source/{poster_id}")
def download_source(
    poster_id: int,
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Stream a raw poster to the node for processing.

    Source images stay on this server; the node pulls, processes, pushes the
    derivative to the storage box, and deletes its temp copy. That keeps the
    Windows box stateless and disposable.
    """
    _require_capability(node, "process")
    poster = db.query(SavedPoster).filter_by(id=poster_id).first()
    if poster is None or poster.deleted_at is not None:
        raise HTTPException(404, "Poster not found.")

    path = saved_poster_path(poster)
    if not path.is_file():
        raise HTTPException(404, "Source file missing on disk.")
    db.commit()
    return FileResponse(path, filename=poster.filename)


@router.post("/process/report")
def process_report(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Report one image's Photoshop outcome.

    On success this also seeds pending upload rows for every enabled account
    in the project, so processed work flows into the upload queue with no
    separate scheduling step.

    Expected body:
        {poster_id, ok, storage_path, filename, file_size, width, height,
         duration_ms, script_version, error}
    """
    _require_capability(node, "process")
    poster_id = payload.get("poster_id")
    if not poster_id:
        raise HTTPException(400, "poster_id is required.")

    if payload.get("ok"):
        storage_path = payload.get("storage_path")
        filename = payload.get("filename")
        if not storage_path or not filename:
            raise HTTPException(400, "storage_path and filename are required on success.")
        try:
            processed = P.report_processed(
                db,
                poster_id=int(poster_id),
                node=node.name,
                storage_path=storage_path,
                filename=filename,
                file_size=payload.get("file_size"),
                width=payload.get("width"),
                height=payload.get("height"),
                duration_ms=payload.get("duration_ms"),
                version=payload.get("script_version"),
            )
        except ValueError as e:
            raise HTTPException(404, str(e))
        db.commit()
        return JSONResponse({"ok": True, "processed_image_id": processed.id})

    P.report_process_failure(
        db, poster_id=int(poster_id), node=node.name,
        error=payload.get("error") or "Unknown processing error",
    )
    db.commit()
    return JSONResponse({"ok": True, "recorded": "failure"})


# ═══════════════════════════════════════════════════════════════════════════
#  UPLOAD STAGE
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/upload/claim")
def upload_claim(
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Claim an upload batch for one account.

    The response is self-contained: account credentials, the effective
    selector map and timings, today's remaining quota, and the per-image
    title/keywords/description already rendered from the current templates.
    The node performs no lookups and holds no config.

    Batch size is capped by the account's remaining daily allowance, so the
    marketplace's limit is enforced server-side and can't be exceeded by a
    node running an old build.
    """
    _require_capability(node, "upload")
    result = P.claim_upload_batch(
        db, node=node.name,
        account_id=payload.get("account_id"),
        limit=payload.get("limit"),
        project_id=payload.get("project_id"),
    )
    db.commit()
    return JSONResponse({"ok": True, "count": len(result.get("items") or []), **result})


@router.get("/upload/image/{tracking_id}")
def download_processed(
    tracking_id: int,
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Fallback delivery of a processed image.

    Normally the node reads the file straight off the mounted storage box
    using `storage_path` — no transfer needed. This endpoint exists for the
    case where the box isn't mounted (or a test is being run from a
    different machine) so a misconfigured mount degrades to slow rather
    than broken.
    """
    _require_capability(node, "upload")
    tracking = db.query(UploadTracking).filter_by(id=tracking_id).first()
    if tracking is None:
        raise HTTPException(404, "Tracking row not found.")

    processed = (
        db.query(ProcessedImage)
          .filter_by(id=tracking.processed_image_id)
          .first()
    )
    if processed is None:
        raise HTTPException(404, "No processed derivative recorded.")

    root = str(P.get_setting(db, "storage_root"))
    candidate = Path(root) / processed.storage_path
    if not candidate.is_file():
        raise HTTPException(
            404,
            f"Processed file not readable by the server at {candidate}. "
            "The node should read it from its own storage mount instead.",
        )
    db.commit()
    return FileResponse(candidate, filename=processed.filename)


@router.post("/upload/report")
def upload_report(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Report one image's upload outcome. Called immediately after each image,
    which is what makes a mid-batch crash cost at most one image.

    A node signals a *systemic* problem (bot check, rejected credentials,
    missing form field) with `pause_minutes`: the account is parked so the
    remaining queue isn't burned through, and the dashboard shows why.

    Expected body:
        {tracking_id, ok, remote_id, error, screenshot,
         pause_minutes, pause_reason}
    """
    _require_capability(node, "upload")
    tracking_id = payload.get("tracking_id")
    if not tracking_id:
        raise HTTPException(400, "tracking_id is required.")

    if payload.get("ok"):
        P.report_uploaded(
            db, tracking_id=int(tracking_id), node=node.name,
            remote_id=payload.get("remote_id"),
        )
        db.commit()
        return JSONResponse({"ok": True})

    P.report_upload_failure(
        db, tracking_id=int(tracking_id), node=node.name,
        error=payload.get("error") or "Unknown upload error",
        screenshot=payload.get("screenshot"),
        pause_minutes=int(payload.get("pause_minutes") or 0),
        pause_reason=payload.get("pause_reason"),
        # Defaults to True so an OLDER node, which does not send this field,
        # keeps the previous pause-immediately behaviour rather than silently
        # getting a new policy it was never tested against.
        pause_immediate=bool(payload.get("pause_immediate", True)),
    )
    db.commit()
    return JSONResponse({"ok": True, "recorded": "failure"})


@router.post("/upload/quota")
def upload_quota(
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Current quota for one account or all of them. The node checks this before
    starting a run so it can skip straight to sleeping when everything is
    already at its daily cap.
    """
    _require_capability(node, "upload")
    account_id = payload.get("account_id")

    query = db.query(UploadAccount).filter(UploadAccount.is_enabled == 1)
    if account_id:
        query = query.filter(UploadAccount.id == int(account_id))

    out = []
    for account in query.all():
        quota = P.account_quota(db, account)
        out.append({
            "account_id": account.id,
            "name": account.name,
            "target_site": account.target_site,
            "available": P.account_is_available(account),
            "paused_until": account.paused_until.isoformat() if account.paused_until else None,
            **quota,
        })
    db.commit()
    return JSONResponse({"ok": True, "accounts": out})


# ═══════════════════════════════════════════════════════════════════════════
#  JOBS — batch bookkeeping and Test & Debug
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/jobs/claim")
def jobs_claim(
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Take the next queued job.

    Test jobs jump the queue (see pipeline.claim_job) — when you're iterating
    on a broken selector you want your one-image test to run immediately, not
    behind a full batch.

    Test job payloads are resolved server-side into everything the node needs
    so a test never depends on node-local state:
      test_download → the title's source images
      test_process  → one image + the rendered script
      test_upload   → one processed image + account creds + selectors
    """
    kinds = payload.get("kinds")
    if kinds and not isinstance(kinds, list):
        kinds = [kinds]

    # Never hand a node work it isn't provisioned for.
    allowed: list[str] = []
    if _has_capability(node, "process"):
        allowed += ["process", "test_download", "test_process"]
    if _has_capability(node, "upload"):
        allowed += ["upload", "test_upload"]
    if kinds:
        allowed = [k for k in allowed if k in kinds]
    if not allowed:
        return JSONResponse({"ok": True, "job": None})

    job = P.claim_job(db, node=node.name, kinds=allowed)
    if job is None:
        db.commit()
        return JSONResponse({"ok": True, "job": None})

    try:
        payload_data = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError):
        payload_data = {}

    project = P.resolve_project(db, job.project_id)
    resolved = _resolve_job_payload(db, job, payload_data, project)
    P.append_job_log(db, job, f"Claimed by {node.name}")
    db.commit()

    return JSONResponse({
        "ok": True,
        "job": {
            "id": job.id,
            "kind": job.kind,
            "project": project.slug,
            "payload": resolved,
        },
    })


def _resolve_job_payload(
    db: Session, job: PipelineJob, payload: dict, project,
) -> dict:
    """
    Expand a job's stored payload into a fully self-contained instruction.

    Test jobs reference DB ids; the node gets concrete paths, credentials,
    templates and script text. This is deliberately done here rather than on
    the node so that "test with one image" exercises the exact same settings
    resolution a real batch would.
    """
    resolved = dict(payload)

    if job.kind == "test_download":
        master_id = payload.get("master_id")
        title = db.query(MasterTitle).filter_by(id=master_id).first()
        if title is None:
            return {**resolved, "error": "Title not found."}
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.master_title_id == title.id,
                      SavedPoster.deleted_at.is_(None))
              .order_by(SavedPoster.created_at.asc())
              .all()
        )
        resolved["title"] = title.title
        resolved["items"] = [
            {"poster_id": p.id,
             "filename": p.filename,
             "source_url": f"/api/pipeline/source/{p.id}"}
            for p in posters
        ]

    elif job.kind == "test_process":
        poster_id = payload.get("poster_id")
        poster = db.query(SavedPoster).filter_by(id=poster_id).first()
        if poster is None:
            return {**resolved, "error": "Poster not found."}
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        rel_path, filename = P.storage_path_for(db, title, poster, project=project)
        resolved.update({
            "poster_id": poster.id,
            "source_url": f"/api/pipeline/source/{poster.id}",
            "source_filename": poster.filename,
            # Test output is written beside the real archive under a _tests
            # prefix, so experiments never overwrite a live derivative.
            "storage_path": f"_tests/{rel_path}",
            "output_filename": filename,
            "settings": P.process_settings_payload(db, project=project),
        })

    elif job.kind == "test_upload":
        tracking_id = payload.get("tracking_id")
        poster_id = payload.get("poster_id")
        account_id = payload.get("account_id")

        tracking = None
        if tracking_id:
            tracking = db.query(UploadTracking).filter_by(id=tracking_id).first()
        elif poster_id and account_id:
            tracking = (
                db.query(UploadTracking)
                  .filter_by(saved_poster_id=int(poster_id), account_id=int(account_id))
                  .first()
            )
        if tracking is None:
            return {**resolved, "error": "No upload row for that image/account pair."}

        account = db.query(UploadAccount).filter_by(id=tracking.account_id).first()
        poster = db.query(SavedPoster).filter_by(id=tracking.saved_poster_id).first()
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        processed = db.query(ProcessedImage).filter_by(id=tracking.processed_image_id).first()
        if account is None or processed is None:
            return {**resolved, "error": "Account or processed derivative missing."}

        resolved.update({
            "tracking_id": tracking.id,
            "storage_path": processed.storage_path,
            "filename": processed.filename,
            "remote_title": P.render_remote_title(
                db, title, poster, tracking.letter_index or 0, project=project),
            "keywords": P.render_keywords(db, title, project=project),
            "description": P.render_description(db, title, project=project),
            "account": P.account_payload(db, account, include_secret=True),
            "settings": P.upload_settings_payload(db, project=project),
            # A test never mutates tracking state — it only reports phases.
            "dry_run_tracking": True,
        })

    return resolved


@router.post("/jobs/{job_id}/log")
def jobs_log(
    job_id: int,
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Append log lines and optionally update progress.

    The node calls this as it works — per phase, per image — because the
    whole point of the Live Console is watching a failure happen rather than
    reconstructing it afterwards.
    """
    job = db.query(PipelineJob).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(404, "Job not found.")

    lines = payload.get("lines")
    if isinstance(lines, str):
        lines = [lines]
    for line in (lines or []):
        P.append_job_log(db, job, str(line), level=payload.get("level", "info"))

    if payload.get("progress") is not None:
        try:
            job.progress = max(0, min(100, int(payload["progress"])))
        except (TypeError, ValueError):
            pass
    if payload.get("note"):
        job.progress_note = str(payload["note"])[:255]

    db.commit()
    return JSONResponse({"ok": True})


@router.post("/jobs/{job_id}/finish")
def jobs_finish(
    job_id: int,
    payload: dict = Body(default={}),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """Close out a job with its result or error."""
    job = db.query(PipelineJob).filter_by(id=job_id).first()
    if job is None:
        raise HTTPException(404, "Job not found.")

    ok = bool(payload.get("ok"))
    P.finish_job(
        db, job, ok=ok,
        result=payload.get("result"),
        error=payload.get("error"),
    )
    P.append_job_log(
        db, job,
        "Finished successfully" if ok else f"Failed: {payload.get('error') or 'unknown'}",
        level="ok" if ok else "error",
    )
    db.commit()
    return JSONResponse({"ok": True})


# ═══════════════════════════════════════════════════════════════════════════
#  FAILURE ARTEFACTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/artifact")
async def upload_artifact(
    request: Request,
    kind: str = Query("screenshot"),
    name: str = Query(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Accept a failure screenshot or page-source dump.

    Stored server-side (not on the disposable node) under
    workspace/_pipeline_artifacts/ so the dashboard can show you exactly what
    the browser was looking at when an upload failed — which is the single
    most useful thing when a marketplace changes its form.

    Returns the relative path to record on the tracking row.
    """
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty artifact body.")
    if len(body) > 20 * 1024 * 1024:
        raise HTTPException(413, "Artifact too large.")

    # Flatten any path traversal — the node supplies this name.
    safe_name = os.path.basename(name).replace("\\", "_")[:180]
    if not safe_name:
        raise HTTPException(400, "Bad artifact name.")

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    folder = WORKSPACE_DIR / "_pipeline_artifacts" / kind
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stamp}_{node.name}_{safe_name}"
    target.write_bytes(body)

    rel = f"_pipeline_artifacts/{kind}/{target.name}"
    db.commit()
    return JSONResponse({"ok": True, "path": rel})
