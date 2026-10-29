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


# ── Paid pictures are kept until they are filed (v287) ─────────────────────
#
# A picture OpenAI has sent back is PAID FOR. Filing it takes two more
# things that can fail on their own: the Storage Box, and a database write
# that another writer can hold up ("database is locked"). Either failure
# used to throw the picture away and put the photo back in the queue, so the
# same picture was painted, and paid for, again.
#
# So the picture is written here FIRST, and deleted only after its row is
# committed. Anything that would otherwise pay for it again — the painter,
# the step-2 painter, the batch sender — looks here before going to OpenAI,
# and file_kept() files what is here at the start of every pass.
#
#   s1_<poster id>.png     a painting, waiting to be filed
#   s2_<painting id>.png   a step-2 picture, waiting to be filed

KEPT_RETRY_S = 120          # how long a kept picture waits between tries
_kept_tried: dict = {}


def _kept_dir() -> Path:
    from .config import WORKSPACE_DIR
    return WORKSPACE_DIR / "_gpt_tmp" / "paid"


def kept_path(stage: int, key: int) -> Path:
    return _kept_dir() / f"s{stage}_{int(key)}.png"


def keep_paid(stage: int, key: int, image_bytes: bytes) -> None:
    path = kept_path(stage, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_bytes(image_bytes)
    part.replace(path)          # whole or not at all


def kept_generation(stage: int, key: int):
    """The picture already paid for, as a Generation, or None."""
    from .gpt_images import Generation
    path = kept_path(stage, key)
    try:
        return Generation(image_bytes=path.read_bytes())
    except OSError:
        return None


def forget_paid(stage: int, key: int) -> None:
    kept_path(stage, key).unlink(missing_ok=True)
    _kept_tried.pop((stage, int(key)), None)


def has_kept(stage: int, key: int) -> bool:
    return kept_path(stage, key).is_file()


def file_kept(db: Session, project) -> bool:
    """
    File the pictures that were paid for but not filed. Returns True if any
    was filed. One that is no longer wanted (the photo was deleted, painted
    since, or the painting stopped waiting for step 2) is let go.
    """
    import time as _time
    from .models import MasterTitle, ProcessedImage, SavedPoster
    from .pipeline import project_for_title
    folder = _kept_dir()
    if not folder.is_dir():
        return False
    did = False
    for path in sorted(folder.glob("s[12]_*.png")):
        try:
            stage, key = int(path.stem[1]), int(path.stem.split("_", 1)[1])
        except ValueError:
            continue
        if _time.monotonic() - _kept_tried.get((stage, key), -1e9) < KEPT_RETRY_S:
            continue
        if stage == 1:
            poster = db.query(SavedPoster).filter_by(id=key).first()
            if poster is None:
                forget_paid(1, key)
                continue
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title is None or project_for_title(db, title).id != project.id:
                continue        # another project's pass files it
            if poster.deleted_at is not None or poster.pipeline_status in (
                    "processed", "uploaded", "unusable"):
                forget_paid(1, key)
                continue
            if poster.pipeline_status not in ("greenlit", "failed_processing"):
                continue        # out with the painter or a batch; filed there
            gen = kept_generation(1, key)
            if gen is None:
                continue
            _kept_tried[(1, key)] = _time.monotonic()
            poster.pipeline_status = "processing"
            poster.claimed_by = "server"
            poster.claimed_at = datetime.utcnow()
            db.commit()
            log.info("Filing painting for poster %s that was already paid for", key)
            try:
                if file_painting(db, poster, title, project, gen):
                    did = True
            except Exception as e:                       # noqa: BLE001
                db.rollback()
                again = db.query(SavedPoster).filter_by(id=key).first()
                if again is not None and again.pipeline_status == "processing":
                    again.pipeline_status = "greenlit"
                    again.claimed_by = None
                    again.claimed_at = None
                    again.process_error = f"filing the paid picture: {type(e).__name__}: {e}"
                    db.commit()
                log.error("Could not file the paid painting for poster %s: %s", key, e)
        else:
            painting = db.query(ProcessedImage).filter_by(id=key).first()
            if painting is None:
                forget_paid(2, key)
                continue
            if painting.project_id != project.id:
                continue
            poster = db.query(SavedPoster).filter_by(id=painting.saved_poster_id).first()
            title = (db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
                     if poster is not None else None)
            if title is None or not step2_still_wanted(db, painting, poster):
                forget_paid(2, key)
                continue
            gen = kept_generation(2, key)
            if gen is None:
                continue
            _kept_tried[(2, key)] = _time.monotonic()
            log.info("Filing step 2 for painting %s that was already paid for", key)
            try:
                if file_step2(db, painting, poster, title, project, gen):
                    did = True
            except Exception as e:                       # noqa: BLE001
                db.rollback()
                log.error("Could not file the paid step 2 for painting %s: %s", key, e)
    return did


# ── One image ───────────────────────────────────────────────────────────────

def process_one(db: Session, poster, title, project) -> bool:
    """
    Take one greenlit poster through the whole stage.

    Returns True if an image was produced. Raises nothing — every failure is
    recorded on the poster so the dashboard can explain it.
    """
    from . import gpt_images as G
    from .pipeline import get_setting
    from .utils import saved_poster_path
    from .config import WORKSPACE_DIR

    source = saved_poster_path(poster)
    style_rel = str(get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / style_rel if style_rel else Path("")

    kept = kept_generation(1, poster.id)
    if kept is not None:
        log.info("Poster %s: filing the painting already paid for", poster.id)
        return file_painting(db, poster, title, project, kept)

    try:
        # The title travels to the generator so the poster carries the FULL
        # place name. Until 2026-09-06 the prompt's {LOCATION} was never
        # filled in — sent verbatim — so the model captioned the PHOTO
        # instead, guessing e.g. "JAPAN" for Mount Fuji. The owner chose the
        # `title` column as the poster text.
        gen = G.generate(db, source=source, style=style, project=project,
                         location=(title.title or ""))
    except (G.PermanentFailure, G.TransientFailure) as e:
        record_paint_failure(db, poster, title, project, e)
        db.commit()
        return False

    # Spend metering was REMOVED in v172 at the owner's instruction. OpenAI's
    # own usage page is where he reads what this costs, and our figure only
    # ever duplicated it less accurately. The token counts OpenAI returns are
    # still on `gen` if anything ever wants them again.
    db.commit()
    return file_painting(db, poster, title, project, gen)


def record_paint_failure(db: Session, poster, title, project, err) -> None:
    """
    A painting that did not come back, recorded on the poster. ONE spelling
    for the live call and for a batch line, so a refusal at OpenAI reads the
    same on Needs Attention whichever way it was sent. Commits nothing.
    """
    from . import gpt_images as G
    from .pipeline import get_setting, recompute_title_status
    poster.claimed_at = None
    poster.claimed_by = None
    if isinstance(err, G.PermanentFailure):
        # Never retried automatically. GPT's own words are kept so the admin
        # can judge, and so a policy change a year from now is actionable.
        poster.pipeline_status = "failed_processing"
        poster.process_error = f"[{err.kind}] {err}"
        # Attempts pushed past the cap so no automatic retry can pick it up.
        poster.process_attempts = 999
        # The hand-named mark ends with the painting's last try. RETRY on
        # Needs Attention sets it again (v268).
        poster.paint_requested_at = None
        log.warning("GPT permanently rejected poster %s: %s", poster.id, err)
    else:
        poster.pipeline_status = "greenlit"     # back in the queue, mark kept
        poster.process_error = str(err)
        poster.process_attempts = (poster.process_attempts or 0) + 1
        max_attempts = int(get_setting(db, "process_max_attempts", project=project) or 3)
        if poster.process_attempts >= max_attempts:
            poster.pipeline_status = "failed_processing"
            poster.paint_requested_at = None
        log.warning("GPT transient failure on poster %s: %s", poster.id, err)
    recompute_title_status(db, title)


def file_painting(db: Session, poster, title, project, gen) -> bool:
    """
    File a PAINTING that came back — from the live call or from a batch —
    and record its row. Raises nothing it can catch; the caller owns the
    commit on failure paths it handles itself.
    """
    from .imagefetch import (flatten_onto,
                             has_transparency, make_preview,
                             upscale_to_width)
    from .pipeline import (default_background, get_setting,
                           recompute_title_status, step2_active,
                           storage_path_for)
    from .models import ProcessedImage
    from .storage_remote import StorageError, write_bytes
    from .config import WORKSPACE_DIR

    # WHICH GENERATION THIS IS, DECIDED BEFORE THE FILENAME IS BUILT.
    #
    # It used to be counted afterwards, purely to fill in a column. Now the
    # number is part of the path, so a rerun writes `..._v2.jpg` beside the
    # first one instead of on top of it — see storage_path_for() for why the
    # old behaviour quietly destroyed the picture it claimed to be keeping.
    #
    # MAX + 1, not COUNT + 1 (2026-09-14). Edited derivatives (Photopea)
    # share their parent's number and add a letter — v1b is a ROW but not
    # a GENERATION.
    # Counting rows would have named the rerun after v1 and v1b "v3", a
    # number with no v2 behind it, and its files likewise.
    from sqlalchemy import func as _func
    prior = (db.query(_func.max(ProcessedImage.attempt))
               .filter(ProcessedImage.saved_poster_id == poster.id)
               .scalar() or 0)
    attempt = prior + 1
    rel_path, filename = storage_path_for(db, title, poster, project=project,
                                          attempt=attempt)
    full_rel = f"{rel_path}"

    # ── The model's own output, kept exactly as it arrived ───────────────
    #
    # Saved BEFORE anything is done to it, and kept. Asked for a transparent
    # background this model renders differently and better for this niche,
    # and the see-through result is a side effect to be flattened away — but
    # WHICH colour is flattened on is a judgement the admin makes later, on
    # the review screen, looking at the picture.
    #
    # Keeping the original means changing that judgement costs a local
    # re-render rather than paying OpenAI for the picture a second time.
    raw = WORKSPACE_DIR / "_gpt_tmp" / f"{poster.id}_raw.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(gen.image_bytes)
    # Paid for: kept until its row is committed (see keep_paid).
    keep_paid(1, poster.id, gen.image_bytes)

    tmp = WORKSPACE_DIR / "_gpt_tmp" / f"{poster.id}.jpg"
    # One colour or top-into-bottom, from the dashboard (default_background).
    background = default_background(db, project)
    transparent = has_transparency(raw)

    # FLATTEN FIRST, ENLARGE SECOND. With no alpha channel left there is
    # nothing for the resize to average the hidden colour into. The safe
    # order, and it costs nothing — see imagefetch.flatten_onto() for why the
    # measurement behind this is weaker than it first looked.
    # Flatten to a LOSSLESS intermediate, then enlarge straight into the
    # final JPEG. Encoding once instead of twice keeps JPEG artefacts out of
    # the picture that gets magnified to print size (2026-09-06).
    flat = tmp.with_name(f"{poster.id}_flat.png")
    flatten_onto(raw, flat, background)

    width = int(get_setting(db, "upscale_width_px", project=project) or 4000)
    sharpen = int(get_setting(db, "upscale_sharpen", project=project) or 0)
    quality = int(get_setting(db, "upscale_jpeg_quality", project=project) or 92)

    # ════════════════════════════════════════════════════════════════════
    #  THE PRINT FILE IS BUILT WHEN IT IS APPROVED, NOT HERE (2026-09-09)
    # ════════════════════════════════════════════════════════════════════
    # The 4000px file used to be made right here, and then made AGAIN on
    # approval whenever the admin changed the background colour. Adding the
    # signature would have made that second rebuild happen every single
    # time, so the work would be done twice for every poster.
    #
    # Building it once, at approval, with the colour AND the signature
    # together, is strictly LESS work than today and keeps the single JPEG
    # encode the quality rules ask for.
    #
    # DEFERRING IS ONLY SAFE WHEN BOTH HALVES ARE TRUE, and the condition
    # says exactly that rather than assuming this project's shape:
    #
    #   · there is a MASTER to rebuild from. An opaque generation has none,
    #     so there would be nothing to build later.
    #   · there is a REVIEW GATE, which is the later moment. Without one
    #     nothing ever approves, so nothing would ever build the file and
    #     the uploader would be handed a path with no file behind it.
    #
    # Get either wrong and the failure is silent, which is why it is one
    # expression read once rather than two checks in different places.
    from .pipeline import review_gate_enabled
    gate = review_gate_enabled(db, project)
    defer_print_file = bool(transparent and gate)

    if defer_print_file:
        # The preview comes from the FLATTENED picture rather than from the
        # print file, because the print file does not exist yet. Same
        # picture, same colour — just not enlarged first, which the preview
        # was only ever throwing away anyway.
        # The finished size is arithmetic, not a measurement of a file that
        # does not exist yet: the enlargement keeps the shape, so the height
        # follows from the width. Reading the header of the flattened file
        # costs nothing and means the review screen can still say how big
        # the print will be before it has been made.
        from PIL import Image as _Image
        with _Image.open(flat) as _probe:
            src_w, src_h = _probe.size
        out_w = width
        out_h = max(1, round(src_h * (width / src_w)))
        preview_source = flat
    else:
        out_w, out_h = upscale_to_width(flat, width=width, sharpen=sharpen,
                                        quality=quality, dest=tmp)
        preview_source = tmp

    preview_tmp = tmp.with_name(f"{poster.id}_preview.jpg")
    make_preview(preview_source, preview_tmp)

    preview_rel = full_rel.rsplit("/", 1)
    preview_rel = (preview_rel[0] + "/previews/" + preview_rel[1]) if len(preview_rel) == 2 \
        else f"previews/{full_rel}"
    # Beside the finished file, under its own name. Only written when there
    # is actually something to keep — an opaque generation has no master to
    # go back to, and storing a second identical copy would be waste.
    master_rel = (full_rel.rsplit(".", 1)[0] + "_master.png") if transparent else None

    try:
        if not defer_print_file:
            write_bytes(db, full_rel, tmp.read_bytes(), project=project)
        write_bytes(db, preview_rel, preview_tmp.read_bytes(), project=project)
        if master_rel:
            write_bytes(db, master_rel, raw.read_bytes(), project=project)
        # A RERUN overwrites these deterministic paths with new bytes, but
        # the review cache is keyed on the path — so without this the
        # Approve-Artwork screen keeps serving the OLD picture and the rerun
        # looks like it did nothing (owner's find, 2026-09-06).
        from .review_cache import clear as _clear_review_cache
        _clear_review_cache([full_rel, preview_rel, master_rel])
    except StorageError as e:
        # The image exists and was paid for, but we could not file it. Treat
        # as transient — storage comes back. The picture itself is KEPT
        # (keep_paid), so the next try files it rather than paying again.
        poster.pipeline_status = "greenlit"
        poster.process_error = f"storage: {e} (the picture is kept, not paid for again)"
        poster.process_attempts = (poster.process_attempts or 0) + 1
        poster.claimed_at = None
        poster.claimed_by = None
        db.commit()
        log.error("Could not store processed image for poster %s: %s", poster.id, e)
        return False
    finally:
        tmp.unlink(missing_ok=True)
        flat.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)

    # The newest generation becomes the one the uploader would use. The older
    # ones keep their rows AND, since 2026-09-09, their own files — so the
    # review screen can offer them and the admin can go back to the third
    # attempt after a fourth came out worse.
    db.query(ProcessedImage).filter(
        ProcessedImage.saved_poster_id == poster.id,
        ProcessedImage.is_current == 1,
    ).update({ProcessedImage.is_current: 0}, synchronize_session=False)

    processed = ProcessedImage(
        saved_poster_id=poster.id,
        project_id=project.id,
        # EMPTY UNTIL THE FILE EXISTS. A path is a promise that something
        # is there, and while the print file is deferred nothing is. That
        # emptiness is also how the approval knows it has to build rather
        # than merely recolour — one fact, read from one place, instead of
        # a second flag that could disagree with the disc.
        storage_path=("" if defer_print_file else full_rel),
        filename=filename,
        file_size=len(gen.image_bytes),
        output_width=out_w,
        output_height=out_h,
        script_version=str(get_setting(db, "openai_model", project=project)),
        processed_by="server",
        duration_ms=gen.duration_ms,
        is_current=1,
        attempt=attempt,
        preview_path=preview_rel,
        # With STEP 2 on, a new painting goes on to be flattened instead of
        # to the owner — he reviews the step-2 result (v266).
        review_status=(("step2" if step2_active(db, project) else "pending")
                       if gate else None),
        master_path=master_rel,
        # The colour ALREADY baked into storage_path, not a request for one.
        # Recording it is what lets the review screen know whether a change
        # actually needs a re-render or is a no-op.
        background_color=background if transparent else None,
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
    # Painted: the hand-named mark is spent here and nowhere earlier (v268).
    poster.paint_requested_at = None

    if not gate:
        from .pipeline import ensure_upload_rows
        ensure_upload_rows(db, poster=poster, title=title,
                           processed=processed, project=project)

    recompute_title_status(db, title)
    db.commit()
    forget_paid(1, poster.id)
    return True


# ── Step 2: the flatten, made from a painting (v266) ────────────────────────

def step2_still_wanted(db: Session, painting, poster) -> bool:
    """
    Is this painting still the one waiting for step 2? Asked again when an
    answer comes back — from a live call it is seconds later, from a batch it
    may be a day later, and in that time the worker may have swapped the
    picture, the owner may have pressed REPAINT, or the picture may be gone.
    An answer for a painting nobody is waiting on any more is not filed.
    """
    db.refresh(painting)
    db.refresh(poster)
    return (poster.deleted_at is None
            and (poster.pipeline_status or "") in ("processed", "uploaded")
            and painting.is_current == 1
            and (painting.review_status or "") == "step2")


def record_step2_failure(db: Session, painting, project, err) -> None:
    """A step-2 try that did not come back, recorded on the PAINTING."""
    from . import gpt_images as G
    from .pipeline import get_setting
    if isinstance(err, G.PermanentFailure):
        painting.review_status = "step2_failed"
        painting.step2_error = f"[{err.kind}] {err}"[:4000]
        painting.step2_attempts = 999
        log.warning("Step 2 refused for painting %s: %s", painting.id, err)
        return
    painting.step2_error = str(err)[:4000]
    painting.step2_attempts = (painting.step2_attempts or 0) + 1
    cap = int(get_setting(db, "step2_max_attempts", project=project) or 3)
    if painting.step2_attempts >= cap:
        painting.review_status = "step2_failed"
    log.warning("Step 2 transient failure on painting %s: %s", painting.id, err)


def step2_names(db: Session, title, poster, painting, project) -> dict:
    """
    Where a step-2 picture is filed: in the poster's own folder, named after
    the painting it came from, so the folder reads as one family —

        Kyoto_v2_master.png   the painting (v2)
        Kyoto_v2_step2.png    step 2 made from it, exactly as OpenAI sent it
        Kyoto_v2_step2.jpg    the print file, built when it is approved

    A redo of step 2 on the same painting gets its own name (_step2-2, …),
    so nothing ever overwrites an older picture.
    """
    from .models import ProcessedImage
    from .pipeline import storage_path_for
    n = 1 + (db.query(ProcessedImage.id)
               .filter(ProcessedImage.saved_poster_id == poster.id,
                       ProcessedImage.step2_from == painting.id,
                       ProcessedImage.variant.is_(None)).count())
    base_rel, base_name = storage_path_for(db, title, poster, project=project,
                                           attempt=painting.attempt or 1)
    stem = base_rel.rsplit(".", 1)[0]
    name_stem = base_name.rsplit(".", 1)[0]
    tag = "_step2" if n == 1 else f"_step2-{n}"
    print_rel = f"{stem}{tag}.jpg"
    bits = print_rel.rsplit("/", 1)
    return {
        "master": f"{stem}{tag}.png",
        "print": print_rel,
        "preview": (bits[0] + "/previews/" + bits[1]) if len(bits) == 2
                   else f"previews/{print_rel}",
        "filename": f"{name_stem}{tag}.jpg",
    }


def file_step2(db: Session, painting, poster, title, project, gen,
               *, processed_by: str = "server") -> bool:
    """
    File a STEP-2 picture that came back, as a new generation row of the
    same poster. Returns False (and files nothing) when the painting is no
    longer the one waiting — see step2_still_wanted.

    The print file is NOT built here. Like a painting, it is built when the
    owner approves, with the signature, by `_build_print_file` — which reads
    `master_path`, here the picture exactly as OpenAI returned it.
    """
    import io
    from PIL import Image
    from .imagefetch import make_preview
    from .models import ProcessedImage
    from .pipeline import get_setting, recompute_title_status
    from .storage_remote import StorageError, write_bytes
    from .config import WORKSPACE_DIR

    if not step2_still_wanted(db, painting, poster):
        log.info("Step 2 for painting %s came back after it stopped waiting — "
                 "not filed", painting.id)
        forget_paid(2, painting.id)
        return False
    # Paid for: kept until its row is committed (see keep_paid).
    keep_paid(2, painting.id, gen.image_bytes)

    names = step2_names(db, title, poster, painting, project)
    with Image.open(io.BytesIO(gen.image_bytes)) as probe:
        src_w, src_h = probe.size
    width = int(get_setting(db, "upscale_width_px", project=project) or 4000)
    out_h = max(1, round(src_h * (width / max(1, src_w))))

    tmp_dir = WORKSPACE_DIR / "_gpt_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw = tmp_dir / f"s2_{painting.id}.png"
    prev = tmp_dir / f"s2_{painting.id}_prev.jpg"
    try:
        raw.write_bytes(gen.image_bytes)
        make_preview(raw, prev)
        write_bytes(db, names["master"], raw.read_bytes(), project=project)
        write_bytes(db, names["preview"], prev.read_bytes(), project=project)
        from .review_cache import clear as _clear_review_cache
        _clear_review_cache([names["master"], names["preview"], names["print"]])
    except StorageError as e:
        # Paid for, but not filed. Counted as a transient failure on the
        # painting, so a Storage Box having a moment retries rather than
        # losing the picture — and stops after step2_max_attempts.
        record_step2_failure(db, painting, project, e)
        db.commit()
        log.error("Could not store step 2 for painting %s: %s", painting.id, e)
        return False
    finally:
        raw.unlink(missing_ok=True)
        prev.unlink(missing_ok=True)

    # The painting steps aside but KEEPS its files: 'painted' means "went
    # through step 2, still here to redo step 2 from".
    db.query(ProcessedImage).filter(
        ProcessedImage.saved_poster_id == poster.id,
        ProcessedImage.is_current == 1,
    ).update({ProcessedImage.is_current: 0}, synchronize_session=False)
    painting.review_status = "painted"
    painting.step2_error = None

    row = ProcessedImage(
        saved_poster_id=poster.id,
        project_id=project.id,
        storage_path="",                   # built at approval, like a painting
        filename=names["filename"],
        file_size=len(gen.image_bytes),
        output_width=width,
        output_height=out_h,
        script_version=str(get_setting(db, "step2_model", project=project)),
        processed_by=processed_by,
        duration_ms=gen.duration_ms,
        is_current=1,
        attempt=painting.attempt or 1,
        preview_path=names["preview"],
        review_status="pending",
        master_path=names["master"],
        background_color=None,
        signature_json=painting.signature_json,
        stage=2,
        step2_from=painting.id,
    )
    db.add(row)
    db.flush()
    recompute_title_status(db, title)
    db.commit()
    forget_paid(2, painting.id)
    return True


def process_step2_one(db: Session, painting, poster, title, project) -> bool:
    """One step 2 at full price, now (batch switched off)."""
    from . import gpt_images as G
    from .pipeline import get_setting
    from .storage_remote import StorageError, read_bytes
    from .config import WORKSPACE_DIR

    kept = kept_generation(2, painting.id)
    if kept is not None:
        log.info("Painting %s: filing the step 2 already paid for", painting.id)
        return file_step2(db, painting, poster, title, project, kept)

    style_rel = str(get_setting(db, "openai_style_image", project=project) or "")
    style = WORKSPACE_DIR / style_rel if style_rel else Path("")
    try:
        master = read_bytes(db, painting.master_path or painting.storage_path,
                            project=project)
        png, size = G.step2_input(master)
        gen = G.generate_step2(db, painting_png=png, input_size=size,
                               style=style, project=project,
                               location=(title.title or ""))
    except StorageError as e:
        record_step2_failure(db, painting, project, G.TransientFailure(
            f"could not read the painting: {e}"))
        db.commit()
        return False
    except (G.PermanentFailure, G.TransientFailure) as e:
        record_step2_failure(db, painting, project, e)
        db.commit()
        return False
    return file_step2(db, painting, poster, title, project, gen)


def step2_queue(db: Session, project):
    """
    Query: (painting, poster, title) waiting for step 2 and NOT already at
    OpenAI in an open batch, oldest first. Shared by the one-at-a-time path
    and the batch sender, so both take work in the same order.
    """
    from .models import MasterTitle, ProcessedImage, SavedPoster
    from .pipeline import get_setting, waiting_for_step2
    from .ai_batches import open_item_ids
    cap = int(get_setting(db, "step2_max_attempts", project=project) or 3)
    away = open_item_ids(db, project, stage=2)
    q = (waiting_for_step2(db, project)
           .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
           .filter(ProcessedImage.step2_attempts < cap)
           .with_entities(ProcessedImage, SavedPoster, MasterTitle)
           .order_by(SavedPoster.original_save_date.asc(), ProcessedImage.id.asc()))
    if away:
        q = q.filter(ProcessedImage.id.notin_(away))
    return q


# ── The loop ────────────────────────────────────────────────────────────────

def paint_queue(db: Session, project):
    """
    Query: (poster, title) waiting to be PAINTED in this project, oldest
    first. Shared by the one-at-a-time painter and the batch sender.

    STEP 1 SWITCHED OFF (v266) narrows it to the pictures the owner named
    by hand — RETRY, REPAINT — so step 2 can run on what is already painted
    while the rest of the photos wait (owner, 2026-10-28).
    """
    from .models import MasterTitle, SavedPoster
    from .pipeline import (get_setting, project_scope, _default_project_id,
                           step1_open)
    from sqlalchemy import or_

    max_attempts = int(get_setting(db, "process_max_attempts", project=project) or 3)
    q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.deleted_at.is_(None),
                  SavedPoster.process_attempts < max_attempts,
                  or_(SavedPoster.pipeline_status == "greenlit",
                      SavedPoster.pipeline_status == "failed_processing"),
                  project_scope(project.id,
                                default_project_id=_default_project_id(db)))
          .order_by(SavedPoster.original_save_date.asc(), SavedPoster.id.asc())
    )
    if not step1_open(db, project):
        q = q.filter(SavedPoster.paint_requested_at.isnot(None))
    return q


def _claim_next(db: Session, project):
    """
    Take the oldest waiting image for this project and mark it in progress.

    Same claim discipline as the node's, for the same reason: a restart
    mid-image must leave something the reaper can free, not a row that looks
    finished.
    """
    row = paint_queue(db, project).first()
    if row is None:
        return None, None
    poster, title = row
    poster.pipeline_status = "processing"
    poster.claimed_at = datetime.utcnow()
    poster.claimed_by = "server"
    # A hand-named picture KEEPS its mark while it is out. It is spent in
    # file_painting() or at a final record_paint_failure(), never here: every
    # way back to the queue (a transient error, a refused or stopped batch,
    # a restart, RELEASE) would otherwise return it unmarked, and with step 1
    # off an unmarked picture is never painted (v268).
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
        from .pipeline import batch_enabled, intake_open, step2_active
        from . import ai_batches as AB

        for project in projects:
            # BATCHES ALREADY AT OPENAI ARE ALWAYS LOOKED AFTER — asked how
            # they are doing, and their answers filed — even while paused or
            # with batch switched off. Collecting is finishing work already
            # paid for, not handing out new work, the same way a drain lets
            # claimed work report back (v266).
            try:
                if AB.poll(db, project):
                    did_work = True
            except Exception as e:                       # noqa: BLE001
                db.rollback()
                AB.note_error(project, f"checking batches: {type(e).__name__}: {e}")
                log.error("Batch check failed for %s: %s", project.slug, e)

            # PICTURES ALREADY PAID FOR are filed before anything new is
            # sent or painted, paused or not — like collecting (v287).
            try:
                if file_kept(db, project):
                    did_work = True
            except Exception as e:                       # noqa: BLE001
                db.rollback()
                log.error("Filing kept pictures failed for %s: %s", project.slug, e)

            # Paused or draining. The loop keeps ticking (so the heartbeat
            # stays fresh and the watchdog does not "revive" a worker that is
            # deliberately idle) — it simply claims nothing.
            if not intake_open(db, project):
                continue

            if batch_enabled(db, project):
                # Everything waiting — step 2 and step 1 — goes out in the
                # next send; nothing is painted one at a time.
                try:
                    if AB.send_due(db, project):
                        did_work = True
                except Exception as e:                   # noqa: BLE001
                    db.rollback()
                    AB.note_error(project, f"sending: {type(e).__name__}: {e}")
                    log.error("Batch send failed for %s: %s", project.slug, e)
                continue

            # ONE AT A TIME. Step 2 first: the painting is already paid for,
            # and the flatten is what the owner is waiting to look at.
            if step2_active(db, project):
                row = step2_queue(db, project).first()
                problem2 = G.settings_problem(db, project, stage=2) if row is not None else None
                if problem2:
                    AB.note_error(project, problem2)       # same as step 1 below (v279)
                    row = None
                if row is not None:
                    painting, poster2, title2 = row
                    pid2 = painting.id
                    try:
                        if process_step2_one(db, painting, poster2, title2, project):
                            did_work = True
                    except Exception as e:               # noqa: BLE001
                        db.rollback()
                        from . import gpt_images as _G
                        from .models import ProcessedImage as _PI
                        again = db.query(_PI).filter_by(id=pid2).first()
                        if again is not None:
                            record_step2_failure(db, again, project, _G.TransientFailure(
                                f"{type(e).__name__}: {e}"))
                            db.commit()
                        log.error("Step 2: painting %s failed unexpectedly: %s", pid2, e)
                    continue

            # THE MONTHLY SPEND CAP WAS REMOVED HERE IN v172, and this is the
            # one place where that removal takes a real protection away. It
            # used to stop the painting loop once the month's metered spend
            # reached a ceiling. The owner had the cap set to 0 (off) and
            # asked for the whole spend feature to go, so nothing on this
            # server now limits what a night of painting can cost. His
            # OpenAI account's own spend limit is what governs it.
            # A missing style picture fails every photo alike: say so and
            # claim nothing, rather than fail each photo for good (v279).
            problem = G.settings_problem(db, project, stage=1)
            if problem:
                AB.note_error(project, problem)
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
            # A batch caught half-way through SENDING may or may not have
            # reached OpenAI. The first poll asks OpenAI and either watches
            # it or puts its pictures back (v286 — requeueing blind paid
            # twice; rule 8, claimed work must end in a reported state).
            from .ai_batches import recover_unsent
            unsent = recover_unsent(_db)
            _db.commit()
            if unsent:
                log.warning("GPT: %s batch send(s) were cut short — the first "
                            "batch check asks OpenAI whether it has them", unsent)
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
