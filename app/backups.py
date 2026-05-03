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


def _seconds_until_next_midnight() -> float:
    now = datetime.now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


def _scheduler_loop():
    """
    Run forever. Wake once per minute. On each tick:
      - If today's auto-backup is missing, run one (also covers the
        startup catch-up case when the server was off at midnight).
      - If the configured email send_time has passed today AND we haven't
        already sent today's summary, send it.

    Both checks are idempotent — they look at filesystem (backup file
    existence) and a small marker setting (last email send date), so a
    restart mid-day doesn't double-fire either action.
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

            # ── Backup check ─────────────────────────────────────────────
            today_backup = BACKUPS_DIR / f"auto-{today_local.isoformat()}.db"
            if not today_backup.exists():
                try:
                    auto_backup_today()
                except Exception:
                    log.exception("Auto-backup attempt failed")

            # ── Daily email check ────────────────────────────────────────
            try:
                _maybe_send_daily_email(now=now, today_local=today_local)
            except Exception:
                log.exception("Daily email send attempt failed")

            # Sleep one minute (60s) — fine-grained enough that admin
            # changing send_time takes effect within a minute.
            time.sleep(60)
        except Exception:
            log.exception("Scheduler loop iteration failed; pausing 5 minutes")
            time.sleep(300)


def _maybe_send_daily_email(*, now: datetime, today_local) -> None:
    """
    Per-tick email check. Reads config + last-sent marker from app_settings
    and decides whether to send. Updates the marker on success.
    """
    from .db import SessionLocal
    from .email_summary import get_email_config, send_daily_summary
    from .models import AppSetting

    db = SessionLocal()
    try:
        cfg = get_email_config(db)
        if not cfg["enabled"]:
            return

        # Parse the configured send_time (HH:MM, defaults to 23:55).
        try:
            hh, mm = (cfg.get("send_time") or "23:55").split(":")
            send_h, send_m = int(hh), int(mm)
        except Exception:
            send_h, send_m = 23, 55

        # Has the time-of-day passed yet, today?
        if (now.hour, now.minute) < (send_h, send_m):
            return

        # Already sent today? Marker key stores ISO date of last send.
        marker = db.query(AppSetting).filter_by(key="email_last_sent_date").first()
        last_sent_iso = marker.value if marker else ""
        if last_sent_iso == today_local.isoformat():
            return

        # Send and record. If sending fails, don't update the marker — we'll
        # retry on the next tick (1 minute later).
        sent = send_daily_summary(db, target_day=today_local)
        if sent:
            if marker is None:
                marker = AppSetting(key="email_last_sent_date", value=today_local.isoformat(),
                                    updated_by="scheduler")
                db.add(marker)
            else:
                marker.value = today_local.isoformat()
                marker.updated_by = "scheduler"
                marker.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


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
