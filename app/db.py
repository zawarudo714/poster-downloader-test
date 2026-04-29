"""
SQLAlchemy engine + session factory.

This module is intentionally thin: most of the work happens in `envs.py`,
which manages a per-environment engine cache. The exports below are all
*active-env aware* — they look up the active env from a contextvar at call
time, so route handlers don't need to know which env they're in.

The classic single-engine pattern (e.g. `from .db import engine`) still
works for the live env: just call `live_engine()`. New code should prefer
`current_engine()` / `get_db()` so it transparently follows env switches.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DATABASE_URL


# Base is used by models.py and `Base.metadata.create_all(...)`.
Base = declarative_base()


# Live-env engine. Kept as a module-level constant so existing imports from
# `app.db` keep working (used by SQLAlchemy migrations / startup hooks).
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    """
    FastAPI dependency that yields a DB session for the *active environment*
    and closes it after the request. Active env defaults to "live"; admins
    switch into test envs via the env-switcher UI which sets a cookie that
    middleware translates into a contextvar.
    """
    # Imported lazily to avoid a circular import (envs.py uses Base from here).
    from .envs import current_session_factory
    Session = current_session_factory()
    db = Session()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables in the live env. Safe to call repeatedly."""
    from . import models  # noqa: F401 — register models on Base.metadata
    Base.metadata.create_all(bind=engine)
