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
    ActivityLog, ChatMessage, ChatReadState, ImportJob, LedgerEntry,
    ListingSweep, MarketplaceSnapshot, MasterTitle, PaymentRun, PipelineJob,
    ProcessedImage, Revision, SavedPoster, SearchCache, TitleAlias,
    UploadTracking, User,
)

# ── EVERY table is either WIPED below or NAMED here, with its reason. ──────
# `check_reset_covers_every_table` in tools/preflight.py compares this file
# against models.py and fails the deploy if a table is in neither list —
# because that is exactly how five tables (earnings, sweeps, aliases,
# snapshots, the search cache) sat out the reset unnoticed: each was added
# AFTER this script was written, and nothing asked whether the reset should
# know about it (found in the 2026-09-09 audit).
KEPT_ON_PURPOSE = (
    "User",            # accounts and passwords survive a reset by contract
    "UserProject",     # which worker may enter which project — configuration
    "Project",         # the registry; recreated from code anyway, kept for ids
    "AppSetting",      # every dashboard setting — the owner's tuning
    "UploadAccount",   # marketplace accounts, passwords, artist names
    "AccountProject",  # which account uploads for which project
    "WorkerNode",      # node registration + token; a reset must not unpair the machine
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


def clear_workspace(dry_run: bool) -> tuple[int, list[str]]:
    """
    Delete every saved poster file, and NOTHING the settings still point at.

    ════════════════════════════════════════════════════════════════════════
    THE UNDERSCORE FOLDERS ARE ASSETS, NOT WORK — DO NOT DELETE THEM
    ════════════════════════════════════════════════════════════════════════
    This used to delete EVERY directory under the workspace root, while its
    own docstring claimed it removed "only the per-user directories". It did
    not, and the difference was expensive: the signature image lives at
    `_signature/<project>.png` and the style reference at `_style/<project>
    .png`, both of them under this root.

    So a reset deleted both files and left the SETTINGS still naming them.
    The setting is not work and survives — and `_build_print_file` REFUSES
    outright when a signature is switched on and its file is missing. The
    whole pipeline would have stopped on the first poster after a reset,
    with a message about a missing file the owner had never knowingly
    deleted (found 2026-09-10, before he ran it).

    A file here can be claimed two ways: a poster ROW points at it, or a
    SETTING names it. This function only ever meant the first kind. The
    underscore prefix is how the app marks the second kind, so that is what
    is skipped — and `check_workspace_assets_are_underscored` in preflight
    fails the deploy if any new asset is written outside that convention,
    because a convention nobody checks is a convention that gets broken.

    Same family as `check_orphan_files`, which reported the signature as an
    unknown file for the same reason: it knew one of the ways a file can be
    owned.
    """
    if not WORKSPACE_DIR.is_dir():
        return 0, []
    removed = 0
    kept: list[str] = []
    for child in WORKSPACE_DIR.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            kept.append(child.name)
            continue
        removed += sum(1 for _ in child.rglob("*") if _.is_file())
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
    return removed, sorted(kept)


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
        # The five below were added to the app AFTER this script was written
        # and sat out the reset until the 2026-09-09 audit. All five rebuild
        # themselves or belong to work that is being wiped:
        #   · money rows are re-read in full from the marketplace's own
        #     Balance page on the next earnings read — nothing is lost;
        #   · sweeps and snapshots describe listings and turns of work this
        #     reset deletes;
        #   · aliases point sale names at DESIGNS, which are being wiped;
        #   · the search cache holds one claim's Brave results, and claims
        #     are being wiped.
        # Leaving them meant the fresh site would open with the TEST shop's
        # money on the Earnings tab — a number that should read zero and
        # would not.
        ("earnings ledger rows",   LedgerEntry),
        ("marketplace snapshots",  MarketplaceSnapshot),
        ("listing sweeps",         ListingSweep),
        ("sale-name aliases",      TitleAlias),
        ("search cache",           SearchCache),
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

        files, kept_assets = clear_workspace(dry_run)
        print(f"  {'would delete' if dry_run else 'deleting':<14} {human(files):>9}  poster files on disk")
        # SAY WHAT WAS KEPT, not only what went. These are the files the
        # settings still point at, and the owner has no other way to know
        # they survived — silence here reads as "they were deleted too".
        if kept_assets:
            print(f"  {'keeping':<14} {'':>9}  {', '.join(kept_assets)} "
                  f"(signature, style reference and other settings assets)")

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
