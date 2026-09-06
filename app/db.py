"""SQLAlchemy engine + session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DATABASE_URL


Base = declarative_base()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

if DATABASE_URL.startswith("sqlite"):
    # ── WAL mode: readers never wait for writers (added in the 2026-09-06
    # speed pass). The site is many small readers — every open tab polls the
    # pulse, the node polls for work, the GPT worker writes — and in SQLite's
    # default mode one writer BLOCKS every reader for the length of its
    # write. WAL lets reads proceed during writes, which is exactly this
    # site's shape. It is a property of the DATABASE FILE, set once, safe to
    # re-issue on every connection.
    #
    # busy_timeout replaces instant "database is locked" errors with a short
    # wait — the difference between a rare 500 and an unnoticeable 50 ms.
    #
    # synchronous=NORMAL is the documented pairing for WAL: it can lose the
    # last moments of work in a power cut, never corrupt the file. Backups
    # run nightly and the pipeline re-derives its state, so that trade is
    # right here.
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_tuning(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    """FastAPI dependency that yields a DB session, closes after request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call repeatedly."""
    from . import models  # noqa: F401 — register models on Base.metadata
    Base.metadata.create_all(bind=engine)
