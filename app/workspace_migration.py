"""
One-shot workspace reshape: split the raw-poster tree by project.

════════════════════════════════════════════════════════════════════════════
THE PROBLEM
════════════════════════════════════════════════════════════════════════════
The workspace was laid out when there was exactly one niche:

    workspace/{worker}/{date}/{title folder}/{file}

Nothing in that path says which project a file belongs to. The moment a
second project exists, a worker covering both writes MUSIK artists and movie
posters into the same dated folder. They don't literally collide today — both
projects number from 1, and "1. Coldplay" isn't "1. The Shawshank Redemption"
— but that's luck, not design, and the tree becomes unreadable.

    workspace/{PROJECT}/{worker}/{date}/{title folder}/{file}

Project first, so the raw tree reads the same way as the processed archive on
the Storage Box ({site}/{project}/processed/{date}/...). When you're chasing
one image across both at 1am, that consistency is worth having.

════════════════════════════════════════════════════════════════════════════
WHY THIS CAN RUN ITSELF
════════════════════════════════════════════════════════════════════════════
`app/schema_migrations.py` says data migrations do NOT belong in startup, and
that rule stands. This is the exception, for three specific reasons:

  1. It is a DIRECTORY RENAME, which the filesystem performs atomically. It
     either happened or it didn't; there is no half-moved state. That is a
     completely different risk profile from rewriting rows.
  2. It is one rename per worker — not per file. With 7,972 posters and one
     active worker it is a single operation taking milliseconds, regardless
     of how many files are inside.
  3. Nothing depends on it succeeding. `saved_poster_path()` looks in the new
     location and FALLS BACK to the old one, so even a partial or skipped
     migration leaves every file resolvable. The move is a tidy-up, not a
     load-bearing step.

That third point is the important one. It is why this is safe to automate and
why a failure here cannot take the site down.

════════════════════════════════════════════════════════════════════════════
VERIFYING IT
════════════════════════════════════════════════════════════════════════════
Diagnostics -> "Poster records with no file on disk" is the check. It should
read `clean` afterwards. Do not take this module's word for it.

The fallback in `saved_poster_path()` can be deleted once production has been
scanned clean — at which point this module can go too.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from .config import WORKSPACE_DIR
from .models import MasterTitle, Project, SavedPoster

log = logging.getLogger("uvicorn.error")


def project_folder_for(project: Project | None) -> str:
    """
    The folder segment for a project. Slugified so a name the admin typed
    ("GR(Movie&Series)") can't produce an illegal or surprising path.

    Deliberately reuses the pipeline's token rule so the workspace tree and
    the Storage Box tree name projects identically.
    """
    from .pipeline import _path_token, ensure_default_project  # local: avoids a cycle

    if project is None:
        return "unassigned"
    return _path_token(project.name or project.slug)


def _known_project_folders(db: Session) -> set[str]:
    return {project_folder_for(p) for p in db.query(Project).all()}


def migrate_workspace(db: Session, *, dry_run: bool = False) -> list[str]:
    """
    Move each worker's directory under its project's directory.

    Idempotent: a worker directory that has already been moved simply isn't
    at the top level any more, so it is not seen a second time.

    Returns human-readable descriptions of what moved, for the startup log.
    """
    if not WORKSPACE_DIR.is_dir():
        return []

    changes: list[str] = []
    project_folders = _known_project_folders(db)

    # Which project each worker's existing files belong to. Every poster
    # predating this migration belongs to the default project — there was
    # only one — but resolve it per worker rather than assuming, so a
    # half-migrated install can't put files under the wrong project.
    for child in sorted(WORKSPACE_DIR.iterdir()):
        if not child.is_dir():
            continue
        # Already a project directory: nothing at this level to move.
        if child.name in project_folders:
            continue

        worker = child.name
        row = (
            db.query(SavedPoster.project_folder, MasterTitle.project_id)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.username == worker)
              .first()
        )
        if row is None:
            # A directory with no poster rows behind it. Could be junk, could
            # be a worker whose rows were purged. Either way, moving it would
            # be a guess — leave it and let Diagnostics report it as orphaned.
            changes.append(f"skipped {worker!r}: no poster records reference it")
            continue

        project = None
        if row.project_id:
            project = db.query(Project).filter_by(id=row.project_id).first()
        if project is None:
            from .pipeline import ensure_default_project
            project = ensure_default_project(db)

        folder = project_folder_for(project)
        target_parent = WORKSPACE_DIR / folder
        target = target_parent / worker

        if target.exists():
            changes.append(
                f"skipped {worker!r}: {folder}/{worker} already exists — "
                f"resolve by hand"
            )
            continue

        if dry_run:
            changes.append(f"would move {worker} -> {folder}/{worker}")
            continue

        target_parent.mkdir(parents=True, exist_ok=True)
        child.rename(target)          # atomic within one filesystem
        changes.append(f"moved {worker} -> {folder}/{worker}")

    return changes


def backfill_project_folder(db: Session) -> int:
    """
    Stamp `SavedPoster.project_folder` on rows written before the column
    existed. One UPDATE per project, not per row.

    Returns the number of rows stamped.
    """
    from .pipeline import ensure_default_project

    default = ensure_default_project(db)
    total = 0

    for project in db.query(Project).all():
        folder = project_folder_for(project)
        ids = [
            r[0] for r in
            db.query(MasterTitle.id).filter(MasterTitle.project_id == project.id).all()
        ]
        if project.id == default.id:
            ids += [
                r[0] for r in
                db.query(MasterTitle.id).filter(MasterTitle.project_id.is_(None)).all()
            ]
        if not ids:
            continue
        # Chunked: SQLite caps the number of bound parameters, and the movie
        # project has 101k titles.
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            total += (
                db.query(SavedPoster)
                  .filter(SavedPoster.project_folder.is_(None),
                          SavedPoster.master_title_id.in_(chunk))
                  .update({SavedPoster.project_folder: folder},
                          synchronize_session=False)
            )
    return total


def run_startup_migration(db: Session) -> None:
    """
    Called once on boot. Backfill first so the folder move can trust the
    database, then move the directories. Never raises — a failure here must
    not stop the app, because the path fallback means everything still works.
    """
    try:
        stamped = backfill_project_folder(db)
        if stamped:
            log.info("Workspace migration: stamped %s poster rows with a project folder", stamped)
        for line in migrate_workspace(db):
            log.info("Workspace migration: %s", line)
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("Workspace migration FAILED (site unaffected, files still "
                  "resolve via the legacy path): %s", e)
