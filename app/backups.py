"""
Backup + restore for poster.db.

Two flavours:
- AUTO: a background thread fires at server time midnight (and once on startup
  if there's no backup for "today" yet). Auto-backups are pruned to the most
  recent N days (config.AUTO_BACKUP_RETENTION_DAYS).
- MANUAL: admin-triggered snapshot with an optional name. Manual snapshots
  are never auto-pruned.

Both use SQLite's online backup API (sqlite3.Connection.backup), so the DB
stays consistent even if the server is taking writes mid-backup. No mutex or
"stop the world" needed for the backup itself.

Restore replaces poster.db with the chosen backup file. The SQLAlchemy engine
is disposed and recreated so existing pool connections are flushed; subsequent
requests open against the restored DB.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

from .config import BACKUPS_DIR, DB_PATH, AUTO_BACKUP_RETENTION_DAYS


log = logging.getLogger("poster.backups")

# File-naming prefixes — used to distinguish auto from manual snapshots
# without a separate index file.
AUTO_PREFIX = "auto-"
MANUAL_PREFIX = "manual-"

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_\-]+")


# ─── Helpers ────────────────────────────────────────────────────────────────


def _safe_name(s: str) -> str:
    """Slug a user-supplied name into something safe for a filename."""
    s = (s or "").strip()
    s = _SAFE_NAME_RE.sub("_", s)
    return s[:80]  # cap at 80 chars


def _backup_path(label: str) -> Path:
    """Return the canonical .db path for a backup with this label."""
    return BACKUPS_DIR / f"{label}.db"


def _do_sqlite_backup(target: Path) -> None:
    """SQLite-native online backup. Safe with concurrent writers."""
    if not DB_PATH.exists():
        # Nothing to backup yet — first run, no DB file. Skip silently.
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(DB_PATH))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


# ─── Auto-backup ────────────────────────────────────────────────────────────


def auto_backup_today() -> Optional[Path]:
    """
    Create today's auto-backup if not already present. Returns the path of the
    backup we created (or that already existed) — or None if nothing to back up.
    """
    if not DB_PATH.exists():
        return None
    today = date.today().isoformat()
    target = _backup_path(f"{AUTO_PREFIX}{today}")
    if target.exists():
        return target
    try:
        _do_sqlite_backup(target)
        log.info("Created daily auto-backup: %s", target.name)
        prune_auto_backups()
    except Exception:
        log.exception("Auto-backup failed")
        return None
    return target


def prune_auto_backups() -> int:
    """Delete auto-backup files older than AUTO_BACKUP_RETENTION_DAYS. Returns count pruned."""
    cutoff = date.today() - timedelta(days=AUTO_BACKUP_RETENTION_DAYS)
    pruned = 0
    for p in BACKUPS_DIR.glob(f"{AUTO_PREFIX}*.db"):
        # Parse YYYY-MM-DD out of "auto-YYYY-MM-DD.db"
        m = re.match(rf"^{re.escape(AUTO_PREFIX)}(\d{{4}}-\d{{2}}-\d{{2}})\.db$", p.name)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            try:
                p.unlink()
                pruned += 1
            except OSError:
                pass
    if pruned:
        log.info("Pruned %d old auto-backup(s)", pruned)
    return pruned


# ─── Manual snapshot ────────────────────────────────────────────────────────


def manual_snapshot(name: str) -> Path:
    """Create a manually-named snapshot. Returns the path."""
    if not DB_PATH.exists():
        raise RuntimeError("Database file does not exist yet — nothing to snapshot.")
    safe = _safe_name(name)
    if not safe:
        safe = "snapshot"
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = _backup_path(f"{MANUAL_PREFIX}{stamp}__{safe}")
    if target.exists():
        # Extremely unlikely but be defensive.
        raise FileExistsError(f"A snapshot with this name already exists: {target.name}")
    _do_sqlite_backup(target)
    log.info("Created manual snapshot: %s", target.name)
    return target


# ─── List + delete ──────────────────────────────────────────────────────────


def list_backups():
    """Return a list of dicts describing each backup file in BACKUPS_DIR."""
    out = []
    for p in sorted(BACKUPS_DIR.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        kind = "manual"
        label = p.stem  # filename without .db
        if p.name.startswith(AUTO_PREFIX):
            kind = "auto"
        elif p.name.startswith(MANUAL_PREFIX):
            kind = "manual"
        else:
            kind = "other"
        try:
            stat = p.stat()
            out.append({
                "filename": p.name,
                "label":    label,
                "kind":     kind,
                "size":     stat.st_size,
                "mtime":    datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except OSError:
            continue
    return out


def delete_backup(filename: str) -> bool:
    """Delete a backup by filename. Returns True if deleted, False if not found."""
    # Resolve and re-check that the file is inside BACKUPS_DIR (no traversal).
    target = (BACKUPS_DIR / filename).resolve()
    try:
        target.relative_to(BACKUPS_DIR.resolve())
    except ValueError:
        raise PermissionError("Path escapes the backups directory.")
    if not target.exists() or not target.is_file():
        return False
    target.unlink()
    return True


# ─── Restore ────────────────────────────────────────────────────────────────


def restore_backup(filename: str) -> Path:
    """
    Replace poster.db with the named backup. Must be called with the SQLAlchemy
    engine disposed first (the caller is responsible for that — see the admin
    route). Returns the path of the safety-snapshot we made before overwriting.

    A "pre-restore" snapshot is automatically created so the previous state is
    recoverable if the restore was a mistake.
    """
    target = (BACKUPS_DIR / filename).resolve()
    try:
        target.relative_to(BACKUPS_DIR.resolve())
    except ValueError:
        raise PermissionError("Path escapes the backups directory.")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"Backup file not found: {filename}")

    # Pre-restore safety snapshot (only if there's an existing DB to save).
    safety = None
    if DB_PATH.exists():
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        safety = _backup_path(f"{MANUAL_PREFIX}{stamp}__pre-restore")
        _do_sqlite_backup(safety)
        log.info("Pre-restore safety snapshot: %s", safety.name)

    # Replace the live DB. Use a copy + atomic rename to minimise window.
    import shutil
    tmp = DB_PATH.with_suffix(".db.restore-tmp")
    shutil.copy2(target, tmp)
    tmp.replace(DB_PATH)
    log.info("Restored database from: %s", target.name)
    return safety or DB_PATH


# ─── Background scheduler ───────────────────────────────────────────────────


def _scheduler_loop():
    """
    Run forever. Wake once per minute and:

      - if today's auto-backup is missing, run one (this also covers the
        catch-up case where the server was off at midnight)
      - make sure the image-generation worker is still running

    Both are idempotent. The backup check asks "does today's file exist", and
    the worker check asks "is the thread alive", so a restart mid-day cannot
    double-fire either.

    The generation check lives HERE rather than in its own thread on the
    principle that a watchdog needing its own watchdog has not helped. This
    loop already exists, already runs every minute, and is the simplest thing
    in the app.

    (This loop also used to send a daily summary email. That feature was
    never used and has been removed.)
    """
    from .timeutil import local_today, local_now

    # Initial catch-up backup (if today's missing).
    try:
        auto_backup_today()
    except Exception:
        log.exception("Catch-up backup failed")

    while True:
        try:
            now = local_now()
            today_local = local_today()

            # ── Does OpenAI agree with our own metering? ─────────────────
            # Once a day, guarded by its own date marker. Wrapped so a
            # failure here cannot stop the backup below — an unreachable
            # billing API must never cost you a backup.
            try:
                from .db import SessionLocal
                from .openai_costs import run_daily
                _db = SessionLocal()
                try:
                    run_daily(_db)
                finally:
                    _db.close()
            except Exception:
                log.exception("OpenAI cost reconciliation failed")

            # ── What did the marketplaces earn? ──────────────────────────
            # Once per local day, first tick after midnight. Its own try for
            # the usual reason: FineArtAmerica being unreachable, or one
            # account's password being wrong, must not cost a backup.
            try:
                from .db import SessionLocal
                from .earnings.service import run_daily_if_due, retry_unread_if_due
                _db = SessionLocal()
                try:
                    if run_daily_if_due(_db) is None:
                        # Today's run already happened. Anything it failed to
                        # read gets another go once that account's own cooldown
                        # expires, for a few hours. Only reached when the daily
                        # run did NOT just fire, so the two can never queue the
                        # same account twice in one tick.
                        retry_unread_if_due(_db)
                finally:
                    _db.close()
            except Exception:
                log.exception("Earnings read failed")

            # ── Is a listing sweep waiting to try again? ─────────────────
            # A wall or a maintenance page makes a sweep sleep rather than
            # give up. Nothing else would ever wake it: the node cannot
            # queue its own work, so the clock has to live here.
            try:
                from .db import SessionLocal
                from .routes.store_admin import wake_due_retries
                _db = SessionLocal()
                try:
                    wake_due_retries(_db)
                finally:
                    _db.close()
            except Exception:
                log.exception("Listing-sweep retry failed")

            # ── Is a listing sweep stuck with nothing working on it? ─────
            # The worker machine rebooting mid-account leaves a run in
            # `deactivating` for ever, holding Photoshop and the uploads all
            # night while the screen shows work in progress. Nothing else
            # notices: the node cannot report a crash it did not survive.
            # Separate try from the retry above so one failing cannot stop
            # the other.
            try:
                from .db import SessionLocal
                from .routes.store_admin import sweep_stalled_runs
                _db = SessionLocal()
                try:
                    for note in sweep_stalled_runs(_db):
                        log.warning("Stalled listing run repaired — %s", note)
                finally:
                    _db.close()
            except Exception:
                log.exception("Stalled listing-run sweep failed")

            # ── Is a marketplace listing sweep stuck? ────────────────────
            # Same invariant, different feature: a sweep that says "running"
            # must have a job running. This one holds nothing, so a stall
            # costs a wrong screen rather than an idle pipeline — but a
            # screen that lies all night is still a defect.
            try:
                from .db import SessionLocal
                from .routes.listing_admin import sweep_stalled
                _db = SessionLocal()
                try:
                    for note in sweep_stalled(_db):
                        log.warning("Stalled listing sweep repaired — %s", note)
                finally:
                    _db.close()
            except Exception:
                log.exception("Stalled listing-sweep check failed")

            # ── Is image generation still running? ───────────────────────
            # Wrapped in its own try so a failure here can never stop the
            # backups. Losing a day's backup to a watchdog would be a poor
            # trade.
            try:
                from .gpt_worker import supervise
                supervise()
            except Exception:
                log.exception("Generation worker supervision failed")

            # ── Backup check ─────────────────────────────────────────────
            today_backup = BACKUPS_DIR / f"auto-{today_local.isoformat()}.db"
            if not today_backup.exists():
                try:
                    auto_backup_today()
                except Exception:
                    log.exception("Auto-backup attempt failed")

            # Sleep one minute (60s) — fine-grained enough that admin
            # changing send_time takes effect within a minute.
            time.sleep(60)
        except Exception:
            log.exception("Scheduler loop iteration failed; pausing 5 minutes")
            time.sleep(300)


_scheduler_thread: Optional[threading.Thread] = None


def start_background_scheduler():
    """Start the daily backup thread (idempotent)."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    t = threading.Thread(target=_scheduler_loop, name="poster-backup-scheduler", daemon=True)
    t.start()
    _scheduler_thread = t
    log.info("Backup scheduler started")
