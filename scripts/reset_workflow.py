"""
Reset the workflow to a clean slate — server-side, deliberate, reversible.

════════════════════════════════════════════════════════════════════════════
WHAT IT CLEARS AND WHAT IT KEEPS
════════════════════════════════════════════════════════════════════════════
CLEARED — everything produced by doing the work:

    saved posters + their files on disk        revisions / change requests
    the pipeline (processed + upload records)  payment runs
    chat history                               the activity log
    import jobs                                claims and greenlights on titles

KEPT — everything that describes the setup:

    users and their passwords        marketplace accounts (and their
    worker↔project assignments        encrypted credentials)
    registered worker nodes           every pipeline setting you have tuned
    projects                          the master title list itself

Master titles are RESET, not deleted, by default: every row goes back to
'pending', unclaimed and un-greenlit. That's what makes the run realistic —
you get the same 101,605-row queue a worker actually faces, rather than an
empty list. `--wipe-titles` deletes them outright if you want to re-import.

════════════════════════════════════════════════════════════════════════════
WHY IT REFUSES BY DEFAULT
════════════════════════════════════════════════════════════════════════════
This is the most destructive thing in the repo. It takes a backup of the
database first, always, and it will not touch a database that looks like
production unless you say so twice. The production install is the one holding
several thousand completed titles and a dozen payment runs — the exact things
this deletes and nobody can reconstruct.

    python scripts/reset_workflow.py --dry-run     # show me, change nothing
    python scripts/reset_workflow.py --yes         # do it

Inside Docker:

    docker compose exec web python scripts/reset_workflow.py --dry-run
    docker compose exec web python scripts/reset_workflow.py --yes
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Allow `python scripts/reset_workflow.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BACKUPS_DIR, DB_PATH, WORKSPACE_DIR          # noqa: E402
from app.db import SessionLocal                                      # noqa: E402
from app.models import (                                             # noqa: E402
    ActivityLog, ChatMessage, ChatReadState, ImportJob, MasterTitle,
    PaymentRun, PipelineJob, ProcessedImage, Revision, SavedPoster,
    UploadTracking, User,
)

# A database with more than this much finished work is assumed to be
# production. Tuned against the real install: 3,467 completed titles and 12
# payment runs at the time of writing.
PRODUCTION_COMPLETED_TITLES = 500
PRODUCTION_PAYMENT_RUNS = 3


def human(n: int) -> str:
    return f"{n:,}"


def survey(db) -> dict:
    return {
        "master_total":     db.query(MasterTitle).count(),
        "master_complete":  db.query(MasterTitle).filter_by(status="complete").count(),
        "master_claimed":   db.query(MasterTitle)
                              .filter(MasterTitle.claimed_by_id.isnot(None)).count(),
        "posters":          db.query(SavedPoster).count(),
        "revisions":        db.query(Revision).count(),
        "processed":        db.query(ProcessedImage).count(),
        "uploads":          db.query(UploadTracking).count(),
        "payments":         db.query(PaymentRun).count(),
        "chat":             db.query(ChatMessage).count(),
        "activity":         db.query(ActivityLog).count(),
        "jobs":             db.query(PipelineJob).count(),
        "users":            db.query(User).filter_by(is_deleted=0).count(),
    }


def looks_like_production(stats: dict) -> list[str]:
    reasons = []
    if stats["master_complete"] > PRODUCTION_COMPLETED_TITLES:
        reasons.append(
            f"{human(stats['master_complete'])} completed titles "
            f"(> {human(PRODUCTION_COMPLETED_TITLES)})"
        )
    if stats["payments"] > PRODUCTION_PAYMENT_RUNS:
        reasons.append(
            f"{human(stats['payments'])} payment runs (> {PRODUCTION_PAYMENT_RUNS})"
        )
    return reasons


def backup_database() -> Path | None:
    """
    Copy the SQLite file before touching anything.

    Unconditional, and not optional. Undoing this script means restoring this
    file; without it the only path back is the nightly auto-backup, which may
    be up to 24 hours old.
    """
    if not DB_PATH.is_file():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUPS_DIR / f"pre-reset-{stamp}.db"
    shutil.copy2(DB_PATH, target)
    return target


def clear_workspace(dry_run: bool) -> int:
    """
    Delete every saved poster file.

    Only the per-user directories under the workspace root are removed, and
    the root itself is left in place — the app creates it at import time and
    deleting it out from under a running container is asking for trouble.
    """
    if not WORKSPACE_DIR.is_dir():
        return 0
    removed = 0
    for child in WORKSPACE_DIR.iterdir():
        if not child.is_dir():
            continue
        removed += sum(1 for _ in child.rglob("*") if _.is_file())
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
    return removed


def reset(db, *, wipe_titles: bool, dry_run: bool) -> None:
    # Order matters only for readability — SQLite has no FK enforcement here,
    # but deleting children first keeps the log sensible if it's interrupted.
    tables = [
        ("upload tracking",  UploadTracking),
        ("processed images", ProcessedImage),
        ("pipeline jobs",    PipelineJob),
        ("revisions",        Revision),
        ("saved posters",    SavedPoster),
        ("payment runs",     PaymentRun),
        ("chat messages",    ChatMessage),
        ("chat read state",  ChatReadState),
        ("import jobs",      ImportJob),
        ("activity log",     ActivityLog),
    ]
    for label, model in tables:
        n = db.query(model).count()
        print(f"  {'would delete' if dry_run else 'deleting':<14} {human(n):>9}  {label}")
        if not dry_run:
            db.query(model).delete(synchronize_session=False)

    if wipe_titles:
        n = db.query(MasterTitle).count()
        print(f"  {'would delete' if dry_run else 'deleting':<14} {human(n):>9}  master titles")
        if not dry_run:
            db.query(MasterTitle).delete(synchronize_session=False)
    else:
        n = db.query(MasterTitle).count()
        print(f"  {'would reset' if dry_run else 'resetting':<14} {human(n):>9}  master titles -> pending")
        if not dry_run:
            # Everything a title accumulates by being worked on. Left alone:
            # the title's own content (name, year, rating, project).
            db.query(MasterTitle).update({
                MasterTitle.status: "pending",
                MasterTitle.needs_revision: 0,
                MasterTitle.claimed_by_id: None,
                MasterTitle.claimed_by_name: None,
                MasterTitle.claimed_at: None,
                MasterTitle.started_at: None,
                MasterTitle.completed_at: None,
                MasterTitle.original_save_date: None,
                MasterTitle.title_folder_path: None,
                MasterTitle.skip_reason: None,
                MasterTitle.complete_comment: None,
                MasterTitle.admin_note: None,
                MasterTitle.greenlit_at: None,
                MasterTitle.greenlit_by: None,
                MasterTitle.greenlit_source: None,
                MasterTitle.pipeline_status: None,
            }, synchronize_session=False)

    # Users keep their accounts but lose any pointer into deleted work.
    n = db.query(User).filter(User.locked_master_id.isnot(None)).count()
    print(f"  {'would clear' if dry_run else 'clearing':<14} {human(n):>9}  active title locks")
    if not dry_run:
        db.query(User).update({User.locked_master_id: None},
                              synchronize_session=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset the workflow to a clean slate.")
    ap.add_argument("--yes", action="store_true",
                    help="actually do it (without this, nothing is written)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen and exit")
    ap.add_argument("--wipe-titles", action="store_true",
                    help="delete the master title list too, instead of "
                         "resetting it to pending")
    ap.add_argument("--force", action="store_true",
                    help="override the production-database refusal")
    args = ap.parse_args()

    dry_run = args.dry_run or not args.yes

    db = SessionLocal()
    try:
        stats = survey(db)

        print()
        print("═" * 64)
        print("  WORKFLOW RESET")
        print("═" * 64)
        print(f"  database   {DB_PATH}")
        print(f"  workspace  {WORKSPACE_DIR}")
        print()
        print("  Currently holding:")
        print(f"    {human(stats['master_total']):>9}  master titles "
              f"({human(stats['master_complete'])} complete, "
              f"{human(stats['master_claimed'])} claimed)")
        print(f"    {human(stats['posters']):>9}  saved posters")
        print(f"    {human(stats['processed']):>9}  processed images")
        print(f"    {human(stats['uploads']):>9}  upload records")
        print(f"    {human(stats['payments']):>9}  payment runs")
        print(f"    {human(stats['users']):>9}  users (KEPT)")
        print()

        danger = looks_like_production(stats)
        if danger and not args.force:
            print("  REFUSING — this looks like the production database:")
            for r in danger:
                print(f"    · {r}")
            print()
            print("  Nothing has been changed. If you are certain, re-run with")
            print("  --force. Take a manual backup first.")
            print("═" * 64)
            return 2

        if danger:
            print("  ⚠ Production-looking database, proceeding because --force "
                  "was given.")
            print()

        if dry_run:
            print("  DRY RUN — nothing will be written. Re-run with --yes to apply.")
        else:
            backup = backup_database()
            print(f"  Backup written to {backup}" if backup
                  else "  No database file to back up (fresh install).")
        print()

        reset(db, wipe_titles=args.wipe_titles, dry_run=dry_run)

        files = clear_workspace(dry_run)
        print(f"  {'would delete' if dry_run else 'deleting':<14} {human(files):>9}  poster files on disk")

        if not dry_run:
            db.commit()
            print()
            print("  Done. Restart the app so nothing is holding stale state:")
            print("    docker compose restart web")
        print("═" * 64)
        print()
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
