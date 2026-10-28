"""
Sending paintings (step 1) and flattens (step 2) to OpenAI's Batch API.

Asked for by the owner on 2026-10-28: half the price, answered within 24
hours instead of seconds. With 1,100 photos waiting and a marketplace that
takes 100 uploads a day, a day's delay costs nothing and half price is real.

════════════════════════════════════════════════════════════════════════════
THE LIFE OF A BATCH
════════════════════════════════════════════════════════════════════════════
    send_due()  build a JSONL file of requests → upload it → create the batch
    poll()      every batch_poll_min minutes, ask OpenAI how it is doing
    collect()   once OpenAI says it is finished (or expired, or cancelled),
                download the answers and file each picture

All three run inside the painting thread (gpt_worker._cycle), one at a time,
so nothing here races itself. The web pages only READ batches — apart from
CANCEL, which asks OpenAI to stop and lets the next poll collect.

════════════════════════════════════════════════════════════════════════════
WHAT "IN FLIGHT" MEANS, AND WHERE IT IS WRITTEN
════════════════════════════════════════════════════════════════════════════
  · A PAINTING (step 1) in a batch is claimed exactly like one being painted
    now: its poster is 'processing', claimed_by "batch:<id>". claimed_at is
    left EMPTY on purpose — the stale-claim reaper only frees claims with a
    time on them, and a batch may honestly take 24 hours, which would read as
    abandoned against a 45-minute timeout (the "a timeout is a guessed
    duration" rule in CLAUDE.md). The worker cannot change a 'processing'
    picture, exactly as today.
  · A FLATTEN (step 2) needs no claim: its painting stays 'step2', and "is it
    at OpenAI" is an open AIBatchItem. step2_queue() leaves those out.

════════════════════════════════════════════════════════════════════════════
EVERY EXIT REPORTS (rule 8)
════════════════════════════════════════════════════════════════════════════
  · the send fails          → batch 'send_failed', every picture back in the
                              queue, OpenAI's words on the batch row
  · the server restarts mid-send → recover_unsent() at startup does the same
  · a line succeeds          → filed (or 'dropped' if the picture changed
                              while it was away — never filed over a newer one)
  · a line fails             → the same failure record a live call writes
  · a line never comes back  → (expired, cancelled) back in the queue
  · the answers cannot be downloaded → the batch stays open and the next
                              poll tries again; items already filed are not
                              filed twice, because each item is marked.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

# OpenAI's own words for a batch that will not change again.
TERMINAL = ("completed", "failed", "expired", "cancelled")

# The last problem met per project, for the Pipeline page — in memory, like
# the painter's heartbeat, because it describes this process.
_errors: dict[str, dict] = {}


def note_error(project, text: str) -> None:
    _errors[project.slug] = {"text": text[:600], "at": datetime.utcnow()}


def last_error(project) -> Optional[dict]:
    e = _errors.get(project.slug)
    if not e:
        return None
    return {"text": e["text"], "at": e["at"].isoformat() + "Z"}


def _tmp_dir() -> Path:
    from .config import WORKSPACE_DIR
    d = WORKSPACE_DIR / "_batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Reading the state ──────────────────────────────────────────────────────

def open_batches(db: Session, project):
    from .models import AIBatch
    return (db.query(AIBatch)
              .filter(AIBatch.project_id == project.id,
                      AIBatch.collected_at.is_(None))
              .order_by(AIBatch.id.asc()))


def open_item_ids(db: Session, project, *, stage: int) -> list[int]:
    """For step 2: the PAINTING ids at OpenAI right now. For step 1: the
    poster ids. Read from the items, never from a counter."""
    from .models import AIBatch, AIBatchItem
    col = AIBatchItem.processed_id if stage == 2 else AIBatchItem.saved_poster_id
    return [i for (i,) in
            db.query(col)
              .join(AIBatch, AIBatch.id == AIBatchItem.batch_id)
              .filter(AIBatch.project_id == project.id,
                      AIBatch.stage == stage,
                      AIBatch.collected_at.is_(None),
                      AIBatchItem.outcome.is_(None)).all()
            if i is not None]


def summary(db: Session, project, *, limit: int = 12) -> list[dict]:
    """The newest batches, for the progress panel."""
    from .models import AIBatch, AIBatchItem
    from sqlalchemy import func
    rows = (db.query(AIBatch).filter(AIBatch.project_id == project.id)
              .order_by(AIBatch.id.desc()).limit(limit).all())
    out = []
    for b in rows:
        outcomes = dict(db.query(AIBatchItem.outcome, func.count(AIBatchItem.id))
                          .filter(AIBatchItem.batch_id == b.id)
                          .group_by(AIBatchItem.outcome).all())
        items = sum(outcomes.values())
        out.append({
            "id": b.id, "stage": b.stage, "status": b.status,
            "items": items,
            # OpenAI's own counts while it works; ours once collected.
            "openai_total": b.total, "openai_done": b.done,
            "openai_failed": b.failed,
            "filed": outcomes.get("filed", 0),
            "failed": outcomes.get("failed", 0),
            "back_in_queue": outcomes.get("retry", 0),
            "dropped": outcomes.get("dropped", 0),
            "created_at": b.created_at.isoformat() + "Z" if b.created_at else "",
            "expires_at": b.expires_at.isoformat() + "Z" if b.expires_at else "",
            "checked_at": b.last_checked_at.isoformat() + "Z" if b.last_checked_at else "",
            "collected_at": b.collected_at.isoformat() + "Z" if b.collected_at else "",
            "error": b.error or "",
            "open": b.collected_at is None,
            "cancel_requested_by": b.cancel_requested_by or "",
        })
    return out


# ── Recovery ───────────────────────────────────────────────────────────────

def _release_stage1(db: Session, batch) -> int:
    """Posters still claimed by this batch go back in the paint queue."""
    from .models import MasterTitle, SavedPoster
    from .pipeline import recompute_title_status
    n = 0
    for poster in (db.query(SavedPoster)
                     .filter(SavedPoster.pipeline_status == "processing",
                             SavedPoster.claimed_by == f"batch:{batch.id}").all()):
        poster.pipeline_status = "greenlit"
        poster.claimed_by = None
        poster.claimed_at = None
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title is not None:
            recompute_title_status(db, title)
        n += 1
    return n


def recover_unsent(db: Session) -> int:
    """At startup: a batch still 'sending' was cut short by a restart."""
    from .models import AIBatch, AIBatchItem
    now = datetime.utcnow()
    stuck = db.query(AIBatch).filter(AIBatch.status == "sending",
                                     AIBatch.collected_at.is_(None)).all()
    for b in stuck:
        b.status = "send_failed"
        b.error = "The server restarted while this batch was being sent. " \
                  "Its pictures went back in the queue."
        b.collected_at = now
        (db.query(AIBatchItem)
           .filter(AIBatchItem.batch_id == b.id, AIBatchItem.outcome.is_(None))
           .update({AIBatchItem.outcome: "retry", AIBatchItem.finished_at: now},
                   synchronize_session=False))
        if b.stage == 1:
            _release_stage1(db, b)
    # And the repair for rows any FUTURE exit forgets: a picture still held
    # by a batch that is already closed goes back in the queue. Watched by
    # diagnostics.check_batch_claims_have_an_open_batch.
    from .models import SavedPoster
    open_ids = {f"batch:{i}" for (i,) in
                db.query(AIBatch.id).filter(AIBatch.collected_at.is_(None)).all()}
    for poster in (db.query(SavedPoster)
                     .filter(SavedPoster.pipeline_status == "processing",
                             SavedPoster.claimed_by.like("batch:%")).all()):
        if poster.claimed_by not in open_ids:
            poster.pipeline_status = "greenlit"
            poster.claimed_by = None
            poster.claimed_at = None
    return len(stuck)


# ── Sending ────────────────────────────────────────────────────────────────

def _recently_refused(db: Session, project, stage: int) -> bool:
    """
    A send OpenAI REFUSED (a bad key, no credit, a model it will not batch)
    is not tried again for an hour. Re-uploading a 180 MB file every ten
    minutes to be refused identically helps nobody; the reason is on the
    Pipeline page, and switching batch off sends the work the normal way.
    """
    from .models import AIBatch
    last = (db.query(AIBatch)
              .filter(AIBatch.project_id == project.id, AIBatch.stage == stage)
              .order_by(AIBatch.id.desc()).first())
    return bool(last is not None and last.status == "send_failed"
                and (last.error or "").startswith("[refused]")
                and last.created_at > datetime.utcnow() - timedelta(hours=1))


def send_due(db: Session, project) -> bool:
    """
    Send everything waiting, if a send is due. Returns True if anything went.

    Due = no batch sent for this project in the last batch_gather_min
    minutes. EVERYTHING waiting goes in one send (split across files by
    OpenAI's 200 MB limit), so a catch-up of 689 is one send, not 70 —
    capped by batch_max_items as a brake on a mistake.
    """
    from .models import AIBatch
    from .pipeline import get_setting, step2_active
    from .gpt_worker import paint_queue, step2_queue
    gather = int(get_setting(db, "batch_gather_min", project=project) or 0)
    cap = max(1, int(get_setting(db, "batch_max_items", project=project) or 1))
    last = (db.query(AIBatch.created_at)
              .filter(AIBatch.project_id == project.id)
              .order_by(AIBatch.id.desc()).first())
    if last and last[0] > datetime.utcnow() - timedelta(minutes=gather):
        return False

    from . import gpt_images as G
    sent = 0
    if step2_active(db, project) and not _recently_refused(db, project, 2):
        work = step2_queue(db, project).limit(cap).all()
        # A SETTING that fails every picture alike stops this step's send
        # before anything is claimed, instead of failing each picture (v279).
        problem = G.settings_problem(db, project, stage=2) if work else None
        if problem:
            note_error(project, problem)
        elif work:
            sent += _send(db, project, stage=2, work=work)
    room = cap - sent
    if room > 0 and not _recently_refused(db, project, 1):
        work = paint_queue(db, project).limit(room).all()
        problem = G.settings_problem(db, project, stage=1) if work else None
        if problem:
            note_error(project, problem)
        elif work:
            sent += _send(db, project, stage=1, work=work)
    return sent > 0


def _send(db: Session, project, *, stage: int, work: list) -> int:
    """
    Build the request lines for `work`, and send them as one or more
    batches. Returns how many pictures went.

    A picture whose request cannot be BUILT (its photo is missing, it is too
    big for a batch line) is recorded as failed right here, exactly as a
    live call would record it, and the rest go on without it.
    """
    from . import gpt_images as G
    from .pipeline import get_setting
    from .storage_remote import StorageError, read_bytes
    from .config import WORKSPACE_DIR
    from .gpt_worker import record_paint_failure, record_step2_failure
    from .utils import saved_poster_path

    api_key = G.api_key_for(db, project)
    style_rel = str(get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / style_rel if style_rel else Path("")
    model = str(get_setting(db, "step2_model" if stage == 2 else "openai_model",
                            project=project))

    files: list[tuple[Path, list[dict]]] = []
    path = _tmp_dir() / f"{project.slug}_s{stage}_{uuid.uuid4().hex[:10]}.jsonl"
    fh = open(path, "w", encoding="utf-8")
    size = 0
    items: list[dict] = []
    try:
        for entry in work:
            try:
                if stage == 2:
                    painting, poster, title = entry
                    master = read_bytes(db, painting.master_path or painting.storage_path,
                                        project=project)
                    png, dims = G.step2_input(master)
                    fields = G.request_fields(db, project, stage=2,
                                              location=(title.title or ""),
                                              input_size=dims)
                    parts = G.step2_images(db, project, painting_png=png, style=style)
                else:
                    poster, title = entry
                    painting = None
                    fields = G.request_fields(db, project, stage=1,
                                              location=(title.title or ""))
                    parts = G.step1_images(db, project,
                                           source=saved_poster_path(poster),
                                           style=style)
                body = G.batch_body(fields, parts)
            except G.PermanentFailure as e:
                if stage == 2 and e.kind == "bad_request" and "prompt is empty" in str(e):
                    raise          # a setting, not this picture — stop the send
                if stage == 2:
                    record_step2_failure(db, painting, project, e)
                else:
                    record_paint_failure(db, poster, title, project, e)
                db.commit()
                continue
            except (StorageError, OSError) as e:
                if stage == 2:
                    record_step2_failure(db, painting, project,
                                         G.TransientFailure(f"could not read the painting: {e}"))
                else:
                    record_paint_failure(db, poster, title, project,
                                         G.TransientFailure(f"could not read the photo: {e}"))
                db.commit()
                continue

            cid = f"s{stage}-p{poster.id}-{uuid.uuid4().hex[:12]}"
            line = json.dumps({"custom_id": cid, "method": "POST",
                               "url": "/v1/images/edits", "body": body}) + "\n"
            if items and size + len(line) > G.BATCH_FILE_MAX_BYTES:
                fh.close()
                files.append((path, items))
                path = _tmp_dir() / f"{project.slug}_s{stage}_{uuid.uuid4().hex[:10]}.jsonl"
                fh = open(path, "w", encoding="utf-8")
                size, items = 0, []
            fh.write(line)
            size += len(line)
            items.append({"cid": cid, "poster_id": poster.id,
                          "processed_id": painting.id if painting is not None else None})
        fh.close()
        if items:
            files.append((path, items))
        else:
            path.unlink(missing_ok=True)

        sent = 0
        for fpath, fitems in files:
            sent += _send_one_file(db, project, stage=stage, model=model,
                                   api_key=api_key, path=fpath, items=fitems)
        return sent
    finally:
        try:
            fh.close()
        except Exception:                                # noqa: BLE001
            pass
        for fpath, _ in files:
            fpath.unlink(missing_ok=True)
        path.unlink(missing_ok=True)


def _send_one_file(db: Session, project, *, stage: int, model: str,
                   api_key: str, path: Path, items: list[dict]) -> int:
    from . import gpt_images as G
    from .models import AIBatch, AIBatchItem, SavedPoster

    # 1 · The record and the claims, COMMITTED before anything leaves this
    #     server, so a crash from here on is something recover_unsent() can
    #     see and undo.
    batch = AIBatch(project_id=project.id, stage=stage, model=model,
                    status="sending", total=len(items))
    db.add(batch)
    db.flush()
    claimed = []
    for it in items:
        if stage == 1:
            # Conditional, like every claim: if the picture moved on since
            # the queue was read, it is simply left out of this batch.
            n = (db.query(SavedPoster)
                   .filter(SavedPoster.id == it["poster_id"],
                           SavedPoster.pipeline_status.in_(("greenlit", "failed_processing")))
                   .update({SavedPoster.pipeline_status: "processing",
                            SavedPoster.claimed_by: f"batch:{batch.id}",
                            SavedPoster.claimed_at: None},
                           synchronize_session=False))
            if n != 1:
                continue
        db.add(AIBatchItem(batch_id=batch.id, saved_poster_id=it["poster_id"],
                           processed_id=it["processed_id"], custom_id=it["cid"]))
        claimed.append(it["cid"])
    db.commit()
    if not claimed:
        batch.status = "send_failed"
        batch.error = "Nothing was left to send — every picture moved on."
        batch.collected_at = datetime.utcnow()
        db.commit()
        return 0
    if len(claimed) != len(items):
        # Rewrite the file without the pictures that moved on, so OpenAI is
        # never paid for a picture we would then refuse to file.
        keep = set(claimed)
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines(True)
                 if json.loads(ln)["custom_id"] in keep]
        path.write_text("".join(lines), encoding="utf-8")
        batch.total = len(claimed)

    # 2 · Up it goes.
    try:
        file_id = G.batch_upload_file(api_key, path)
        batch.input_file_id = file_id
        info = G.batch_create(api_key, file_id)
    except (G.PermanentFailure, G.TransientFailure, OSError) as e:
        refused = isinstance(e, G.PermanentFailure)
        db.rollback()
        batch = db.query(AIBatch).filter_by(id=batch.id).first()
        now = datetime.utcnow()
        batch.status = "send_failed"
        batch.error = (("[refused] " if refused else "[could not send] ")
                       + str(e))[:4000]
        batch.collected_at = now
        (db.query(AIBatchItem)
           .filter(AIBatchItem.batch_id == batch.id, AIBatchItem.outcome.is_(None))
           .update({AIBatchItem.outcome: "retry", AIBatchItem.finished_at: now,
                    AIBatchItem.error: batch.error[:500]},
                   synchronize_session=False))
        if stage == 1:
            _release_stage1(db, batch)
        db.commit()
        note_error(project, batch.error)
        log.error("Batch %s for %s could not be sent: %s", batch.id, project.slug, e)
        return 0

    _apply_info(batch, info)
    db.commit()
    log.info("Batch %s sent: %s step-%s picture(s), OpenAI id %s",
             batch.id, len(claimed), stage, batch.openai_batch_id)
    return len(claimed)


def _apply_info(batch, info: dict) -> None:
    """Copy OpenAI's batch object onto our row."""
    batch.openai_batch_id = info.get("id") or batch.openai_batch_id
    batch.status = str(info.get("status") or batch.status)[:24]
    counts = info.get("request_counts") or {}
    batch.total = int(counts.get("total") or batch.total or 0)
    batch.done = int(counts.get("completed") or 0)
    batch.failed = int(counts.get("failed") or 0)
    batch.output_file_id = info.get("output_file_id") or batch.output_file_id
    batch.error_file_id = info.get("error_file_id") or batch.error_file_id
    if info.get("expires_at"):
        try:
            batch.expires_at = datetime.utcfromtimestamp(int(info["expires_at"]))
        except (TypeError, ValueError, OverflowError):
            pass
    errs = (info.get("errors") or {}).get("data") if isinstance(info.get("errors"), dict) else None
    if errs:
        batch.error = "; ".join(str(e.get("message") or e) for e in errs)[:4000]
    batch.last_checked_at = datetime.utcnow()


# ── Watching and collecting ────────────────────────────────────────────────

def poll(db: Session, project) -> bool:
    """Ask OpenAI about each open batch that is due a look; collect the
    finished ones. Returns True if anything was filed."""
    from . import gpt_images as G
    from .pipeline import get_setting
    batches = [b for b in open_batches(db, project).all() if b.status != "sending"]
    if not batches:
        return False
    every = int(get_setting(db, "batch_poll_min", project=project) or 3)
    now = datetime.utcnow()
    api_key = None
    did = False
    for b in batches:
        due = not b.last_checked_at or b.last_checked_at <= now - timedelta(minutes=every)
        # A running batch is asked every few minutes; a finished one whose
        # answers would not download waits the same few minutes between tries.
        if not due and (b.status not in TERMINAL
                        or (b.error or "").startswith("could not download")):
            continue
        if api_key is None:
            api_key = G.api_key_for(db, project)
        if b.status not in TERMINAL:
            try:
                info = G.batch_get(api_key, b.openai_batch_id)
            except (G.PermanentFailure, G.TransientFailure) as e:
                b.last_checked_at = now
                b.error = f"could not check it: {e}"[:4000]
                db.commit()
                continue
            _apply_info(b, info)
            db.commit()
        if b.status in TERMINAL:
            if collect(db, project, b, api_key=api_key):
                did = True
    return did


def collect(db: Session, project, batch, *, api_key: str) -> bool:
    """
    File every answer of a finished batch, then close it. Safe to run twice:
    an item that already has an outcome is skipped, so a download that broke
    half-way is simply finished on the next poll.
    """
    from . import gpt_images as G
    from .models import AIBatchItem
    filed_any = False
    pending = {it.custom_id: it for it in
               db.query(AIBatchItem).filter(AIBatchItem.batch_id == batch.id,
                                            AIBatchItem.outcome.is_(None)).all()}
    for file_id in (batch.output_file_id, batch.error_file_id):
        if not file_id or not pending:
            continue
        dest = _tmp_dir() / f"result_{batch.id}_{file_id[-12:]}.jsonl"
        try:
            G.file_download(api_key, file_id, dest)
        except (G.PermanentFailure, G.TransientFailure) as e:
            batch.error = f"could not download the answers yet: {e}"[:4000]
            batch.last_checked_at = datetime.utcnow()     # wait before trying again
            db.commit()
            dest.unlink(missing_ok=True)
            return filed_any          # the batch stays open; next poll retries
        try:
            with open(dest, encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        line = json.loads(raw)
                    except ValueError:
                        continue
                    item = pending.pop(line.get("custom_id") or "", None)
                    if item is None:
                        continue
                    item_id = item.id
                    try:
                        if _file_line(db, project, batch, item, line):
                            filed_any = True
                    except Exception as e:                   # noqa: BLE001
                        # ONE bad answer must not hold the other 400
                        # hostage, and must not be re-downloaded for ever:
                        # it is recorded as failed and the next line goes on.
                        db.rollback()
                        _fail_unexpected(db, project, batch, item_id, e)
        finally:
            dest.unlink(missing_ok=True)

    # Anything OpenAI never answered (expired, cancelled, or a batch that
    # failed validation) goes back in the queue to be sent again.
    now = datetime.utcnow()
    for item in pending.values():
        item.outcome = "retry"
        item.finished_at = now
        item.error = f"no answer — the batch ended '{batch.status}'"
    if batch.stage == 1:
        _release_stage1(db, batch)
    batch.collected_at = now
    db.commit()
    return filed_any


def _file_line(db: Session, project, batch, item, line: dict) -> bool:
    """File one answer. Returns True if a picture was filed."""
    from . import gpt_images as G
    from .models import MasterTitle, ProcessedImage, SavedPoster
    from .gpt_worker import (file_painting, file_step2, record_paint_failure,
                             record_step2_failure, step2_still_wanted)

    now = datetime.utcnow()
    resp = line.get("response") or {}
    status = int(resp.get("status_code") or 0)
    body = resp.get("body")
    poster = db.query(SavedPoster).filter_by(id=item.saved_poster_id).first()
    title = (db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
             if poster is not None else None)
    painting = (db.query(ProcessedImage).filter_by(id=item.processed_id).first()
                if item.processed_id else None)

    def done(outcome: str, error: str = "") -> None:
        item.outcome = outcome
        item.error = error[:2000] or None
        item.finished_at = now
        db.commit()

    if poster is None or title is None or (batch.stage == 2 and painting is None):
        done("dropped", "the picture no longer exists")
        return False

    if batch.stage == 1:
        still = (poster.deleted_at is None
                 and poster.pipeline_status == "processing"
                 and poster.claimed_by == f"batch:{batch.id}")
        if not still:
            done("dropped", "the picture changed while it was at OpenAI")
            return False
    elif not step2_still_wanted(db, painting, poster):
        done("dropped", "the painting stopped waiting for step 2 while it was away")
        return False

    if status != 200:
        err = G.classify_line_error(status, body if body is not None else line.get("error"))
        if batch.stage == 1:
            record_paint_failure(db, poster, title, project, err)
        else:
            record_step2_failure(db, painting, project, err)
        done("failed", str(err))
        return False

    try:
        gen = G.parse_image_payload(body or {}, started=datetime.utcnow().timestamp())
        gen.duration_ms = 0
    except G.TransientFailure as e:
        if batch.stage == 1:
            record_paint_failure(db, poster, title, project, e)
        else:
            record_step2_failure(db, painting, project, e)
        done("failed", str(e))
        return False

    ok = (file_painting(db, poster, title, project, gen) if batch.stage == 1
          else file_step2(db, painting, poster, title, project, gen,
                          processed_by="batch"))
    done("filed" if ok else "retry", "" if ok else "could not be filed — see the picture")
    return ok


def _fail_unexpected(db: Session, project, batch, item_id: int, err: Exception) -> None:
    """An answer that broke something while being filed: recorded as a
    failure on its picture (so it shows on Needs Attention), never retried
    by re-downloading the whole batch."""
    from . import gpt_images as G
    from .models import AIBatchItem, MasterTitle, ProcessedImage, SavedPoster
    from .gpt_worker import record_paint_failure, record_step2_failure
    item = db.query(AIBatchItem).filter_by(id=item_id).first()
    if item is None:
        return
    detail = f"{type(err).__name__}: {err}"
    poster = db.query(SavedPoster).filter_by(id=item.saved_poster_id).first()
    t = G.TransientFailure(f"the answer could not be filed — {detail}")
    if batch.stage == 1 and poster is not None \
            and poster.claimed_by == f"batch:{batch.id}":
        title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
        if title is not None:
            record_paint_failure(db, poster, title, project, t)
    elif batch.stage == 2 and item.processed_id:
        painting = db.query(ProcessedImage).filter_by(id=item.processed_id).first()
        if painting is not None and painting.review_status == "step2":
            record_step2_failure(db, painting, project, t)
    item.outcome = "failed"
    item.error = detail[:2000]
    item.finished_at = datetime.utcnow()
    db.commit()
    log.error("Batch %s: could not file %s: %s", batch.id, item.custom_id, detail)


def cancel(db: Session, project, batch_id: int, *, by: str) -> dict:
    """Ask OpenAI to stop a batch. What already finished is still paid for
    and still filed; the rest come back to the queue when it is collected."""
    from . import gpt_images as G
    from .models import AIBatch
    b = (db.query(AIBatch)
           .filter(AIBatch.id == batch_id, AIBatch.project_id == project.id).first())
    if b is None:
        raise LookupError("No such batch in this project.")
    if b.collected_at is not None or b.status in TERMINAL:
        return {"ok": True, "status": b.status, "note": "already finished"}
    if not b.openai_batch_id:
        return {"ok": False, "status": b.status, "note": "it has not reached OpenAI yet"}
    info = G.batch_cancel(G.api_key_for(db, project), b.openai_batch_id)
    _apply_info(b, info)
    b.cancel_requested_by = by
    b.last_checked_at = None          # look again on the very next pass
    return {"ok": True, "status": b.status}
