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

from .config import WORKSPACE_DIR  # used as fallback only; live workspace path
# (env feature removed) — workspace path is now a constant
from .parsing import IMAGE_EXTS


# Resolve the workspace dir at every call so we follow env switches. Calling
# `WORKSPACE_DIR` is just a contextvar lookup + small dict hop, so
# this is essentially free.

# ── Workspace layout ─────────────────────────────────────────────────────────

def user_root(username: str) -> Path:
    """The user's top-level folder. Created on demand."""
    p = WORKSPACE_DIR / username
    p.mkdir(parents=True, exist_ok=True)
    return p


def date_folder(username: str, d: date_type) -> Path:
    """The user's date folder for `d`. Created on demand."""
    p = user_root(username) / d.isoformat()
    p.mkdir(parents=True, exist_ok=True)
    return p


def title_folder_for(username: str, d: date_type, folder_name: str) -> Path:
    """
    Locate the title folder for a given (user, date, folder_name).
    Used during save / delete / serve. Creates on demand because saves create folders.
    """
    p = date_folder(username, d) / folder_name
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


def count_images_in(folder: Path) -> int:
    """Count image files directly in `folder` (non-recursive)."""
    if not folder.is_dir():
        return 0
    n = 0
    for entry in folder.iterdir():
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            n += 1
    return n


def count_images_recursive(folder: Path) -> int:
    """Count image files recursively under `folder`."""
    if not folder.is_dir():
        return 0
    n = 0
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if Path(f).suffix.lower() in IMAGE_EXTS:
                n += 1
    return n


def list_users_with_workspaces() -> list[str]:
    """Top-level folders under /workspace/ — one per user that has any work.
    Excludes private system dirs like _zips."""
    if not WORKSPACE_DIR.is_dir():
        return []
    return sorted(
        [p.name for p in WORKSPACE_DIR.iterdir()
         if p.is_dir() and not p.name.startswith("_")]
    )


def list_date_folders(username: str) -> list[str]:
    """Date folder names (YYYY-MM-DD) for a given user, newest first."""
    root = WORKSPACE_DIR / username
    if not root.is_dir():
        return []
    out = []
    for p in root.iterdir():
        if p.is_dir():
            try:
                date_type.fromisoformat(p.name)
                out.append(p.name)
            except ValueError:
                continue
    out.sort(reverse=True)
    return out


def list_title_folders(username: str, d: date_type) -> list[Path]:
    """All title subfolders inside the user's date folder, sorted by name."""
    base = WORKSPACE_DIR / username / d.isoformat()
    if not base.is_dir():
        return []
    return sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name)


# ── Date helpers ─────────────────────────────────────────────────────────────

def week_range(d: Optional[date_type] = None) -> tuple[date_type, date_type]:
    """Return (monday, sunday) of the week containing `d` (default today)."""
    d = d or date_type.today()
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

def saved_poster_path(poster) -> Path:
    """Compute the on-disk Path of a SavedPoster row."""
    return (
        WORKSPACE_DIR
        / poster.username
        / poster.original_save_date.isoformat()
        / poster.title_folder_path
        / poster.filename
    )


def saved_poster_folder(poster) -> Path:
    """Compute the on-disk folder Path containing a SavedPoster row."""
    return (
        WORKSPACE_DIR
        / poster.username
        / poster.original_save_date.isoformat()
        / poster.title_folder_path
    )


# ── Security ─────────────────────────────────────────────────────────────────

def safe_under_workspace(p: Path) -> bool:
    """Guard against path traversal — refuse anything outside WORKSPACE_DIR."""
    try:
        p = p.resolve()
        return WORKSPACE_DIR.resolve() in p.parents or p == WORKSPACE_DIR.resolve()
    except (OSError, RuntimeError):
        return False


# ── Misc ─────────────────────────────────────────────────────────────────────

def hash_file(path: Path, algo: str = "sha256") -> str:
    """SHA-256 hex digest of a file (used to detect duplicate content)."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n_bytes: int) -> str:
    if n_bytes >= 1_000_000:
        return f"{n_bytes/1024/1024:.1f} MB"
    if n_bytes >= 1000:
        return f"{n_bytes/1024:.1f} KB"
    return f"{n_bytes} B"
