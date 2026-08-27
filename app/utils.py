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
from .timeutil import local_today


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

def count_user_saves_for_date(db, username: str, d: date_type) -> int:
    """Number of live saves authored by `username` whose created_at fell on `d`."""
    from .models import SavedPoster  # local import to avoid circular at module load
    start = datetime.combine(d, datetime.min.time())
    end   = datetime.combine(d + timedelta(days=1), datetime.min.time())
    q = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.username == username,
              SavedPoster.deleted_at.is_(None),
              SavedPoster.created_at >= start,
              SavedPoster.created_at <  end,
          )
    )
    return q.count()


def count_user_saves_for_week(db, username: str, d: Optional[date_type] = None) -> int:
    """Live saves authored this week (Mon–Sun) by created_at."""
    from .models import SavedPoster
    monday, sunday = week_range(d)
    start = datetime.combine(monday, datetime.min.time())
    end   = datetime.combine(sunday + timedelta(days=1), datetime.min.time())
    q = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.username == username,
              SavedPoster.deleted_at.is_(None),
              SavedPoster.created_at >= start,
              SavedPoster.created_at <  end,
          )
    )
    return q.count()


def count_titles_worked_today(db, user_id: int, d: date_type) -> int:
    """Distinct master titles a user touched today (had a save on)."""
    from .models import SavedPoster
    start = datetime.combine(d, datetime.min.time())
    end   = datetime.combine(d + timedelta(days=1), datetime.min.time())
    q = (
        db.query(SavedPoster.master_title_id)
          .filter(
              SavedPoster.user_id == user_id,
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

