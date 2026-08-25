"""
One-time pipeline migration.

Does three things, each independently re-runnable:

  1. SCHEMA — adds the pipeline columns to the existing master_titles and
     saved_posters tables. SQLAlchemy's create_all() creates new tables but
     never ALTERs existing ones, so this has to be explicit.

  2. IMPORT — folds the legacy JSON state into the database:
       faa_upload_tracking.json  → upload_tracking rows
       Outputs/Straight From Photoshop/ → processed_images rows
     After this the JSON files are historical artefacts. The database becomes
     the only source of truth.

  3. BACKFILL — sets pipeline_status on every poster so the dashboard's funnel
     reflects reality on day one: already-uploaded work shows as uploaded,
     processed-but-not-uploaded shows as processed, and the rest surfaces as
     backlog awaiting greenlight.

EVERY STEP IS IDEMPOTENT. Run it twice and nothing duplicates — existing rows
are recognised and skipped. That matters because you will almost certainly run
it once as a dry run, once for real, and possibly again after copying more
processed files to storage.

────────────────────────────────────────────────────────────────────────────
USAGE
────────────────────────────────────────────────────────────────────────────
Inspect first, change nothing:

    python scripts/migrate_pipeline.py --dry-run \
        --tracking "/path/to/faa_upload_tracking.json" \
        --processed-root "/path/to/Outputs/Straight From Photoshop"

Then apply:

    python scripts/migrate_pipeline.py \
        --tracking "/path/to/faa_upload_tracking.json" \
        --processed-root "/path/to/Outputs/Straight From Photoshop" \
        --account-name GR \
        --account-email you@example.com \
        --account-profile-url "https://fineartamerica.com/profiles/2-elton-odhiambo"

Schema only (safe to run on the server before anything else):

    python scripts/migrate_pipeline.py --schema-only

────────────────────────────────────────────────────────────────────────────
HOW THE LEGACY DATA MAPS
────────────────────────────────────────────────────────────────────────────
The legacy tools key everything on the folder-prefix number — the `0` column
from the original CSV — which is `MasterTitle.external_id` here. Within a
title, images are matched by filename: the processed name is the source name
plus the configured suffix, so "Pulp Fiction 1.jpg" ↔ "Pulp Fiction 1_Painted.jpg".

**THAT NUMBER IS ONLY UNIQUE INSIDE ONE PROJECT.** Every project's sheet
starts again at 1, so the movie list and MUSIK both hold an external_id 2 —
`The Dark Knight` and `Radiohead`. Every lookup here therefore goes through
`_titles_by_ext_query`, which scopes to the project being imported. This
script predates multi-project support and did not, which made it match 0 of
4,865 images while reporting them as plausible-looking findings.

Where a processed filename can't be matched back to a live poster (the poster
was deleted after upload, or the file was renamed by hand), the row is
reported as unmatched rather than guessed at. Those are listed at the end so
you can decide what to do; they are usually deletions and safe to ignore.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow running as `python scripts/migrate_pipeline.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.db import SessionLocal, engine, init_db
from app import pipeline as P
from app.models import (
    MasterTitle, ProcessedImage, Project, SavedPoster, UploadAccount,
    UploadTracking,
)


# ═════════════════════════════════════════════════════════════════════════
#  1. SCHEMA
# ═════════════════════════════════════════════════════════════════════════

# Column and index definitions live in app/schema_migrations.py, which the app
# also applies automatically at startup. Imported rather than duplicated: two
# copies of a migration list is exactly how a column ends up added in one place
# and not the other.
from app.schema_migrations import NEW_COLUMNS, NEW_INDEXES, migrate_schema  # noqa: F401,E402


# ═════════════════════════════════════════════════════════════════════════
#  2. IMPORT — legacy JSON + processed files
# ═════════════════════════════════════════════════════════════════════════

def ensure_account(
    db, *, name: str, email: str, profile_url: Optional[str],
    password: Optional[str], project: Project, dry_run: bool,
) -> Optional[UploadAccount]:
    """
    Find or create the marketplace account the historical uploads belong to.

    A placeholder password is stored when none is supplied: the history import
    needs an account row to attach to, and you'll set the real credentials in
    the dashboard before the first automated run.
    """
    account = (
        db.query(UploadAccount)
          .filter_by(project_id=project.id, name=name)
          .first()
    )
    if account is not None:
        return account
    if dry_run:
        return None

    account = UploadAccount(
        project_id=project.id,
        name=name,
        target_site="faa",
        email=email,
        password_enc=P.encrypt_secret(password or "CHANGE_ME_IN_DASHBOARD"),
        profile_url=profile_url,
        daily_limit=100,
        is_enabled=1 if password else 0,
        created_by="migration",
    )
    db.add(account)
    db.flush()
    return account


def _titles_by_ext_query(db, project: Project, *extra):
    """
    external_id → title, FOR ONE PROJECT.

    ════════════════════════════════════════════════════════════════════════
    external_id IS ONLY UNIQUE INSIDE A PROJECT
    ════════════════════════════════════════════════════════════════════════
    It is the `0` column from a project's own source sheet, so every project
    starts again at 1. The movie list has an external_id 2 and so does
    MUSIK — `The Dark Knight` and `Radiohead`.

    Both versions of this lookup were written when there was one project,
    and built ONE dictionary across the whole table. The later rows win, so
    every movie title silently resolved to a music artist, and the upload
    history import matched 0 of 4,865 images while reporting them as
    "renumbered". Nothing looked broken: it produced a report full of
    plausible-sounding findings about the wrong project's data.

    Measured 2026-08-25 on the real database, and it is the exact shape the
    project brief warns about — a query that filters on project by hand, or
    in this case does not filter at all.

    NULL means the DEFAULT project and nothing else, which is why this goes
    through `pipeline.project_scope` rather than comparing the column:
    production's 101,605 movie rows are still NULL until the backfill at the
    end of this script runs.
    """
    columns = (MasterTitle.external_id, MasterTitle.id) + tuple(extra)
    default = P.ensure_default_project(db)
    return (db.query(*columns)
              .filter(MasterTitle.external_id.isnot(None))
              .filter(P.project_scope(project.id,
                                      default_project_id=default.id)))


def _poster_index_map(db, master_id: int) -> dict[int, int]:
    """
    Position of each live poster within its title, in creation order.

    This is what determines the A/B/C suffix, and it must match what
    pipeline.ensure_upload_rows computes — otherwise imported history would
    disagree with newly-queued work about which image is "A".
    """
    ids = [
        pid for (pid,) in
        db.query(SavedPoster.id)
          .filter(SavedPoster.master_title_id == master_id,
                  SavedPoster.deleted_at.is_(None))
          .order_by(SavedPoster.created_at.asc(), SavedPoster.id.asc())
          .all()
    ]
    return {pid: index for index, pid in enumerate(ids)}


def _strip_suffix(filename: str, suffix: str) -> str:
    """
    Map a processed filename back to its source filename.

    "Pulp Fiction 1_Painted.jpg" → "Pulp Fiction 1" so it can be matched
    against the source poster regardless of original extension (sources are a
    mix of .jpg/.png/.webp; output is always .jpg).
    """
    stem = os.path.splitext(filename)[0]
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem


def import_processed_files(
    db, *, root: Path, project: Project, dry_run: bool,
) -> dict:
    """
    Register every locally-processed image as a ProcessedImage row.

    Walks the mirrored `{date}/{N. Title (Year)}/{file}_Painted.jpg` tree the
    old Photoshop script produced. Matching is by external_id (the folder
    prefix) then by filename stem, so a title whose folder was renamed still
    resolves as long as the number is intact.

    Note the storage_path recorded here is relative and mirrors the same
    layout the pipeline will use going forward — which is why you can copy
    this tree onto the storage box as-is and the paths just work.
    """
    if not root.is_dir():
        return {"error": f"Processed root not found: {root}"}

    suffix = P.get_setting(db, "output_suffix", project=project)
    version = "legacy-local"

    titles_by_ext = {
        ext_id: (title_id, folder)
        for ext_id, title_id, folder in
        _titles_by_ext_query(db, project,
                             MasterTitle.title_folder_path).all()
    }

    created = skipped = unmatched = 0
    unmatched_examples: list[str] = []
    dates_seen: set[str] = set()

    for date_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        dates_seen.add(date_dir.name)

        for title_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            match = re.match(r"^(\d+)\.\s", title_dir.name)
            if not match:
                continue
            ext_id = int(match.group(1))
            entry = titles_by_ext.get(ext_id)
            if entry is None:
                unmatched += 1
                if len(unmatched_examples) < 12:
                    unmatched_examples.append(f"{title_dir.name} (no title with id {ext_id})")
                continue

            master_id, _folder = entry
            posters = (
                db.query(SavedPoster)
                  .filter(SavedPoster.master_title_id == master_id,
                          SavedPoster.deleted_at.is_(None))
                  .all()
            )
            # Index sources by filename stem so extension differences between
            # source and output don't matter.
            by_stem = {os.path.splitext(p.filename)[0]: p for p in posters}

            for image in sorted(title_dir.iterdir()):
                if image.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                    continue

                stem = _strip_suffix(image.name, suffix)
                poster = by_stem.get(stem)
                if poster is None:
                    unmatched += 1
                    if len(unmatched_examples) < 12:
                        unmatched_examples.append(f"{title_dir.name}/{image.name}")
                    continue

                existing = (
                    db.query(ProcessedImage)
                      .filter_by(saved_poster_id=poster.id, is_current=1)
                      .first()
                )
                if existing is not None:
                    skipped += 1
                    continue

                if dry_run:
                    created += 1
                    continue

                rel = f"{date_dir.name}/{title_dir.name}/{image.name}"
                db.add(ProcessedImage(
                    saved_poster_id=poster.id,
                    project_id=project.id,
                    storage_path=rel,
                    filename=image.name,
                    file_size=image.stat().st_size,
                    script_version=version,
                    processed_by="legacy-local",
                    is_current=1,
                ))
                created += 1

        if not dry_run:
            db.flush()

    return {
        "created": created,
        "skipped": skipped,
        "unmatched": unmatched,
        "unmatched_examples": unmatched_examples,
        "dates": sorted(dates_seen),
    }


def import_upload_tracking(
    db, *, tracking_path: Path, account: Optional[UploadAccount],
    project: Project, dry_run: bool, orphan_report: Optional[Path] = None,
) -> dict:
    """
    Import faa_upload_tracking.json into upload_tracking.

    The legacy shape is {external_id: {processed_filename: {uploaded, removed,
    date}}}. Original upload timestamps are preserved so the dashboard's
    history chart shows your real activity rather than the migration date.

    Rows are created even where no ProcessedImage exists yet — the upload
    happened, and that fact is what stops the pipeline from re-uploading it.

    ORPHANS: ~1% of legacy entries reference a filename with no matching
    poster row. Measured on the real dataset there are exactly two causes, and
    the report distinguishes them because they mean very different things:

      "jsx_dot_bug" — the old Photoshop script named output with
          `currentFile.name.split('.')[0] + "_Painted.jpg"`, i.e. it truncated
          at the FIRST dot. For any title containing a dot ("E.T.",
          "Monsters, Inc.", "Kill Bill: Vol. 1") every poster collapsed onto
          one output filename and overwrote the others. Only one image per
          such title ever reached the marketplace; the rest were processed and
          silently destroyed. Those posters are genuinely unprocessed work, so
          leaving them un-imported is correct — the pipeline will redo them
          properly. Note the marketplace already holds one listing per
          affected title, so expect one near-duplicate each.

      "renumbered" — the poster number in a filename is not stable
          (`save_image` derives it from the live poster count, so deleting and
          re-saving shifts it). On the earliest titles, posters were swapped
          after being uploaded, so the uploaded filenames point at numbers
          that no longer exist.

    Either way the listing is live on the marketplace with nothing local to
    attach it to, and inventing poster rows to hold it would corrupt the audit
    trail. Reporting is the honest outcome.
    """
    if not tracking_path.is_file():
        return {"error": f"Tracking file not found: {tracking_path}"}

    try:
        raw = json.loads(tracking_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return {"error": f"Could not read tracking file: {e}"}

    suffix = P.get_setting(db, "output_suffix", project=project)

    titles_by_ext = {
        ext_id: title_id
        for ext_id, title_id in _titles_by_ext_query(db, project).all()
    }

    created = skipped = unmatched = removed_count = 0
    unmatched_examples: list[str] = []
    orphans: list[dict] = []
    index_cache: dict[int, dict[int, int]] = {}

    for ext_key, images in raw.items():
        try:
            ext_id = int(ext_key)
        except (TypeError, ValueError):
            continue

        master_id = titles_by_ext.get(ext_id)
        if master_id is None:
            unmatched += len(images)
            for filename, meta in images.items():
                orphans.append({
                    "external_id": ext_id, "filename": filename,
                    "reason": "no master title with that external_id",
                    "legacy": meta if isinstance(meta, dict) else None,
                })
            if len(unmatched_examples) < 12:
                unmatched_examples.append(f"external_id {ext_id} not in master list")
            continue

        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.master_title_id == master_id,
                      SavedPoster.deleted_at.is_(None))
              .all()
        )
        by_stem = {os.path.splitext(p.filename)[0]: p for p in posters}

        if master_id not in index_cache:
            index_cache[master_id] = _poster_index_map(db, master_id)
        indexes = index_cache[master_id]

        title = db.query(MasterTitle).filter_by(id=master_id).first()

        for filename, meta in images.items():
            if not isinstance(meta, dict):
                continue

            stem = _strip_suffix(filename, suffix)
            poster = by_stem.get(stem)
            if poster is None:
                unmatched += 1
                # Classify, because the two causes need different follow-up.
                # A dot in the title is the reliable tell for the old script's
                # split('.')[0] truncation.
                title_text = title.title if title else ""
                if "." in title_text:
                    cause = "jsx_dot_bug"
                    note = ("Old script truncated the output name at the first dot, so "
                            "every poster for this title overwrote the same file. Only "
                            "one image reached the marketplace; the rest are unprocessed "
                            "work the pipeline will now redo. Expect one near-duplicate "
                            "listing per affected title.")
                else:
                    cause = "renumbered"
                    note = ("Poster numbering shifted after a delete-and-resave, so this "
                            "uploaded filename points at a number that no longer exists.")
                orphans.append({
                    "external_id": ext_id,
                    "title": title_text or None,
                    "filename": filename,
                    "cause": cause,
                    "note": note,
                    "live_poster_filenames": sorted(by_stem),
                    "legacy": meta,
                })
                if len(unmatched_examples) < 12:
                    unmatched_examples.append(f"{ext_id}/{filename}  [{cause}]")
                continue

            if account is None:
                # Dry run without an account — count what we'd do and move on.
                created += 1
                continue

            existing = (
                db.query(UploadTracking)
                  .filter_by(saved_poster_id=poster.id, account_id=account.id)
                  .first()
            )
            if existing is not None:
                skipped += 1
                continue

            if dry_run:
                created += 1
                continue

            uploaded_at = None
            if meta.get("date"):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        uploaded_at = datetime.strptime(meta["date"], fmt)
                        break
                    except ValueError:
                        continue

            was_removed = bool(meta.get("removed"))
            was_uploaded = bool(meta.get("uploaded"))
            if was_removed:
                status = "removed"
                removed_count += 1
            elif was_uploaded:
                status = "uploaded"
            else:
                status = "pending"

            index = indexes.get(poster.id, 0)
            processed = (
                db.query(ProcessedImage)
                  .filter_by(saved_poster_id=poster.id, is_current=1)
                  .first()
            )

            db.add(UploadTracking(
                saved_poster_id=poster.id,
                processed_image_id=processed.id if processed else None,
                account_id=account.id,
                project_id=project.id,
                target_site="faa",
                remote_title=(P.render_remote_title(db, title, poster, index, project=project)
                              if title else None),
                letter_index=index,
                status=status,
                attempts=1 if was_uploaded else 0,
                uploaded_at=uploaded_at if was_uploaded else None,
                removed_at=uploaded_at if was_removed else None,
                removed_reason="Imported as removed/copyright" if was_removed else None,
            ))
            created += 1

        if not dry_run:
            db.flush()

    # Preserve the orphan list — these are real listings on the marketplace
    # with no local counterpart, and that's worth knowing about later.
    by_cause: dict[str, int] = {}
    for entry in orphans:
        by_cause[entry.get("cause", "unknown")] = by_cause.get(entry.get("cause", "unknown"), 0) + 1

    if orphans and orphan_report is not None:
        try:
            orphan_report.write_text(
                json.dumps({
                    "generated_at": datetime.utcnow().isoformat(),
                    "summary": (
                        "Legacy upload records that could not be attached to a "
                        "saved_poster row. Grouped by cause — see 'causes' below. "
                        "Nothing here blocks the pipeline; it is a record of "
                        "listings that exist on the marketplace with no local "
                        "counterpart."
                    ),
                    "causes": {
                        "jsx_dot_bug": (
                            "The old Photoshop script named output using "
                            "split('.')[0], truncating at the first dot. Titles "
                            "containing a dot had every poster collapse onto one "
                            "output filename, overwriting each other. Only one image "
                            "per title reached the marketplace. ACTION: the pipeline "
                            "will now process these posters correctly; consider "
                            "removing the single old listing on each affected title "
                            "to avoid a near-duplicate."
                        ),
                        "renumbered": (
                            "Poster numbering shifted after a delete-and-resave on the "
                            "earliest titles, so these uploaded filenames reference "
                            "numbers that no longer exist. ACTION: none needed."
                        ),
                    },
                    "count": len(orphans),
                    "count_by_cause": by_cause,
                    "affected_external_ids": sorted({e["external_id"] for e in orphans}),
                    "entries": orphans,
                }, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    return {
        "created": created, "skipped": skipped, "unmatched": unmatched,
        "removed": removed_count, "unmatched_examples": unmatched_examples,
        "titles_in_file": len(raw), "orphans": len(orphans),
        "orphans_by_cause": by_cause,
        "orphan_report": str(orphan_report) if orphans and orphan_report else None,
    }


# ═════════════════════════════════════════════════════════════════════════
#  3. BACKFILL — pipeline_status
# ═════════════════════════════════════════════════════════════════════════

def backfill_statuses(db, *, project: Project, dry_run: bool) -> dict:
    """
    Derive pipeline_status for every live poster from what now exists in the
    database.

    Precedence, most-advanced first:
        uploaded / removed tracking row  → uploaded
        current ProcessedImage           → processed
        otherwise                        → left NULL (awaiting greenlight)

    Anything already uploaded is also marked greenlit at title level, since it
    self-evidently was approved — otherwise the funnel would show thousands of
    finished items as "awaiting greenlight".
    """
    uploaded_poster_ids = {
        pid for (pid,) in
        db.query(UploadTracking.saved_poster_id)
          .filter(UploadTracking.status.in_(("uploaded", "removed")))
          .distinct()
          .all()
    }
    processed_poster_ids = {
        pid for (pid,) in
        db.query(ProcessedImage.saved_poster_id)
          .filter(ProcessedImage.is_current == 1)
          .distinct()
          .all()
    }

    counts = defaultdict(int)
    touched_titles: set[int] = set()

    posters = (
        db.query(SavedPoster)
          .filter(SavedPoster.deleted_at.is_(None))
          .all()
    )

    for poster in posters:
        if poster.id in uploaded_poster_ids:
            status = "uploaded"
        elif poster.id in processed_poster_ids:
            status = "processed"
        else:
            continue

        counts[status] += 1
        touched_titles.add(poster.master_title_id)
        if not dry_run:
            poster.pipeline_status = status

    if dry_run:
        return {"counts": dict(counts), "titles": len(touched_titles)}

    db.flush()

    now = datetime.utcnow()
    for title_id in touched_titles:
        title = db.query(MasterTitle).filter_by(id=title_id).first()
        if title is None:
            continue
        if title.project_id is None:
            title.project_id = project.id
        # Work that reached processing was approved by definition.
        if title.greenlit_at is None:
            title.greenlit_at = now
            title.greenlit_by = "migration"
        P.recompute_title_status(db, title)

    # Give every remaining title a project so later queries never rely on the
    # NULL-means-project-1 fallback.
    db.query(MasterTitle).filter(MasterTitle.project_id.is_(None)).update(
        {"project_id": project.id}, synchronize_session=False,
    )

    return {"counts": dict(counts), "titles": len(touched_titles)}


# ═════════════════════════════════════════════════════════════════════════
#  REPORT
# ═════════════════════════════════════════════════════════════════════════

def summarise(db, *, project: Project) -> None:
    funnel = P.funnel_counts(db, project_id=project.id)
    print("\n── Pipeline state ─────────────────────────────────────────")
    for key in ("awaiting_greenlight", "greenlit", "processing", "processed",
                "uploading", "uploaded", "failed_processing", "failed_upload"):
        print(f"  {key:22s} {funnel.get(key, 0):>8,}")

    backlog = (
        db.query(MasterTitle)
          .filter(MasterTitle.status == "complete",
                  MasterTitle.greenlit_at.is_(None))
          .count()
    )
    print(f"\n  Completed titles awaiting greenlight: {backlog:,}")

    processed_total = db.query(ProcessedImage).filter_by(is_current=1).count()
    uploaded_total = (
        db.query(UploadTracking).filter(UploadTracking.status == "uploaded").count()
    )
    print(f"  Processed derivatives on record:      {processed_total:,}")
    print(f"  Confirmed uploads on record:          {uploaded_total:,}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy Photoshop/upload state into the pipeline tables.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing anything.")
    parser.add_argument("--schema-only", action="store_true",
                        help="Add the new columns/tables and stop.")
    parser.add_argument("--tracking", type=Path,
                        help="Path to faa_upload_tracking.json.")
    parser.add_argument("--processed-root", type=Path,
                        help="Path to 'Outputs/Straight From Photoshop'.")
    parser.add_argument("--account-name", default="GR",
                        help="Marketplace account name the historical uploads belong to.")
    parser.add_argument("--account-email", default="",
                        help="Email for that account (required when creating it).")
    parser.add_argument("--account-password", default="",
                        help="Optional. Omit and set it in the dashboard instead; "
                             "the account stays disabled until you do.")
    parser.add_argument("--account-profile-url", default="",
                        help="Marketplace profile URL for that account.")
    args = parser.parse_args()

    mode = "DRY RUN — nothing will be written" if args.dry_run else "APPLYING CHANGES"
    print(f"\n{'=' * 62}\n  PIPELINE MIGRATION — {mode}\n{'=' * 62}")

    # ── 1. Schema ──────────────────────────────────────────────────────
    print("\n[1/4] Schema")
    result = migrate_schema(dry_run=args.dry_run)
    for column in result["added"]:
        print(f"      + {column}")
    if result["skipped"]:
        print(f"      = {len(result['skipped'])} column(s) already present")
    if not result["added"]:
        print("      Nothing to add — schema is already current.")

    if args.schema_only:
        print("\nSchema-only run complete.")
        return

    db = SessionLocal()
    try:
        project = P.ensure_default_project(db)
        if not args.dry_run:
            db.commit()
        print(f"      Project: {project.name} (id={project.id})")

        # ── 2. Processed files ─────────────────────────────────────────
        print("\n[2/4] Processed images")
        if args.processed_root:
            stats = import_processed_files(
                db, root=args.processed_root, project=project, dry_run=args.dry_run)
            if stats.get("error"):
                print(f"      ! {stats['error']}")
            else:
                print(f"      + {stats['created']:,} registered")
                print(f"      = {stats['skipped']:,} already known")
                print(f"      ? {stats['unmatched']:,} unmatched")
                print(f"        across {len(stats['dates'])} date folder(s)")
                for example in stats["unmatched_examples"]:
                    print(f"          - {example}")
                if stats["unmatched"] > len(stats["unmatched_examples"]):
                    print(f"          … and {stats['unmatched'] - len(stats['unmatched_examples']):,} more")
        else:
            print("      Skipped (no --processed-root given)")

        if not args.dry_run:
            db.commit()

        # ── 3. Upload history ──────────────────────────────────────────
        print("\n[3/4] Upload history")
        if args.tracking:
            account = ensure_account(
                db,
                name=args.account_name,
                email=args.account_email or "unknown@example.com",
                profile_url=args.account_profile_url or None,
                password=args.account_password or None,
                project=project,
                dry_run=args.dry_run,
            )
            if account is not None:
                state = "enabled" if account.is_enabled else "disabled until you set its password"
                print(f"      Account '{account.name}' — {state}")
            elif not args.dry_run:
                print("      ! Could not resolve an account")

            orphan_report = args.tracking.parent / "pipeline_migration_orphans.json"
            stats = import_upload_tracking(
                db, tracking_path=args.tracking, account=account,
                project=project, dry_run=args.dry_run,
                orphan_report=None if args.dry_run else orphan_report)
            if stats.get("error"):
                print(f"      ! {stats['error']}")
            else:
                print(f"      + {stats['created']:,} tracking rows")
                print(f"      = {stats['skipped']:,} already known")
                print(f"      ? {stats['unmatched']:,} unmatched")
                print(f"        ({stats['removed']:,} marked removed/copyright)")
                print(f"        from {stats['titles_in_file']:,} titles in the file")
                for cause, count in sorted((stats.get("orphans_by_cause") or {}).items()):
                    label = {
                        "jsx_dot_bug": "old script truncated the name at the first dot",
                        "renumbered":  "poster numbering shifted after a re-save",
                    }.get(cause, cause)
                    print(f"          {count:>4} — {label}")
                for example in stats["unmatched_examples"]:
                    print(f"            - {example}")
                if stats.get("orphan_report"):
                    print(f"        Detail written to:\n          {stats['orphan_report']}")
                elif stats.get("orphans"):
                    print(f"        {stats['orphans']:,} orphans (report written on a real run)")
        else:
            print("      Skipped (no --tracking given)")

        if not args.dry_run:
            db.commit()

        # ── 4. Backfill ────────────────────────────────────────────────
        print("\n[4/4] Backfilling pipeline_status")
        stats = backfill_statuses(db, project=project, dry_run=args.dry_run)
        for status, count in sorted(stats["counts"].items()):
            print(f"      {status:12s} {count:>8,}")
        print(f"      across {stats['titles']:,} titles")

        if not args.dry_run:
            db.commit()
            summarise(db, project=project)
            print("\nMigration complete.")
            print("\nNext steps:")
            print("  1. Open Pipeline → Upload and set the account's real password.")
            print("  2. Register your Windows node under Pipeline → Nodes.")
            print("  3. Copy the processed files to the storage box (paths already match).")
            print("  4. Greenlight the backlog under Pipeline → Greenlight.")
        else:
            print("\nDry run complete — nothing was written.")
            print("Re-run without --dry-run to apply.")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
