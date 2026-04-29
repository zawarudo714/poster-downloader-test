"""
Multi-environment ("sandbox") support.

Concept:
- An environment is a self-contained instance of the site: its own SQLite DB
  file and its own workspace folder. Switching env changes which DB the
  request talks to and which folder files are read from / written to.
- The default env is "live" — the existing poster.db and workspace/ at the
  project root. No data migration was required to switch this on.
- Test envs live in <project>/data/test_envs/<name>/ — they hold a fresh
  poster.db plus a workspace/ subfolder.
- Test envs auto-reset every midnight (see `reset_all_test_envs`); the
  scheduler thread in backups.py also calls this nightly.

How a request's active env is determined:
- A signed cookie `pd_env` carries the env name. FastAPI middleware in main.py
  reads it on every request and sets a contextvar.
- The DB session and workspace-path helpers read that contextvar at lookup
  time, so individual route handlers don't need to know which env they're in.
- Workers cannot enter test envs — only admins (the env-switch endpoint enforces).

Env names: alnum + _ + - only, max 32 chars. "live" is reserved.
"""

from __future__ import annotations

import contextvars
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import APP_DIR, DATABASE_URL, WORKSPACE_DIR

log = logging.getLogger("poster.envs")

# Where test envs live. Created on first use.
TEST_ENVS_DIR = APP_DIR.parent / "data" / "test_envs"

LIVE_ENV = "live"
ENV_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Contextvar holds the active env name for the current request. Defaults to
# the live env so any code path that runs outside a request still gets sane
# behaviour (e.g. background tasks, startup hooks).
_active_env: contextvars.ContextVar[str] = contextvars.ContextVar("active_env", default=LIVE_ENV)

# Per-env engine + session factory cache. Built lazily on first access.
_engines: dict[str, "object"] = {}
_sessions: dict[str, sessionmaker] = {}
_engines_lock = threading.Lock()


# ─── Env identity / paths ──────────────────────────────────────────────────

def current_env() -> str:
    return _active_env.get()


def set_active_env(name: str) -> None:
    """For tests + middleware. Validates the name belongs to a real env."""
    if name != LIVE_ENV and not test_env_exists(name):
        raise ValueError(f"Unknown env: {name!r}")
    _active_env.set(name)


def list_test_envs() -> list[str]:
    if not TEST_ENVS_DIR.is_dir():
        return []
    return sorted(p.name for p in TEST_ENVS_DIR.iterdir() if p.is_dir())


def test_env_exists(name: str) -> bool:
    return (TEST_ENVS_DIR / name).is_dir()


def env_db_path(env: str) -> Path:
    if env == LIVE_ENV:
        # Pull from the configured DATABASE_URL — handles sqlite:///path style.
        if DATABASE_URL.startswith("sqlite:///"):
            return Path(DATABASE_URL.removeprefix("sqlite:///"))
        # Non-sqlite live DB — env feature isn't usable in that case.
        raise RuntimeError("Test envs require a SQLite live DB.")
    return TEST_ENVS_DIR / env / "poster.db"


def env_workspace_dir(env: str) -> Path:
    if env == LIVE_ENV:
        return WORKSPACE_DIR
    return TEST_ENVS_DIR / env / "workspace"


def current_workspace_dir() -> Path:
    """Workspace folder for the active env. Cheap — single dict lookup."""
    return env_workspace_dir(current_env())


# ─── Engine cache ──────────────────────────────────────────────────────────

def _build_engine(env: str):
    """Build (and cache) the SQLAlchemy engine + session factory for env."""
    db_path = env_db_path(env)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    connect_args = {"check_same_thread": False}
    engine = create_engine(url, connect_args=connect_args, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    _engines[env]  = engine
    _sessions[env] = Session
    # Make sure all tables exist in this env's DB.
    from .db import Base
    from . import models  # noqa: F401 — registers models on Base.metadata
    Base.metadata.create_all(bind=engine)
    return engine, Session


def current_engine():
    env = current_env()
    if env not in _engines:
        with _engines_lock:
            if env not in _engines:
                _build_engine(env)
    return _engines[env]


def current_session_factory() -> sessionmaker:
    env = current_env()
    if env not in _sessions:
        with _engines_lock:
            if env not in _sessions:
                _build_engine(env)
    return _sessions[env]


# ─── CRUD on test envs ─────────────────────────────────────────────────────

def create_test_env(name: str) -> Path:
    """
    Make a brand-new test env. The folder structure is:
        data/test_envs/<name>/
            poster.db        (empty — schema gets created on first session)
            workspace/       (empty — populated as the worker saves posters)
    Raises ValueError on bad name or if the env already exists.
    """
    if not ENV_NAME_RE.match(name):
        raise ValueError(
            "Env name must be 1–32 chars, letters / numbers / underscore / hyphen only."
        )
    if name == LIVE_ENV:
        raise ValueError(f"{LIVE_ENV!r} is reserved.")
    folder = TEST_ENVS_DIR / name
    if folder.exists():
        raise ValueError(f"Env {name!r} already exists.")
    folder.mkdir(parents=True)
    (folder / "workspace").mkdir()
    # The DB file will be created on first session via _build_engine.
    log.info("Created test env: %s", name)
    return folder


def reset_test_env(name: str) -> None:
    """
    Wipe everything inside a test env without removing the env itself.
    DB file is deleted; the workspace folder is emptied.
    Engine cache for this env is dropped so the next request rebuilds the DB.
    """
    if name == LIVE_ENV:
        raise ValueError("Refusing to reset the live env.")
    folder = TEST_ENVS_DIR / name
    if not folder.is_dir():
        raise ValueError(f"Env {name!r} doesn't exist.")
    db_path = folder / "poster.db"
    ws_path = folder / "workspace"
    # Drop cached engine for this env; closing it releases the SQLite file.
    with _engines_lock:
        eng = _engines.pop(name, None)
        _sessions.pop(name, None)
    if eng is not None:
        try:
            eng.dispose()
        except Exception:
            pass
    db_path.unlink(missing_ok=True)
    if ws_path.is_dir():
        shutil.rmtree(ws_path)
    ws_path.mkdir()
    log.info("Reset test env: %s", name)


def delete_test_env(name: str) -> None:
    """Wipe the env folder entirely. Cannot delete 'live'."""
    if name == LIVE_ENV:
        raise ValueError("Refusing to delete the live env.")
    folder = TEST_ENVS_DIR / name
    if not folder.is_dir():
        raise ValueError(f"Env {name!r} doesn't exist.")
    with _engines_lock:
        eng = _engines.pop(name, None)
        _sessions.pop(name, None)
    if eng is not None:
        try:
            eng.dispose()
        except Exception:
            pass
    shutil.rmtree(folder)
    log.info("Deleted test env: %s", name)


def reset_all_test_envs() -> int:
    """Called by the nightly job. Resets every test env. Returns how many were reset."""
    count = 0
    for name in list_test_envs():
        try:
            reset_test_env(name)
            count += 1
        except Exception:
            log.exception("Failed to reset test env: %s", name)
    return count
