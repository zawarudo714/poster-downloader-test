"""
Payments helper — eligible-poster counting and payment-run bookkeeping.

A poster counts toward pay only if ALL of:
  - It's a non-deleted SavedPoster (deleted_at IS NULL).
  - Saved by the worker in question (matched by user_id).
  - Created on a date inside the requested period.
  - It has NO open or awaiting-approval revision against it RIGHT NOW.
  - It hasn't already been paid (poster_ids_json across past PaymentRuns).

The "no open revision" check is intentional — if admin flagged it but the
worker hasn't fixed yet, we don't pay for it. If/when the revision resolves,
the poster becomes eligible for the NEXT payment run.

Replacement counts as 1 unit because there's only one SavedPoster row regardless
of how many times its bytes were swapped.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import AppSetting, PaymentRun, Revision, SavedPoster


# ─── Settings helpers ──────────────────────────────────────────────────────

DEFAULT_RATE_KES = "10"          # change defaults via Admin → Payments
DEFAULT_WEEK_START = "0"         # 0 = Monday, 6 = Sunday (ISO weekday convention - 1)


def get_setting(db: Session, key: str, default: str) -> str:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        return default
    return row.value


def set_setting(db: Session, key: str, value: str, *, by: str | None = None) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=by)
        db.add(row)
    else:
        row.value = value
        row.updated_by = by
        row.updated_at = datetime.utcnow()


def get_rate_kes(db: Session) -> str:
    return get_setting(db, "pay_rate_kes", DEFAULT_RATE_KES)


def get_week_start_day(db: Session) -> int:
    """0..6 where 0 = Monday."""
    raw = get_setting(db, "week_start_day", DEFAULT_WEEK_START)
    try:
        n = int(raw)
        return n if 0 <= n <= 6 else 0
    except ValueError:
        return 0


def parse_decimal(raw: str) -> Decimal:
    """Parse a decimal-ish string. Raises ValueError on bad input."""
    try:
        return Decimal((raw or "0").strip())
    except InvalidOperation as e:
        raise ValueError(f"Invalid number: {raw!r}") from e


# ─── Date helpers ──────────────────────────────────────────────────────────

def week_bounds_containing(d: date, week_start: int) -> tuple[date, date]:
    """
    Return [start, end] (both inclusive) of the week that contains `d`,
    where the week starts on `week_start` (0=Mon..6=Sun).
    """
    # Python's date.weekday() returns 0=Mon..6=Sun, perfect for our convention.
    delta = (d.weekday() - week_start) % 7
    start = d - timedelta(days=delta)
    end   = start + timedelta(days=6)
    return start, end


def daterange_inclusive(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


# ─── Eligibility queries ──────────────────────────────────────────────────

def _already_paid_poster_ids(db: Session, worker_id: int) -> set[int]:
    """All saved_poster IDs that were already counted in a past PaymentRun for this worker."""
    paid: set[int] = set()
    rows = db.query(PaymentRun.poster_ids_json).filter_by(worker_id=worker_id).all()
    for (raw,) in rows:
        if not raw:
            continue
        try:
            ids = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for pid in ids:
            if isinstance(pid, int):
                paid.add(pid)
    return paid


def eligible_poster_ids(
    db: Session,
    *,
    worker_id: int,
    start: date,
    end: date,
) -> list[int]:
    """
    Return the saved_poster IDs that count toward pay for this worker
    over [start, end]. See module docstring for the rules.

    Used by both the preview endpoint (admin browsing what they'd pay for)
    and mark_paid (the actual payment run write).
    """
    # Posters saved by this worker in [start, end], not deleted.
    base_q = (
        db.query(SavedPoster.id)
          .filter(
              SavedPoster.user_id == worker_id,
              SavedPoster.deleted_at.is_(None),
              SavedPoster.original_save_date >= start,
              SavedPoster.original_save_date <= end,
          )
    )
    candidate_ids = {row[0] for row in base_q.all()}
    if not candidate_ids:
        return []

    # Subtract already-paid IDs.
    candidate_ids -= _already_paid_poster_ids(db, worker_id)
    if not candidate_ids:
        return []

    # Subtract IDs that have an open or awaiting-approval revision RIGHT NOW.
    blocked_rows = (
        db.query(Revision.saved_poster_id)
          .filter(
              Revision.saved_poster_id.in_(candidate_ids),
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .all()
    )
    blocked = {row[0] for row in blocked_rows}
    candidate_ids -= blocked

    # Also subtract IDs that appear inside a similar-pair revision's
    # related_poster_ids JSON — those are still under review even if the
    # blocked saved_poster_id IS one of the related, not the primary.
    sim_rows = (
        db.query(Revision.related_poster_ids)
          .filter(
              Revision.status.in_(("open", "awaiting_approval")),
              Revision.revision_type == "similar",
          )
          .all()
    )
    for (raw,) in sim_rows:
        if not raw:
            continue
        try:
            ids = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for pid in ids:
            if isinstance(pid, int):
                candidate_ids.discard(pid)

    return sorted(candidate_ids)


def count_pending_revisions_today(db: Session, worker_id: int) -> int:
    """
    How many of *this worker's* live, today-saved posters are sitting under
    an open / awaiting-approval revision. Surfaced on the worker's dashboard
    as "X not counted until revised" for transparency.
    """
    today = date.today()
    base_ids = {
        row[0] for row in db.query(SavedPoster.id).filter(
            SavedPoster.user_id == worker_id,
            SavedPoster.deleted_at.is_(None),
            SavedPoster.original_save_date == today,
        ).all()
    }
    if not base_ids:
        return 0
    blocked = (
        db.query(Revision.saved_poster_id)
          .filter(
              Revision.saved_poster_id.in_(base_ids),
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .count()
    )
    return blocked


# ─── Payment-run summary helpers ──────────────────────────────────────────

def runs_for_worker(db: Session, worker_id: int, *, limit: int = 50):
    """Recent runs for one worker, newest first."""
    return (
        db.query(PaymentRun)
          .filter_by(worker_id=worker_id)
          .order_by(PaymentRun.created_at.desc())
          .limit(limit)
          .all()
    )


def all_runs(db: Session, *, limit: int = 200):
    return (
        db.query(PaymentRun)
          .order_by(PaymentRun.created_at.desc())
          .limit(limit)
          .all()
    )


def pending_receipts_for_worker(db: Session, worker_id: int):
    """Pushed but not yet acknowledged receipts the worker should see."""
    return (
        db.query(PaymentRun)
          .filter(
              PaymentRun.worker_id == worker_id,
              PaymentRun.pushed_at.isnot(None),
              PaymentRun.ack_at.is_(None),
          )
          .order_by(PaymentRun.pushed_at.desc())
          .all()
    )
