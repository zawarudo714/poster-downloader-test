"""
Filesystem / workspace helpers for the new claim-based model.

Layout on disk:
    /workspace/
        {username}/
            {original_save_date YYYY-MM-DD}/
                {title_folder_path}/      ← decided once at first save, frozen
                    {Title} 1.jpg
                    {Title} 2.webp
                    ...

Every path-building helper here is anchored on a MasterTitle row's
(original_save_date, title_folder_path), never on "today's date".
"""

from __future__ import annotations

import hashlib
import os
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import WORKSPACE_DIR
from .parsing import IMAGE_EXTS
from .timeutil import local_today, utc_start_of_local_day


# ── Workspace layout ─────────────────────────────────────────────────────────

def user_root(username: str, project_folder: str | None = None) -> Path:
    """
    A worker's top-level folder, inside their project. Created on demand.

    `project_folder=None` returns the pre-split location, which is what the
    legacy path fallback and the migration itself need.
    """
    base = WORKSPACE_DIR / project_folder if project_folder else WORKSPACE_DIR
    p = base / username
    p.mkdir(parents=True, exist_ok=True)
    return p


def date_folder(username: str, d: date_type, project_folder: str | None = None) -> Path:
    """The worker's date folder for `d`. Created on demand."""
    p = user_root(username, project_folder) / d.isoformat()
    p.mkdir(parents=True, exist_ok=True)
    return p


def title_folder_for(username: str, d: date_type, folder_name: str,
                     project_folder: str | None = None) -> Path:
    """
    Locate the title folder for a (project, user, date, folder_name).
    Used during save / delete / serve. Creates on demand because saves
    create folders.
    """
    p = date_folder(username, d, project_folder) / folder_name
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_images_in(folder: Path) -> list[Path]:
    """All image files directly in `folder` (non-recursive), sorted."""
    if not folder.is_dir():
        return []
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: p.name,
    )


def list_users_with_workspaces(project_folder: str | None = None) -> list[str]:
    """
    Worker folders that contain any work, for the browse page's dropdown.

    Understands BOTH layouts, because the workspace is split by project and
    the migration may not have run (or a file may predate it):
      · {project}/{worker}/...   — current
      · {worker}/...             — legacy

    Passing a project narrows to that project; passing None returns every
    worker seen under either layout, which is what a cross-project view wants.
    Private dirs (_zips) are excluded in both.
    """
    if not WORKSPACE_DIR.is_dir():
        return []

    names: set[str] = set()

    if project_folder:
        root = WORKSPACE_DIR / project_folder
        if root.is_dir():
            names |= {p.name for p in root.iterdir()
                      if p.is_dir() and not p.name.startswith("_")}

    for p in WORKSPACE_DIR.iterdir():
        if not p.is_dir() or p.name.startswith("_"):
            continue
        # A directory whose children are dates is a legacy worker folder; one
        # whose children are more directories-of-dates is a project folder.
        children = [c for c in p.iterdir() if c.is_dir()]
        looks_legacy = any(_is_date_name(c.name) for c in children)
        if looks_legacy:
            names.add(p.name)
        elif not project_folder:
            names |= {c.name for c in children if not c.name.startswith("_")}

    return sorted(names)


def _is_date_name(name: str) -> bool:
    try:
        date_type.fromisoformat(name)
        return True
    except ValueError:
        return False


def list_date_folders(username: str, project_folder: str | None = None) -> list[str]:
    """
    Date folders for a worker, newest first. Merges both layouts so the
    browse page shows every date regardless of whether the migration has run.
    """
    roots = []
    if project_folder:
        roots.append(WORKSPACE_DIR / project_folder / username)
    roots.append(WORKSPACE_DIR / username)          # legacy

    out: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        out |= {p.name for p in root.iterdir()
                if p.is_dir() and _is_date_name(p.name)}
    return sorted(out, reverse=True)


# ── Date helpers ─────────────────────────────────────────────────────────────

def week_range(d: Optional[date_type] = None) -> tuple[date_type, date_type]:
    """Return (monday, sunday) of the week containing `d` (default today)."""
    d = d or local_today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


# ── Counter helpers (use the DB, not the filesystem) ─────────────────────────
# These are imported into routes; they take a SQLAlchemy session and return ints.

# ── THE WORKER'S OWN SAVES, ON THE WORKER'S OWN CLOCK ──────────────────────
# Two things these counters got wrong until v258 (owner, 2026-10-09: TODAY
# read 96 while he had done 58, and kept climbing while he was offline):
#   * The owner's USE MY OWN PICTURE pick is filed in the worker's folder,
#     so it carries the worker's USERNAME — and these counted by username.
#     38 of the owner's picks were added to the worker's day. A pick is
#     never the worker's work (`added_by` is set), so it is left out.
#   * The day began at UTC midnight (03:00 in Nairobi). It now begins at
#     local midnight — utc_start_of_local_day.
# A redo the worker saved today on an older title still counts: he did
# that work today. Pay is unaffected — payments.payable_criteria has always
# excluded the owner's picks and buckets by the title's day.

def count_user_saves_for_date(db, username: str, d: date_type) -> int:
    """Live pictures `username` saved himself on local day `d`."""
    from .models import SavedPoster  # local import to avoid circular at module load
    start = utc_start_of_local_day(d)
    end   = utc_start_of_local_day(d + timedelta(days=1))
    q = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.username == username,
              SavedPoster.added_by.is_(None),
              SavedPoster.deleted_at.is_(None),
              SavedPoster.created_at >= start,
              SavedPoster.created_at <  end,
          )
    )
    return q.count()


def count_user_saves_for_week(db, username: str, d: Optional[date_type] = None) -> int:
    """Live pictures `username` saved himself this week (Mon–Sun, local)."""
    from .models import SavedPoster
    monday, sunday = week_range(d)
    start = utc_start_of_local_day(monday)
    end   = utc_start_of_local_day(sunday + timedelta(days=1))
    q = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.username == username,
              SavedPoster.added_by.is_(None),
              SavedPoster.deleted_at.is_(None),
              SavedPoster.created_at >= start,
              SavedPoster.created_at <  end,
          )
    )
    return q.count()


def count_titles_worked_today(db, user_id: int, d: date_type) -> int:
    """Distinct master titles a user touched today (had a save on)."""
    from .models import SavedPoster
    start = utc_start_of_local_day(d)
    end   = utc_start_of_local_day(d + timedelta(days=1))
    q = (
        db.query(SavedPoster.master_title_id)
          .filter(
              SavedPoster.user_id == user_id,
              SavedPoster.added_by.is_(None),
              SavedPoster.deleted_at.is_(None),
              SavedPoster.created_at >= start,
              SavedPoster.created_at <  end,
          )
          .distinct()
    )
    return q.count()


def count_live_posters_for_master(db, master_title_id: int) -> int:
    """How many live (non-deleted) posters this master title has."""
    from .models import SavedPoster
    return (
        db.query(SavedPoster)
          .filter(
              SavedPoster.master_title_id == master_title_id,
              SavedPoster.deleted_at.is_(None),
          )
          .count()
    )


def live_flag_title_ids(db, master_title_ids) -> set:
    """
    THE one definition of "this title is flagged": an open or awaiting
    flag on a picture that still EXISTS. Returns the subset of the given
    title ids that qualify.

    Every door that sets MasterTitle.needs_revision, the worker's red FLAG
    tag, and Diagnostics' check_needs_revision_matches_open_flags must all
    ask this one question. Two doors (worker delete, admin DELETE THIS
    RECORD) used to count flags on DELETED pictures too, so a flag left
    open on a long-gone picture lit the red tag for ever on a title with
    0 saved — the worker then avoided those titles, not realising they
    held nothing to fix (owner, 2026-09-23: Atlanta, Yellowstone).
    """
    from .models import SavedPoster
    ids = [i for i in (master_title_ids or []) if i is not None]
    if not ids:
        return set()
    rows = (_live_flag_query(db)
              .filter(SavedPoster.master_title_id.in_(ids))
              .all())
    return {r[0] for r in rows}


def pictures_awaiting_your_look(db, title_id) -> list:
    """
    THE one answer to "does this title hold a picture the owner has not
    seen, that REPLACED something he flagged or had already looked at?"
    — which is what decides whether the worker's DONE may complete the
    title or must wait on Changes Requested.

    Found 2026-09-27 (owner's question): a worker deletes a flagged picture,
    the owner acknowledges the deletion, the worker saves a new picture and
    presses DONE — and the title completed straight away, because the only
    question DONE asked was "is a flag still open?". The new picture was
    filed under the title's ORIGINAL date on Worker Images, and could be
    greenlit and painted without anyone having looked at it.

    A live picture counts when you have not looked at it (reviewed_at is
    empty; the admin's own additions never count) and ANY of:
      (a) it arrived after a FLAGGED picture on this title was taken off
          it — by the worker's delete OR yours: the flag's resolved_at.
          Until 2026-09-29 only the worker's delete counted (its verdict
          wording was matched), so Atlanta — flagged, deleted by the admin,
          redone — finished without his look;
      (b) it arrived after you had looked at ANY picture on this title —
          that picture's reviewed_at, whether it is still live or since
          deleted or swapped out. This covers a reviewed picture being
          replaced AND a second picture being added beside it after a
          reopen, which replaces nothing but is still new to you;
      (c) its own file was swapped after you had looked at it
          (review_voided_at, set by replace_poster);
      (d) it arrived after you REJECTED the title's completion
          (MasterTitle.completion_rejected_at, set by reject_complete).
    Every one of those is a way YOU have already acted on the title. A new
    way of acting on a title must be added here, or the picture after it
    will finish unseen.
    First-time work on a title never counts: nothing was flagged or seen
    before it, so there is nothing it replaced.

    PAYMENT asks this too (payments.unpayable_reasons, owner 2026-09-29):
    a picture waiting for your look is not paid until you have looked.
    """
    return awaiting_your_look_by_title(db, [title_id]).get(title_id, [])


def awaiting_your_look_by_title(db, title_ids) -> dict:
    """
    The body of pictures_awaiting_your_look, over many titles at once:
    {title_id: [SavedPoster, ...]}, only titles that have some. Payments ask
    it for every title a worker ever touched, so it runs a fixed number of
    queries per 500 titles instead of four per title. The single-title
    version calls this one, so the two cannot drift apart.
    """
    from .models import MasterTitle, Revision, SavedPoster
    ids = sorted({i for i in (title_ids or []) if i is not None})
    out: dict = {}
    for at in range(0, len(ids), 500):
        chunk = ids[at:at + 500]
        live = (db.query(SavedPoster)
                  .filter(SavedPoster.master_title_id.in_(chunk),
                          SavedPoster.deleted_at.is_(None),
                          SavedPoster.reviewed_at.is_(None),
                          SavedPoster.added_by.is_(None))
                  .order_by(SavedPoster.id.asc())
                  .all())
        if not live:
            continue
        tids = sorted({sp.master_title_id for sp in live})
        first: dict = {}

        def mark(tid, when):
            if when is not None and (tid not in first or when < first[tid]):
                first[tid] = when

        for tid, when in (
                db.query(SavedPoster.master_title_id, Revision.resolved_at)
                  .join(Revision, Revision.saved_poster_id == SavedPoster.id)
                  .filter(SavedPoster.master_title_id.in_(tids),
                          SavedPoster.deleted_at.isnot(None),
                          Revision.resolved_at.isnot(None))
                  .all()):
            mark(tid, when)                                   # (a)
        for tid, when in (
                db.query(SavedPoster.master_title_id, SavedPoster.reviewed_at)
                  .filter(SavedPoster.master_title_id.in_(tids),
                          SavedPoster.reviewed_at.isnot(None))
                  .all()):
            mark(tid, when)                                   # (b)
        for tid, when in (
                db.query(MasterTitle.id, MasterTitle.completion_rejected_at)
                  .filter(MasterTitle.id.in_(tids),
                          MasterTitle.completion_rejected_at.isnot(None))
                  .all()):
            mark(tid, when)                                   # (d)
        for sp in live:
            f = first.get(sp.master_title_id)
            if (sp.review_voided_at is not None                # (c)
                    or (f is not None and sp.created_at is not None
                        and sp.created_at >= f)):
                out.setdefault(sp.master_title_id, []).append(sp)
    return out

def backfill_completion_rejections(db) -> int:
    """
    Give titles rejected BEFORE MasterTitle.completion_rejected_at existed
    their rejection time, read from the activity log, and return how many
    changed. Run at startup; only fills empty ones, so it is harmless to
    repeat. Without it, a title rejected last week and redone tomorrow would
    still finish without the owner's look — fixing the door does not repair
    what it had already written (CLAUDE.md, rule 7 family).
    """
    from sqlalchemy import func
    from .models import ActivityLog, MasterTitle
    rows = (db.query(ActivityLog.target_id, func.max(ActivityLog.created_at))
              .filter(ActivityLog.action == "rejected_completion",
                      ActivityLog.target_type == "master_title",
                      ActivityLog.target_id.isnot(None))
              .group_by(ActivityLog.target_id).all())
    changed = 0
    for tid, when in rows:
        t = db.query(MasterTitle).filter(MasterTitle.id == tid).first()
        if t is not None and t.completion_rejected_at is None and when is not None:
            t.completion_rejected_at = when
            changed += 1
    return changed


def _live_flag_query(db):
    """The body of the definition above, unfiltered by title — shared with
    resync_flag_markers so the startup repair cannot drift from it."""
    from .models import Revision, SavedPoster
    return (
        db.query(SavedPoster.master_title_id)
          .join(Revision, Revision.saved_poster_id == SavedPoster.id)
          .filter(SavedPoster.deleted_at.is_(None),
                  Revision.status.in_(("open", "awaiting_approval")))
          .distinct()
    )


def resync_flag_markers(db) -> int:
    """
    Bring every stored MasterTitle.needs_revision into line with the live
    flags, and return how many rows changed. Run at startup.

    Why it exists: the doors that set the marker were fixed (v228 one
    definition, v231 SEND BACK no longer marks), but fixing a door does not
    repair the rows it had ALREADY written. The worker's screen derived the
    tag at read time and so looked fixed, while Worker Images and the Title
    List read the stored column and kept a red outline on titles with no
    flag at all (owner, 2026-09-27: Beirut). Idempotent: a clean database
    changes nothing.
    """
    from .models import MasterTitle
    live = {r[0] for r in _live_flag_query(db).all() if r[0] is not None}
    marked = {r[0] for r in
              db.query(MasterTitle.id).filter(MasterTitle.needs_revision == 1).all()}
    changed = 0
    for ids, value in ((sorted(marked - live), 0), (sorted(live - marked), 1)):
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            changed += (db.query(MasterTitle)
                          .filter(MasterTitle.id.in_(chunk))
                          .update({MasterTitle.needs_revision: value},
                                  synchronize_session=False))
    return changed


def withdrawn_picture_paintings(db):
    """
    Query: paintings still CURRENT, or still waiting for a verdict
    ('pending' / 'held'), whose picture has been taken off its title.

    ONE definition, read by the startup repair below and by the Diagnostics
    check `check_withdrawn_pictures_hold_no_waiting_painting`, so the two
    cannot disagree about what counts.
    """
    from sqlalchemy import or_
    from .models import ProcessedImage, SavedPoster
    return (db.query(ProcessedImage)
              .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
              .filter(SavedPoster.deleted_at.isnot(None),
                      or_(ProcessedImage.is_current == 1,
                          ProcessedImage.review_status.in_(("pending", "held")))))


def set_aside_withdrawn_paintings(db) -> int:
    """
    Take the paintings of withdrawn pictures out of Approve Artwork, and
    return how many rows changed. Run at startup.

    RETIRE TITLE (v220-ish) removed the picture and left its painting
    'pending' and current, so a retired title's painting could sit in the
    review queue and, if kept, queue an upload of a picture that no longer
    exists. v239 fixed the door (_withdraw_pictures in routes/admin.py);
    this repairs what the door had already written — fixing a door never
    repairs the rows it wrote. Idempotent: a clean database changes nothing.
    """
    changed = 0
    for pi in withdrawn_picture_paintings(db).all():
        pi.is_current = 0
        if (pi.review_status or "") in ("pending", "held"):
            pi.review_status = "superseded"
        changed += 1
    return changed


def fill_missing_fingerprints(batch: int = 100) -> int:
    """
    Give every live picture with no content_hash one, and return how many
    were filled. Run once at startup on a background thread.

    Why: the fingerprint used to be written only by the place check, so a
    picture saved while that check was off, or before it existed, carried
    none — and the same-picture check on the save doors (2026-09-27) would
    silently compare new saves against nothing for those. A guard that
    cannot see part of what it guards reads as coverage while it is not.

    Commits every `batch` rows so a restart part-way loses almost nothing,
    and simply carries on next start: the query IS the to-do list.
    """
    import logging
    from .db import SessionLocal
    from .models import SavedPoster
    log = logging.getLogger("fingerprints")
    filled = 0
    last_id = 0          # walk forward by id, so an unreadable row is passed once
    db = SessionLocal()
    try:
        while True:
            rows = (db.query(SavedPoster)
                      .filter(SavedPoster.content_hash.is_(None),
                              SavedPoster.deleted_at.is_(None),
                              SavedPoster.id > last_id)
                      .order_by(SavedPoster.id.asc())
                      .limit(batch).all())
            if not rows:
                break
            for sp in rows:
                last_id = sp.id
                try:
                    sp.content_hash = hashlib.sha256(
                        saved_poster_path(sp).read_bytes()).hexdigest()
                    filled += 1
                except OSError:
                    # Missing or unreadable file: left empty, and Diagnostics
                    # already reports missing files. Skipped by id, so one
                    # bad row can never make this loop spin for ever.
                    pass
            db.commit()
        if filled:
            log.info("Fingerprinted %d picture(s) that had none", filled)
    except Exception as e:
        db.rollback()
        log.error("Could not finish fingerprinting pictures: %s", e)
    finally:
        db.close()
    return filled


def start_fingerprint_backfill() -> None:
    """Run fill_missing_fingerprints once, off the startup path, so a few
    thousand file reads never delay the site coming up."""
    import threading
    threading.Thread(target=fill_missing_fingerprints,
                     name="fingerprint-backfill", daemon=True).start()

# ── Filesystem path lookup for a saved poster ────────────────────────────────

def _legacy_folder(poster) -> Path:
    """The pre-multi-project layout: {worker}/{date}/{title folder}."""
    return (
        WORKSPACE_DIR
        / poster.username
        / poster.original_save_date.isoformat()
        / poster.title_folder_path
    )


def saved_poster_folder(poster) -> Path:
    """
    The on-disk folder holding a SavedPoster.

    Current layout is {project}/{worker}/{date}/{title folder}. Rows written
    before the workspace was split by project have no `project_folder`, and
    their files sit at the old {worker}/{date}/... path.

    ════════════════════════════════════════════════════════════════════
    WHY THERE IS A FALLBACK
    ════════════════════════════════════════════════════════════════════
    The startup migration (app/workspace_migration.py) moves the old tree
    into the new shape. This function accepts BOTH layouts so that move is
    not load-bearing: if it hasn't run yet, was skipped, or a file was
    written in the instant before it ran, the file still resolves. Nothing
    404s, no gallery goes blank, no pipeline job fails on a missing source.

    Remove the fallback once production has been scanned clean by
    Diagnostics -> "Poster records with no file on disk".
    """
    folder = getattr(poster, "project_folder", None)
    if folder:
        candidate = (
            WORKSPACE_DIR
            / folder
            / poster.username
            / poster.original_save_date.isoformat()
            / poster.title_folder_path
        )
        # Trust the new layout unless the file genuinely isn't there yet.
        if candidate.exists() or not _legacy_folder(poster).exists():
            return candidate
    return _legacy_folder(poster)


def saved_poster_path(poster) -> Path:
    """Compute the on-disk Path of a SavedPoster row."""
    return saved_poster_folder(poster) / poster.filename


# ── Security ─────────────────────────────────────────────────────────────────

def safe_under_workspace(p: Path) -> bool:
    """Guard against path traversal — refuse anything outside WORKSPACE_DIR."""
    try:
        p = p.resolve()
        return WORKSPACE_DIR.resolve() in p.parents or p == WORKSPACE_DIR.resolve()
    except (OSError, RuntimeError):
        return False


# ── Misc ─────────────────────────────────────────────────────────────────────


def send_stranded_admin_picks_to_painting(db) -> int:
    """
    Repair: send the owner's own picks that never reached painting.

    Until v256, USE MY OWN PICTURE finished a title in memory and then asked
    greenlight_titles() for titles that were finished IN THE DATABASE, so a
    pick on a title that was not already finished was never greenlit (see
    the flush at the top of greenlight_titles). Fixing that door does not
    repair the picks it already left behind, so this does, at startup.

    Only titles whose every live picture is one of the owner's own are
    touched: an old + ADD pick can share a title with a worker's unpaid
    picture, and greenlighting that title would send the worker's picture
    to painting before it is paid. Those stay on Diagnostics for a person.
    Returns how many titles were sent.
    """
    from .models import MasterTitle, SavedPoster
    from .pipeline import awaiting_greenlight_poster_filter, greenlight_titles
    candidates = {
        tid for (tid,) in
        db.query(SavedPoster.master_title_id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.added_by.isnot(None),
                  SavedPoster.deleted_at.is_(None),
                  awaiting_greenlight_poster_filter(),
                  MasterTitle.status == "complete")
          .distinct().all()
    }
    if not candidates:
        return 0
    mixed = {
        tid for (tid,) in
        db.query(SavedPoster.master_title_id)
          .filter(SavedPoster.master_title_id.in_(candidates),
                  SavedPoster.deleted_at.is_(None),
                  SavedPoster.added_by.is_(None))
          .distinct().all()
    }
    ids = sorted(candidates - mixed)
    if not ids:
        return 0
    return greenlight_titles(db, ids, by="startup repair",
                             reason="admin_pick").get("greenlit", 0)
