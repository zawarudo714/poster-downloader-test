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

# ════════════════════════════════════════════════════════════════════════════
#  HEARTBEAT
# ════════════════════════════════════════════════════════════════════════════
# This stage has no node, no job rows and no queue of its own — it is a thread
# inside the web process. That makes it the quietest thing in the system when
# it fails: if it dies, or never starts, generation simply stops. Greenlit
# work piles up, nothing is marked failed, and the only visible symptom is a
# number that stops moving. You would notice in a day, maybe.
#
# So the loop stamps the time on every pass, idle or not, and something else
# watches that stamp. The state is deliberately in MEMORY rather than in the
# database: it describes a thread in THIS process, and a value surviving a
# restart would report a worker that is no longer running.
_health = {
    "started_at": None,     # when the thread was (re)started
    "last_tick": None,      # end of the most recent pass, busy or idle
    "last_error": "",       # last unhandled loop error, if any
    "processed": 0,         # images generated since this thread started
    "restarts": 0,          # times the supervisor had to revive it
}

# How stale the heartbeat may get before it counts as stopped. Generation
# takes ~60s per image and the idle wait is 20s, so a healthy loop stamps at
# least every couple of minutes even mid-image. Five gives real headroom
# without letting a dead worker sit unnoticed for long.
STALE_AFTER_S = 300


def health() -> dict:
    """
    Is generation actually running? Read by the dashboard and the supervisor.

    `alive` is the thread object's own view; `stale` is whether it has done
    anything recently. Both matter: a thread can be alive and wedged on a
    network call that never returns, which looks identical to working from
    the outside and is exactly the case a plain is-it-alive check misses.
    """
    alive = bool(_thread is not None and _thread.is_alive())
    last = _health["last_tick"]
    age = int((datetime.utcnow() - last).total_seconds()) if last else None
    return {
        **_health,
        "alive": alive,
        "age_s": age,
        "stale": bool(age is not None and age > STALE_AFTER_S),
        # "Should there be a worker at all" is answered by the caller, which
        # knows whether any project uses this processor.
    }


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
        from .pipeline import intake_open

        for project in projects:
            # Paused or draining. The loop keeps ticking (so the heartbeat
            # stays fresh and the watchdog does not "revive" a worker that is
            # deliberately idle) — it simply claims nothing.
            if not intake_open(db, project):
                continue

            state = G.cap_state(db, project=project)
            if state["over"] and state["action"] == "pause":
                log.warning("GPT stage paused: month-to-date spend $%s has reached "
                            "the $%s cap", state["spent"], state["cap"])
                continue

            poster, title = _claim_next(db, project)
            if poster is None:
                continue
            # The claim above is COMMITTED, so the cycle-level rollback below
            # cannot undo it. `process_one` handles its three known failure
            # shapes and releases the claim in each; anything OUTSIDE those
            # used to escape to the cycle handler and strand the poster at
            # 'processing' — where, since the reaper began sparing nodes
            # that are visibly producing (v140), a busy worker's wedged
            # poster would never be freed at all. Rule 8: an exception must
            # not escape past the point where the work was claimed.
            #
            # `report_process_failure` is the same call the node's report
            # endpoint uses — releases the claim, bumps attempts, records
            # the error, and past max_attempts it surfaces in the failure
            # list instead of retrying forever.
            pid = poster.id
            try:
                if process_one(db, poster, title, project):
                    did_work = True
            except Exception as e:
                db.rollback()
                detail = f"{type(e).__name__}: {e}"
                log.error("GPT: poster %s failed unexpectedly: %s", pid, detail)
                from .pipeline import report_process_failure
                report_process_failure(db, poster_id=pid, node="server",
                                       error=detail)
                db.commit()
    except Exception as e:
        db.rollback()
        log.error("GPT cycle failed: %s", e)
    finally:
        db.close()
    return did_work


def _run() -> None:
    log.info("GPT image worker started")
    _health["started_at"] = datetime.utcnow()
    _health["last_tick"] = datetime.utcnow()
    # A worker that has just started is not processing anything — the same
    # rule the Windows node's first hello applies (v133/v140). Without this,
    # a claim stranded by a crash-and-restart waited for the reaper, and a
    # reaper that spares visibly-producing nodes would spare 'server' for as
    # long as this worker stays busy.
    try:
        from .db import SessionLocal
        from .pipeline import release_claims_for_node
        _db = SessionLocal()
        try:
            freed = release_claims_for_node(_db, "server")
            _db.commit()
            if freed["posters"] or freed["uploads"]:
                log.warning("GPT: released %s claim(s) left over from a "
                            "previous run", freed["posters"] + freed["uploads"])
        finally:
            _db.close()
    except Exception as e:
        log.error("GPT: could not release leftover claims: %s", e)
    while not _stop.is_set():
        try:
            did = _cycle()
            # Stamped on EVERY pass, including idle ones. A heartbeat that
            # only moved when there was work would read as "dead" during any
            # quiet period, which is most of the time.
            _health["last_tick"] = datetime.utcnow()
            _health["last_error"] = ""
            if did:
                _health["processed"] += 1
            else:
                _stop.wait(IDLE_SLEEP_S)
        except Exception as e:
            # The loop keeps going, but the reason is kept so the dashboard
            # can say WHY rather than just "not running".
            _health["last_error"] = str(e)[:400]
            _health["last_tick"] = datetime.utcnow()
            log.error("GPT worker loop error: %s", e)
            _stop.wait(IDLE_SLEEP_S)
    log.warning("GPT image worker loop exited")


def wanted(db) -> bool:
    """Does any ACTIVE project actually generate images here?"""
    from .models import Project
    try:
        return bool(
            db.query(Project)
              .filter(Project.is_active == 1, Project.processor == "gpt")
              .count()
        )
    except Exception:
        return False


def supervise() -> Optional[str]:
    """
    Called once a minute by the scheduler. Restarts the loop if it is gone.

    ════════════════════════════════════════════════════════════════════════
    WHY REVIVE RATHER THAN JUST REPORT
    ════════════════════════════════════════════════════════════════════════
    A warning that generation has stopped is only useful if someone is
    looking. The owner checks the dashboard when he thinks to, so a stopped
    worker could sit for a day behind a message nobody read. Restarting it
    turns "silent stoppage" into "brief pause", and the restart COUNT is
    what gets reported — a worker that keeps needing revival is a real
    problem, where one that was revived once at 3am is not.

    It also covers a case that is not a failure at all: a project switched
    to processor='gpt' after the server booted. At startup there was nothing
    to run, so no thread was made; this notices and starts one.

    Returns a short reason when it intervened, else None. Deliberately does
    NOT restart a thread that is merely stale — a wedged thread may still be
    inside a paid API call, and starting a second one could pay twice for the
    same image. That case is reported instead, which is the honest trade.
    """
    from .db import SessionLocal

    db = SessionLocal()
    try:
        if not wanted(db):
            return None
    finally:
        db.close()

    if _thread is not None and _thread.is_alive():
        return None

    _health["restarts"] += 1
    log.warning("GPT image worker was not running — restarting it (restart #%s)",
                _health["restarts"])
    start_background_worker()
    return f"restarted (#{_health['restarts']})"


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
    db = SessionLocal()
    try:
        needed = wanted(db)
    finally:
        db.close()

    if not needed:
        return

    _stop.clear()
    _thread = threading.Thread(target=_run, name="gpt-image-worker", daemon=True)
    _thread.start()
