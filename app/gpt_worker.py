"""
The GPT processing loop — a background worker inside the web process.

════════════════════════════════════════════════════════════════════════════
WHY A THREAD AND NOT THE NODE'S CLAIM/LEASE MODEL
════════════════════════════════════════════════════════════════════════════
The Photoshop stage exists on another machine, so it needs a claim protocol:
hand out work, lease it, reap the lease if the box dies. That machinery earns
its keep because Windows genuinely can vanish mid-batch.

This runs in the same process that owns the database. There is no network
between the dispatcher and the queue, so a lease protects against nothing that
can actually happen. What it does still need is the SAME poster-level state
machine, so an image interrupted by a restart is not lost and not duplicated —
hence it still claims rows, and reap_stale_claims() still frees them.

════════════════════════════════════════════════════════════════════════════
ONE IMAGE AT A TIME, DELIBERATELY
════════════════════════════════════════════════════════════════════════════
Generation takes ~60s and is network-bound, so parallelism is tempting. It is
also how you discover a rate limit at 3am with 40 images half-done. Sequential
keeps the failure modes boring, keeps spend predictable per minute, and is
still far faster than Photoshop — which is the thing it replaced.

════════════════════════════════════════════════════════════════════════════
ORDER OF OPERATIONS, AND WHY
════════════════════════════════════════════════════════════════════════════
    generate -> upscale -> preview -> write to storage -> record row

Upscale BEFORE the review gate, so what the admin approves is what ships.
The upscale is a source of defects (softness, sharpening artefacts); reviewing
the 1024px original and shipping the 4000px enlargement would mean the gate
checks something other than what the customer receives.

The database row is written LAST. If the process dies mid-image, the poster is
still 'processing' with a stale claim and gets picked up again — rather than
being marked processed with nothing behind it.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

_thread: Optional[threading.Thread] = None
_stop = threading.Event()

IDLE_SLEEP_S = 20


# ── One image ───────────────────────────────────────────────────────────────

def process_one(db: Session, poster, title, project) -> bool:
    """
    Take one greenlit poster through the whole stage.

    Returns True if an image was produced. Raises nothing — every failure is
    recorded on the poster so the dashboard can explain it.
    """
    from . import gpt_images as G
    from .imagefetch import make_preview, upscale_to_width
    from .pipeline import get_setting, recompute_title_status, storage_path_for
    from .models import ProcessedImage
    from .storage_remote import StorageError, write_bytes
    from .utils import saved_poster_path
    from .config import WORKSPACE_DIR

    source = saved_poster_path(poster)
    style_rel = str(get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / style_rel if style_rel else Path("")

    try:
        gen = G.generate(db, source=source, style=style, project=project)
    except G.PermanentFailure as e:
        # Never retried automatically. GPT's own words are kept so the admin
        # can judge, and so a policy change a year from now is actionable.
        poster.pipeline_status = "failed_processing"
        poster.process_error = f"[{e.kind}] {e}"
        poster.claimed_at = None
        poster.claimed_by = None
        # Attempts pushed past the cap so no automatic retry can pick it up.
        poster.process_attempts = 999
        recompute_title_status(db, title)
        db.commit()
        log.warning("GPT permanently rejected poster %s: %s", poster.id, e)
        return False
    except G.TransientFailure as e:
        poster.pipeline_status = "greenlit"     # back in the queue
        poster.process_error = str(e)
        poster.process_attempts = (poster.process_attempts or 0) + 1
        poster.claimed_at = None
        poster.claimed_by = None
        max_attempts = int(get_setting(db, "process_max_attempts", project=project) or 3)
        if poster.process_attempts >= max_attempts:
            poster.pipeline_status = "failed_processing"
        recompute_title_status(db, title)
        db.commit()
        log.warning("GPT transient failure on poster %s: %s", poster.id, e)
        return False

    # Cost is recorded whether or not the rest succeeds — the money is spent
    # the moment OpenAI answers.
    G.record_spend(db, service="openai", operation="image_edit",
                   cost=gen.cost_usd(), project_id=project.id,
                   saved_poster_id=poster.id,
                   input_tokens=gen.input_tokens, output_tokens=gen.output_tokens)
    db.commit()

    rel_path, filename = storage_path_for(db, title, poster, project=project)
    full_rel = f"{rel_path}"

    # Write to a temp file so Pillow can work on it, then upscale in place.
    tmp = WORKSPACE_DIR / "_gpt_tmp" / f"{poster.id}.jpg"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(gen.image_bytes)

    width = int(get_setting(db, "upscale_width_px", project=project) or 4000)
    sharpen = int(get_setting(db, "upscale_sharpen", project=project) or 0)
    quality = int(get_setting(db, "upscale_jpeg_quality", project=project) or 92)
    out_w, out_h = upscale_to_width(tmp, width=width, sharpen=sharpen, quality=quality)

    preview_tmp = tmp.with_name(f"{poster.id}_preview.jpg")
    make_preview(tmp, preview_tmp)

    preview_rel = full_rel.rsplit("/", 1)
    preview_rel = (preview_rel[0] + "/previews/" + preview_rel[1]) if len(preview_rel) == 2 \
        else f"previews/{full_rel}"

    try:
        write_bytes(db, full_rel, tmp.read_bytes(), project=project)
        write_bytes(db, preview_rel, preview_tmp.read_bytes(), project=project)
    except StorageError as e:
        # The image exists and was paid for, but we could not file it. Treat
        # as transient — storage comes back, and re-running would spend again.
        poster.pipeline_status = "greenlit"
        poster.process_error = f"storage: {e}"
        poster.process_attempts = (poster.process_attempts or 0) + 1
        poster.claimed_at = None
        poster.claimed_by = None
        db.commit()
        log.error("Could not store processed image for poster %s: %s", poster.id, e)
        return False
    finally:
        tmp.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)

    # Supersede any previous generation rather than deleting it — a rerun must
    # not destroy the evidence of what was rejected.
    db.query(ProcessedImage).filter(
        ProcessedImage.saved_poster_id == poster.id,
        ProcessedImage.is_current == 1,
    ).update({ProcessedImage.is_current: 0}, synchronize_session=False)

    prior = db.query(ProcessedImage).filter_by(saved_poster_id=poster.id).count()
    # Read per image, not once at startup: turning the gate off should take
    # effect on the next image, not on the next restart of the server.
    from .pipeline import review_gate_enabled
    gate = review_gate_enabled(db, project)

    processed = ProcessedImage(
        saved_poster_id=poster.id,
        project_id=project.id,
        storage_path=full_rel,
        filename=filename,
        file_size=len(gen.image_bytes),
        output_width=out_w,
        output_height=out_h,
        script_version=str(get_setting(db, "openai_model", project=project)),
        processed_by="server",
        duration_ms=gen.duration_ms,
        is_current=1,
        attempt=prior + 1,
        preview_path=preview_rel,
        review_status="pending" if gate else None,
    )
    db.add(processed)
    # Flushed so it has an id. ensure_upload_rows() below stores that id on
    # each upload row, and would otherwise go looking for a current
    # derivative that has not reached the database yet.
    db.flush()

    # With a review gate the poster stays out of the upload queue until the
    # admin releases it, and the upload rows are created at that moment by
    # the review endpoint. WITHOUT a gate there is no later moment — so they
    # have to be created here, exactly as report_processed() does for the
    # Photoshop path. Miss this and turning the gate off silently stops
    # anything ever uploading.
    poster.pipeline_status = "processed"
    poster.process_error = None
    poster.claimed_at = None
    poster.claimed_by = None

    if not gate:
        from .pipeline import ensure_upload_rows
        ensure_upload_rows(db, poster=poster, title=title,
                           processed=processed, project=project)

    recompute_title_status(db, title)
    db.commit()
    return True


# ── The loop ────────────────────────────────────────────────────────────────

def _claim_next(db: Session, project):
    """
    Take the oldest waiting image for this project and mark it in progress.

    Same claim discipline as the node's, for the same reason: a restart
    mid-image must leave something the reaper can free, not a row that looks
    finished.
    """
    from .models import MasterTitle, SavedPoster
    from .pipeline import get_setting, project_scope, _default_project_id
    from sqlalchemy import or_

    max_attempts = int(get_setting(db, "process_max_attempts", project=project) or 3)
    row = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.deleted_at.is_(None),
                  SavedPoster.process_attempts < max_attempts,
                  or_(SavedPoster.pipeline_status == "greenlit",
                      SavedPoster.pipeline_status == "failed_processing"),
                  project_scope(project.id,
                                default_project_id=_default_project_id(db)))
          .order_by(SavedPoster.original_save_date.asc(), SavedPoster.id.asc())
          .first()
    )
    if row is None:
        return None, None
    poster, title = row
    poster.pipeline_status = "processing"
    poster.claimed_at = datetime.utcnow()
    poster.claimed_by = "server"
    db.commit()
    return poster, title


def _cycle() -> bool:
    """One pass over every GPT project. Returns True if any work was done."""
    from .db import SessionLocal
    from .models import Project
    from . import gpt_images as G

    db = SessionLocal()
    did_work = False
    try:
        projects = (
            db.query(Project)
              .filter(Project.is_active == 1, Project.processor == "gpt")
              .all()
        )
        for project in projects:
            state = G.cap_state(db, project=project)
            if state["over"] and state["action"] == "pause":
                log.warning("GPT stage paused: month-to-date spend $%s has reached "
                            "the $%s cap", state["spent"], state["cap"])
                continue

            poster, title = _claim_next(db, project)
            if poster is None:
                continue
            log.info("GPT: processing poster %s (%s)", poster.id, title.title)
            if process_one(db, poster, title, project):
                did_work = True
    except Exception as e:
        db.rollback()
        log.error("GPT cycle failed: %s", e)
    finally:
        db.close()
    return did_work


def _run() -> None:
    log.info("GPT image worker started")
    while not _stop.is_set():
        try:
            if not _cycle():
                _stop.wait(IDLE_SLEEP_S)
        except Exception as e:
            log.error("GPT worker loop error: %s", e)
            _stop.wait(IDLE_SLEEP_S)


def start_background_worker() -> None:
    """
    Start the loop, once, if any project actually uses GPT.

    Skipped entirely when no project declares processor='gpt', so an install
    that only does Photoshop carries no extra thread.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    from .db import SessionLocal
    from .models import Project
    db = SessionLocal()
    try:
        needed = (
            db.query(Project)
              .filter(Project.is_active == 1, Project.processor == "gpt")
              .count()
        )
    except Exception:
        needed = 0
    finally:
        db.close()

    if not needed:
        return

    _stop.clear()
    _thread = threading.Thread(target=_run, name="gpt-image-worker", daemon=True)
    _thread.start()
