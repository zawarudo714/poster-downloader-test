"""
Configuration for the Poster Downloader web app.

Reads from environment variables when present; falls back to sane local
development defaults so `uvicorn app.main:app --reload` works out of the box.
"""

import os
import secrets
from pathlib import Path


# ── Cache busting ────────────────────────────────────────────────────────────
# Bumped on every deploy. Templates append `?v={APP_VERSION}` to every
# <script> and <link rel="stylesheet"> URL, so deploys force browsers to
# refetch JS/CSS automatically — no Ctrl+Shift+R needed by users.
APP_VERSION = "38"


# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent

# Workspace lives at <project_root>/workspace by default. Override with WORKSPACE_DIR
# in production to put it on a larger volume.
WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", BASE_DIR / "workspace")).resolve()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'poster.db'}")


def _sqlite_file_from_url(url: str, fallback: Path) -> Path:
    """
    Extract the on-disk path from a sqlite:/// URL.

    DB_PATH must track DATABASE_URL. It's used by the backup/restore code
    (app/backups.py) to copy and replace the actual file, so if someone
    relocates the database with the DATABASE_URL env var — which the Docker
    deployment does, to keep it on a mounted volume — a hardcoded DB_PATH
    would silently back up a file that isn't the live database, and restore
    would write over the wrong path. Deriving it removes that whole class of
    mistake.
    """
    prefix = "sqlite:///"
    if url.startswith(prefix):
        raw = url[len(prefix):]
        if raw:
            return Path(raw).resolve()
    # Non-SQLite backend (or malformed URL): backups are SQLite-specific and
    # will report the file as missing rather than doing something surprising.
    return fallback


# Where the SQLite file lives on disk. Only meaningful for sqlite:/// URLs.
DB_PATH = _sqlite_file_from_url(DATABASE_URL, BASE_DIR / "poster.db")

# Backups + snapshots live under <project_root>/backups/. Daily auto-backups are
# written here at midnight; admin-triggered manual snapshots also go here.
BACKUPS_DIR = Path(os.environ.get("BACKUPS_DIR", BASE_DIR / "backups")).resolve()
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
# Auto-backups older than this are pruned (manual snapshots are never pruned).
AUTO_BACKUP_RETENTION_DAYS = int(os.environ.get("AUTO_BACKUP_RETENTION_DAYS", "14"))

# ── Secrets ──────────────────────────────────────────────────────────────────
# In production set SESSION_SECRET to a long random string (e.g. `openssl rand -hex 32`).
# A randomly-generated fallback is used if missing — but it changes on every restart,
# which would log everyone out, so DO set this in production.
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)

# Cookie name + max-age (seconds). 14 days by default.
SESSION_COOKIE_NAME = "poster_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 14

# ── Palette (matches original main.py exactly) ───────────────────────────────
PALETTE = {
    "BG":      "#0e0e14",
    "CARD":    "#1c1c28",
    "BORDER":  "#2a2a3d",
    "ACCENT":  "#e8b84b",
    "ACCENT2": "#c47f17",
    "TEXT":    "#f0ede6",
    "SUBTEXT": "#8a8799",
    "SUCCESS": "#4ec98a",
    "SKIP":    "#e8a84b",
    "ERROR":   "#e8554b",
}

# ── Image constraints ────────────────────────────────────────────────────────
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_DOWNLOAD_HOSTS = {
    "image.tmdb.org",
    "www.themoviedb.org",
    "themoviedb.org",
    # If posters are sometimes hosted elsewhere, add here.
}
# Allow any host by default (worker may grab from various sources). Set
# RESTRICT_HOSTS=1 in env to enforce ALLOWED_DOWNLOAD_HOSTS instead.
RESTRICT_HOSTS = os.environ.get("RESTRICT_HOSTS", "0") == "1"

MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB safety cap per image
DOWNLOAD_TIMEOUT_S = 20

# Soft warning threshold per title (matches spec: "soft warning at 3, not a hard block")
SOFT_LIMIT_PER_TITLE = 3

# ── Pull-from-master defaults ────────────────────────────────────────────────
# Default size of "pull next N" batch. User can override via the input.
DEFAULT_PULL_SIZE = 50
# Hard cap on a single pull to avoid a runaway claim of the entire master.
MAX_PULL_SIZE = 500

# Default per-page size for the master sheet listing (admin + user browse views).
MASTER_PAGE_SIZE = 100
