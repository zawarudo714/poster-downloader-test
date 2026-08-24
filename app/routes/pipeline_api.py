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

@router.post("/earnings/page")
def earnings_page(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    One page of a marketplace ledger, as the node's browser saw it.

    The node fetches and posts; this parses, stores and answers whether to
    keep going. The stop rule ("the first row we already have") needs the
    database, so it has to be decided here — which is also why the node asks
    after every page instead of being handed a page count it would have to
    guess.

    Committed per page, so a node that dies halfway through a first-ever read
    keeps everything up to that point and tomorrow carries on rather than
    starting again.
    """
    _require_capability(node, "upload")
    from ..earnings import service as earnings_service

    account = db.query(UploadAccount).filter_by(
        id=payload.get("account_id")).first()
    if account is None:
        raise HTTPException(404, "Account not found.")

    html = payload.get("html") or ""
    if not html.strip():
        raise HTTPException(400, "Empty page.")

    try:
        result = earnings_service.store_page(
            db, account=account,
            kind=(payload.get("kind") or "ledger"),
            page=int(payload.get("page") or 1),
            url=payload.get("url") or "",
            html=html,
        )
    except earnings_service.SignedOutError as e:
        # Its own branch because it is the one read failure a PERSON fixes,
        # and in two minutes. The account is parked so the scheduler stops
        # knocking on the sign-in page — repeated knocking is what got us
        # challenged in the first place — and the reason says what to do.
        db.rollback()
        # Stops READING only. Uploading to this account is a separate
        # capability and may be working perfectly.
        earnings_service.pause_reading(account, hours=12, reason=str(e))
        job_id = payload.get("job_id")
        if job_id:
            job = db.query(PipelineJob).filter_by(id=int(job_id)).first()
            if job is not None:
                P.append_job_log(db, job, str(e), level="error")
        db.commit()
        return JSONResponse({"ok": False, "more": False,
                             "needs_signin": True, "error": str(e)})

    except Exception as e:
        # Reported rather than raised: a parsing failure on page 7 must not
        # look to the node like a network fault, and the job log is where the
        # admin will read it.
        db.rollback()
        job_id = payload.get("job_id")
        if job_id:
            job = db.query(PipelineJob).filter_by(id=int(job_id)).first()
            if job is not None:
                P.append_job_log(db, job,
                                 f"Could not read page {payload.get('page')}: "
                                 f"{type(e).__name__}: {e}", level="error")
                db.commit()
        return JSONResponse({"ok": False, "more": False,
                             "error": f"{type(e).__name__}: {e}"})

    job_id = payload.get("job_id")
    if job_id:
        job = db.query(PipelineJob).filter_by(id=int(job_id)).first()
        if job is not None:
            P.append_job_log(
                db, job,
                f"{payload.get('kind')} page {payload.get('page')}: "
                f"{result['stored']} new, {result.get('matched', 0)} matched")
            db.commit()

    return JSONResponse({"ok": True, **result})


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
        # earnings_read rides on the upload capability on purpose: it is the
        # same browser, the same account and the same login. A separate
        # capability would be one more thing to provision on every node for
        # no gain today.
        allowed += ["upload", "test_upload", "earnings_read",
                    "profile_cleanup",
                    # Listing health rides on the upload capability for the
                    # same reason earnings reads do: same browser, same
                    # accounts, same Chrome profiles. A separate capability
                    # would be one more thing to provision for no gain.
                    "store_scan", "store_deactivate", "store_reactivate"]
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

    if job.kind == "profile_cleanup":
        # The far-end sanity check. The instruction was written when the
        # account was deleted; by the time a node picks it up the account may
        # have been recreated with the same id — SQLite reuses the number of
        # the most recently deleted row. Deleting its profile then would take
        # a LIVE session with it.
        account_id = payload.get("account_id")
        if db.query(UploadAccount).filter_by(id=account_id).first() is not None:
            return {**resolved,
                    "error": f"Account {account_id} exists again — not "
                             f"deleting its profile."}
        # The PATH is deliberately not computed here. Only the node knows
        # where its own scratch folder is, and this server importing the
        # node's browser module would drag Selenium into an image that has no
        # use for it. We send who it was; the node works out where, with the
        # same function it uses to launch Chrome.
        return resolved

    if job.kind == "earnings_read":
        # Everything the node needs to sign in and fetch: the account with
        # its decrypted password, the selectors and timings, and the page
        # URLs. All resolved HERE, so the node holds no marketplace
        # configuration of its own — same rule as every other job.
        from ..earnings import service as earnings_service

        account = db.query(UploadAccount).filter_by(
            id=payload.get("account_id")).first()
        if account is None:
            return {**resolved, "error": "Account not found."}

        attached = P.project_ids_for_account(db, account.id)
        acct_project = P.resolve_project(db, attached[0] if attached else None)
        resolved.update({
            "account": P.account_payload(db, account, include_secret=True,
                                         project=acct_project),
            "settings": P.upload_settings_payload(db, project=acct_project),
            "pages": earnings_service.page_urls(db, account),
            "max_pages": int(P.get_setting(db, "earnings_max_pages_per_run")),
            # Whether to sign in is a property of the MARKETPLACE, decided
            # here and not sniffed on the node. FineArtAmerica drops the
            # session and must sign in; TeePublic holds it for weeks and
            # challenges anyone who keeps knocking. Declared in CAPABILITIES
            # so marketplace number three answers this by adding a line.
            "signin_on_read": earnings_service.signin_on_read(
                (account.target_site or "").lower()),
        })

        # ── The interstitial wall ────────────────────────────────────────
        # The words that mean "this is the account page" come from the SAME
        # place the parser gets them, so the node's idea of success and the
        # server's cannot drift apart. Enough paths for every attempt are
        # handed over up front: the node must be able to finish its retries
        # even if it briefly loses contact after starting.
        from ..earnings import wall
        site = (account.target_site or "").lower()
        attempts = int(P.get_setting(db, "wall_max_attempts"))
        resolved.update({
            "wall_markers": earnings_service.page_markers(site),
            "signed_out_markers": earnings_service.signed_out_markers(site),
            "wall_paths": wall.payload_for(
                wall.next_paths(db, site, attempts)),
            "wall_wait_s": P.get_setting(db, "wall_wait_s"),
            "wall_max_attempts": attempts,
        })
        db.commit()          # the rotation cursor moved
        return resolved

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
            "account": P.account_payload(db, account, include_secret=True,
                                         project=project),
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

    # A failed READ has to cool down, and until now nothing made it.
    # `report_upload_failure` covers uploading; an earnings job reported its
    # error into the log and nothing else, so the next scheduled run walked
    # straight back into the same bot wall. Three hours matches the pause an
    # upload takes for the same cause.
    if not ok and job.kind == "earnings_read":
        from ..earnings import service as earnings_service
        try:
            account_id = int(json.loads(job.payload_json or "{}")
                             .get("account_id") or 0)
        except (TypeError, ValueError):
            account_id = 0
        account = (db.query(UploadAccount).filter_by(id=account_id).first()
                   if account_id else None)
        # Skip if a page handler already parked it with a more specific
        # reason — "signed out, sign in by hand" beats "the read failed".
        if account is not None and not earnings_service.read_paused(account):
            earnings_service.pause_reading(
                account, hours=3,
                reason=f"Last read failed: {payload.get('error') or 'unknown'}")
            P.append_job_log(
                db, job,
                f"Not reading {account.name} again for 3 hours. Uploading "
                f"to it is unaffected.", level="warn")

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


# ═══════════════════════════════════════════════════════════════════════════
#  THE INTERSTITIAL WALL — recorded mouse paths
# ═══════════════════════════════════════════════════════════════════════════
#
# These endpoints serve TWO callers on the same machine: the agent, which
# replays paths, and the recorder tool the owner runs by hand to create them.
# Both carry the node token, because both ARE the node — the recorder is not a
# dashboard feature, it has to run where the mouse and the browser are.

@router.post("/wall/paths")
def wall_save_path(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Accept one recording from the recorder tool.

    Stored on the SERVER, deliberately, even though it was captured on the
    node. The node holds no configuration by design — it is meant to be
    rebuildable from nothing — and an evening's recording is exactly the kind
    of thing that must survive that box being thrown away.
    """
    from ..earnings import wall

    try:
        row = wall.save_path(
            db,
            marketplace=str(payload.get("marketplace") or "").lower(),
            points=payload.get("points") or [],
            page_width=payload.get("page_width"),
            page_height=payload.get("page_height"),
            label=payload.get("label"),
            created_by=node.name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    db.commit()
    return JSONResponse({"ok": True, "id": row.id, "label": row.label})


@router.get("/wall/paths")
def wall_list_paths(
    marketplace: str = Query(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """Everything recorded for a marketplace — the recorder's own list."""
    from ..earnings import wall
    return JSONResponse({"ok": True,
                         "paths": wall.overview(db, marketplace.lower())})


@router.delete("/wall/paths/{path_id}")
def wall_delete_path(
    path_id: int,
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """Bin a recording that does not work."""
    from ..models import WallPath
    row = db.query(WallPath).filter_by(id=path_id).first()
    if row is None:
        raise HTTPException(404, "No such path.")
    db.delete(row)
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/wall/result")
def wall_result(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Which path was tried, and whether it got through.

    Counted for REPORTING only — never to choose the next path. The question
    this answers is "is it one bad recording or has the wall changed", and
    only per-path counts can tell those apart.
    """
    from ..earnings import wall
    wall.record_outcome(db, int(payload.get("path_id") or 0),
                        worked=bool(payload.get("worked")))
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/wall/path")
def wall_one_path(
    path_id: int = Query(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """One recording in full, for the recorder's replay button."""
    from ..earnings import wall
    from ..models import WallPath

    row = db.query(WallPath).filter_by(id=path_id).first()
    if row is None:
        raise HTTPException(404, "No such path.")
    payload = wall.payload_for([row])
    if not payload:
        raise HTTPException(422, "That recording has no usable points.")
    return JSONResponse(payload[0])


@router.get("/wall/record-target")
def wall_record_target(
    marketplace: str = Query(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    Everything the recorder needs to put the wall on screen.

    It opens the page through the SAME uploader the agent uses, with a real
    account's profile — because the wall has to be the genuine article, in the
    genuine window size, or the coordinates describe a layout that will never
    be seen again.

    Any readable account for that marketplace will do; the wall is a property
    of the site, not of the account.
    """
    from ..earnings import service as earnings_service

    site = marketplace.lower()
    account = next(
        (a for a in earnings_service.readable_accounts(db, include_paused=True)
         if (a.target_site or "").lower() == site), None)
    if account is None:
        raise HTTPException(
            404, f"No {site} account exists yet — add one before recording.")

    attached = P.project_ids_for_account(db, account.id)
    project = P.resolve_project(db, attached[0] if attached else None)
    pages = earnings_service.page_urls(db, account)
    if not pages or not pages[0].get("url"):
        raise HTTPException(422, f"No page URL is configured for {site}.")

    return JSONResponse({
        "ok": True,
        "url": pages[0]["url"],
        "account": P.account_payload(db, account, include_secret=True,
                                     project=project),
        "settings": P.upload_settings_payload(db, project=project),
        "markers": earnings_service.page_markers(site),
    })


# ═══════════════════════════════════════════════════════════════════════════
#  MARKETPLACE LISTING HEALTH
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/store/catalogue")
def store_catalogue(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    The full list of designs an account currently shows, reconciled.

    Sent once per account, before any checking. This is what turns a sweep
    into a CATALOGUE: the first run creates it, every later run records only
    what changed — designs added, designs that came back, designs the
    marketplace no longer lists.

    Returns the designs the node should actually check, which is where
    `missing_only` and the owner's exclusions are applied. The node does not
    decide any of that; it asks.
    """
    from ..earnings import store_health as SH
    from ..models import StoreScanRun

    run = db.query(StoreScanRun).filter_by(id=payload.get("run_id")).first()
    if run is None:
        raise HTTPException(404, "No such run.")
    account = db.query(UploadAccount).filter_by(
        id=payload.get("account_id")).first()
    if account is None:
        raise HTTPException(404, "Account not found.")

    changes = SH.sync_catalogue(db, account=account,
                                marketplace=run.marketplace,
                                seen=payload.get("designs") or [])
    db.commit()

    todo = SH.scannable_listings(db, run, account_id=account.id)
    return JSONResponse({
        "ok": True,
        "changes": changes,
        "check": [{"design_id": r.design_id, "url": r.url,
                   "title": r.title} for r in todo],
        "stop": run.status != "scanning" or bool(run.paused_at),
    })


@router.post("/store/design")
def store_design_result(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    One design's verdict from the scan, written into the catalogue.

    Per design rather than per account, and committed immediately. A scan is
    hours long; a node that dies four hours in must keep everything it had
    already checked, and tomorrow carry on rather than start again.
    """
    from ..earnings import store_health as SH
    from ..models import StoreScanRun

    run = db.query(StoreScanRun).filter_by(id=payload.get("run_id")).first()
    if run is None:
        raise HTTPException(404, "No such run.")

    SH.record_check(
        db,
        account_id=int(payload["account_id"]),
        design_id=str(payload["design_id"]),
        status=payload.get("status") or "error",
        title=payload.get("title"),
        search_tag=payload.get("search_tag"),
        url=payload.get("url"),
        error=payload.get("error"),
    )
    db.commit()

    # ── THIS REPLY IS HOW A SCAN GETS STOPPED OR PAUSED ──────────────────
    #
    # The node has no other way to hear about a button pressed here. It
    # posts one of these per design anyway, so the answer rides along for
    # free — no polling, no second endpoint, and the longest a stop can take
    # is one design.
    #
    # The node used to throw this reply away, which is why STOP appeared to
    # do nothing: the screen said "nothing running" while the node carried
    # on scanning for another twenty minutes.
    return JSONResponse({
        "ok": True,
        "stop": run.status != "scanning" or bool(run.paused_at),
        "reason": "paused" if run.paused_at else run.status,
    })


@router.post("/store/action")
def store_action_result(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    One design turned off, or back on — or the reason it would not.

    `deactivated_at` is what reactivation later works from, so it must be
    written the moment it happens. A stage that reported only at the end
    would, on falling over halfway, lose the record of everything it had
    already deactivated — and those are exactly the designs that would then
    never be switched back on.

    A completed cure — off and then on again — bumps `fix_attempts`. That
    counter is the evidence behind the vague-tag flag: a design still
    missing after several cures almost certainly has a tag too broad for a
    25-page search rather than a broken listing.
    """
    from ..models import StoreListing

    row = db.query(StoreListing).filter_by(
        account_id=payload.get("account_id"),
        design_id=str(payload.get("design_id") or "")).first()
    if row is None:
        raise HTTPException(404, "That design is not in the catalogue.")

    error = payload.get("error")
    row.action_error = error
    if not error:
        if payload.get("action") == "deactivate":
            row.deactivated_at = datetime.utcnow()
        else:
            row.deactivated_at = None
            row.fix_attempts = (row.fix_attempts or 0) + 1
            row.last_fixed_at = datetime.utcnow()
            # Its state is now genuinely unknown until something rechecks it.
            # Leaving it as "missing" would be a claim we have not earned;
            # calling it "visible" would be a lie. Unknown is the honest one
            # and it is what the missing-only recheck looks for.
            row.status = "unknown"
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/store/stage-done")
def store_stage_done(
    payload: dict = Body(...),
    node: WorkerNode = Depends(require_node),
    db: Session = Depends(get_db),
):
    """
    A stage finished. Move the run to whatever comes next.

    The run advances HERE rather than on the node, because what comes next is
    policy: on a manual run it is a person, on an automatic one it is the
    next stage, and after reactivation the run is over and the pipeline
    restarts. The node knows none of that and should not.
    """
    from ..earnings import store_health as SH
    from ..models import StoreScanRun

    run = db.query(StoreScanRun).filter_by(id=payload.get("run_id")).first()
    if run is None:
        raise HTTPException(404, "No such run.")

    stage = payload.get("stage")

    # A stage that failed must NOT advance the run. Ending it here is also
    # what releases the pipeline — otherwise a run that died on its first
    # account would hold Photoshop and uploads indefinitely while the screen
    # showed it politely "scanning".
    error = (payload.get("error") or "").strip()
    if error:
        # ── THE FAR SIDE HAVING A MOMENT IS NOT A FAILED RUN ─────────────
        #
        # A wall or a maintenance page passes. Giving up on one throws away
        # the whole unattended night — which is exactly what happened at
        # 23:14 one evening, costing six and a half hours. So a transient
        # failure sleeps and comes back, with a growing gap, and only gives
        # up once spaced attempts have also failed. It does not hold the
        # pipeline while it waits.
        if payload.get("transient"):
            minutes = SH.schedule_retry(db, run, reason=error[:300])
            if minutes:
                db.commit()
                return JSONResponse({"ok": True, "status": run.status,
                                     "retry_in_min": minutes})

        SH.finish_run(db, run, status="failed",
                      note=f"{stage} failed — {error[:400]}")
        db.commit()
        return JSONResponse({"ok": True, "status": run.status})

    tally = SH.counts(db, run, run.marketplace)

    # ── A SCAN THAT WAS CUT SHORT HAS NOT FINISHED ───────────────────────
    #
    # Pausing or stopping ends the job cleanly, with every design reported
    # and no failure — indistinguishable from success unless the node says
    # so. Advancing here would put a half-covered scan at the review gate,
    # and on an automatic run the next thing to happen would be a mass
    # deactivation based on designs we never actually looked at.
    #
    # The run stays where it is. Resuming re-queues the scan and carries on.
    if payload.get("partial") or run.paused_at:
        run.stage_note = (f"Stopped early — {tally['checked']} of "
                          f"{tally['total']} checked so far.")
        db.commit()
        return JSONResponse({"ok": True, "status": run.status,
                             "partial": True})

    if stage == "scan" and run.status == "scanning":
        run.status = "reviewing"
        run.stage_note = (f"{tally['missing']} missing of {tally['checked']} "
                          f"checked.")
    elif stage == "deactivate" and run.status == "deactivating":
        run.status = "confirming"
        run.stage_note = (f"{tally['deactivated']} deactivated"
                          + (f", {tally['action_errors']} refused"
                             if tally["action_errors"] else "") + ".")
    elif stage == "reactivate" and run.status == "reactivating":
        # The last stage, so this is also where the pipeline is released —
        # by the run ENDING, not by anyone flipping anything back.
        SH.finish_run(db, run, status="done",
                      note=f"{tally['missing']} were missing; "
                           f"all have been switched off and back on.")

    # ── AUTOMATIC MODE HANDS OVER HERE ───────────────────────────────────
    #
    # `next_stage` returns None for a manual run and for a paused one, so an
    # automatic run is the only thing that moves without a button. It also
    # returns None once reactivation is done, because there is nothing after
    # it — the run has already ended above.
    #
    # If the next stage turns out to have no work, the run ENDS rather than
    # sitting in a stage with nothing in it holding the pipeline.
    nxt = SH.next_stage(run)
    if nxt:
        queued = SH.dispatch_stage(db, run, nxt, by="auto")
        if not queued:
            SH.finish_run(db, run, status="done",
                          note="Nothing left to do — every design was visible.")
        db.commit()

    return JSONResponse({"ok": True, "status": run.status, "next": nxt})
