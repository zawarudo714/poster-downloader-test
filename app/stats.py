"""
Worker performance stats — used by both the worker /stats page and the
admin's per-worker view.

The shape is consistent across both callers; admin gets a few extra fields
(flag rate, revision turnaround time) but the core series and totals are
identical. All amounts are computed using the CURRENT rate from app_settings
so workers see a forward-looking projection; past payment runs preserve
their historical rate via PaymentRun.rate_kes when shown elsewhere.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, true as sa_true
from sqlalchemy.orm import Session

from .models import MasterTitle, PaymentRun, Revision, SavedPoster, User
from .payments import (
    eligible_poster_ids, get_rate_kes, parse_decimal,
    get_week_start_day,
)
from .timeutil import local_today


# ════════════════════════════════════════════════════════════════════════════
#  PROJECT SCOPING
# ════════════════════════════════════════════════════════════════════════════
# Worker output is a PERSON-level fact: someone covering two niches has one
# throughput, one streak and one flag rate, and splitting them by default
# would answer a question nobody asked. So combined is the default and the
# filter is opt-in — which is also what was asked for.
#
# Scoped through master_titles rather than SavedPoster.project_folder. That
# column is a denormalised PATH, kept for building file paths cheaply; using
# it as an identity would break the moment a project is renamed, and quietly,
# because the numbers would simply get smaller.


def _poster_scope(db: Session, project_id: Optional[int]):
    """Criterion limiting saved posters to one project, or everything."""
    if not project_id:
        return sa_true()

    from .pipeline import _default_project_id, project_scope
    titles = (
        db.query(MasterTitle.id)
          .filter(project_scope(project_id,
                                default_project_id=_default_project_id(db)))
    )
    return SavedPoster.master_title_id.in_(titles.scalar_subquery())


def _title_scope(db: Session, project_id: Optional[int]):
    """The same, for queries that count titles rather than images."""
    if not project_id:
        return sa_true()
    from .pipeline import _default_project_id, project_scope
    return project_scope(project_id, default_project_id=_default_project_id(db))


def _week_start(d: date, week_start_dow: int) -> date:
    """Return the Monday-or-whatever date for the week containing d."""
    # weekday(): Monday=0..Sunday=6. week_start_dow uses same convention.
    offset = (d.weekday() - week_start_dow) % 7
    return d - timedelta(days=offset)


def _save_dates(db: Session, *, username: str, scope=None) -> list[date]:
    """All distinct calendar dates this worker saved at least one live poster."""
    rows = (
        db.query(SavedPoster.original_save_date)
          .filter(SavedPoster.username == username,
                  SavedPoster.deleted_at.is_(None),
                  scope if scope is not None else sa_true())
          .distinct()
          .all()
    )
    return sorted(d for (d,) in rows if d is not None)


def _longest_streak(dates: list[date]) -> tuple[int, Optional[date], Optional[date]]:
    """Longest run of consecutive calendar dates with saves. Returns
    (length, start, end). Empty list → (0, None, None)."""
    if not dates:
        return 0, None, None
    best_len = 1
    best_start = dates[0]
    best_end = dates[0]
    cur_len = 1
    cur_start = dates[0]
    for i in range(1, len(dates)):
        if dates[i] - dates[i-1] == timedelta(days=1):
            cur_len += 1
        else:
            cur_len = 1
            cur_start = dates[i]
        if cur_len > best_len:
            best_len = cur_len
            best_start = cur_start
            best_end = dates[i]
    return best_len, best_start, best_end


def _best_day(db: Session, *, username: str, scope=None) -> tuple[int, Optional[date]]:
    """The single day with the highest count of live saves."""
    row = (
        db.query(SavedPoster.original_save_date,
                 func.count(SavedPoster.id).label("n"))
          .filter(SavedPoster.username == username,
                  SavedPoster.deleted_at.is_(None),
                  scope if scope is not None else sa_true())
          .group_by(SavedPoster.original_save_date)
          .order_by(func.count(SavedPoster.id).desc())
          .first()
    )
    if row is None:
        return 0, None
    return int(row.n or 0), row.original_save_date


def _best_week(
    db: Session, *, username: str, week_start_dow: int, scope=None
) -> tuple[int, Optional[date]]:
    """Best 7-day window (week-aligned to admin's configured week start)."""
    # Per-day counts, then bucket by week_start.
    rows = (
        db.query(SavedPoster.original_save_date,
                 func.count(SavedPoster.id).label("n"))
          .filter(SavedPoster.username == username,
                  SavedPoster.deleted_at.is_(None),
                  scope if scope is not None else sa_true())
          .group_by(SavedPoster.original_save_date)
          .all()
    )
    bucket: dict[date, int] = {}
    for r in rows:
        if r.original_save_date is None:
            continue
        ws = _week_start(r.original_save_date, week_start_dow)
        bucket[ws] = bucket.get(ws, 0) + int(r.n or 0)
    if not bucket:
        return 0, None
    best = max(bucket.items(), key=lambda kv: kv[1])
    return best[1], best[0]


def _total_paid_kes(db: Session, *, worker_id: int) -> str:
    """Sum of all PaymentRun.amount_kes for this worker (as decimal string)."""
    rows = (
        db.query(PaymentRun.amount_kes)
          .filter(PaymentRun.worker_id == worker_id)
          .all()
    )
    total = Decimal("0")
    for (amt,) in rows:
        try:
            total += parse_decimal(amt or "0")
        except Exception:
            pass
    return str(total)


def _eligible_unpaid_count(db: Session, *, worker_id: int) -> int:
    """Posters currently eligible across ALL time, not yet in a payment run."""
    # eligible_poster_ids called with [epoch, today] covers all.
    return len(eligible_poster_ids(
        db, worker_id=worker_id,
        start=date(1970, 1, 1), end=local_today(),
    ))


def compute_worker_stats(
    db: Session, *, worker_id: int, is_admin_view: bool = False,
    project_id: Optional[int] = None,
) -> dict:
    """
    Heavy-lifting stats query — used by both worker and admin endpoints.
    Returns a single dict; the caller serializes it to JSON.

    Performance note: this runs O(n) over saved_posters and revisions for
    a single worker, which is fine at expected scale (one or two workers
    saving up to a few thousand posters a month). At 10k+ posters/worker
    we'd want to denormalize daily totals into a materialized table; not
    worth doing now.
    """
    u = db.query(User).filter_by(id=worker_id).first()
    if not u:
        return {"ok": False, "error": "Worker not found."}

    today = local_today()
    rate_dec = parse_decimal(get_rate_kes(db))
    week_start_dow = get_week_start_day(db)

    # None means every project, which is the default and the common case.
    scope = _poster_scope(db, project_id)
    tscope = _title_scope(db, project_id)

    # ── 30-day chart series (most recent days, oldest → newest) ─────────
    series: list[dict] = []
    start_30 = today - timedelta(days=29)
    rows = (
        db.query(SavedPoster.original_save_date,
                 func.count(SavedPoster.id).label("n"))
          .filter(SavedPoster.username == u.username,
                  SavedPoster.deleted_at.is_(None),
                  SavedPoster.original_save_date >= start_30,
                  SavedPoster.original_save_date <= today,
                  scope)
          .group_by(SavedPoster.original_save_date)
          .all()
    )
    by_day: dict[date, int] = {r.original_save_date: int(r.n or 0) for r in rows if r.original_save_date}
    for i in range(30):
        d = start_30 + timedelta(days=i)
        n = by_day.get(d, 0)
        kes = rate_dec * n
        series.append({
            "date":  d.isoformat(),
            "count": n,
            "kes":   str(kes),
        })

    # ── Totals ──────────────────────────────────────────────────────────
    total_saved = (
        db.query(func.count(SavedPoster.id))
          .filter(SavedPoster.username == u.username,
                  SavedPoster.deleted_at.is_(None),
                  scope)
          .scalar() or 0
    )
    total_completed = (
        db.query(func.count(MasterTitle.id))
          .filter(MasterTitle.claimed_by_id == u.id,
                  MasterTitle.status == "complete",
                  tscope)
          .scalar() or 0
    )
    total_paid_str = _total_paid_kes(db, worker_id=u.id)
    eligible_unpaid = _eligible_unpaid_count(db, worker_id=u.id)
    eligible_unpaid_kes = str(rate_dec * eligible_unpaid)
    total_earned_str = str(parse_decimal(total_paid_str) + rate_dec * eligible_unpaid)

    # ── This week / last week / this month / last month ────────────────
    this_week_start = _week_start(today, week_start_dow)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end   = this_week_start - timedelta(days=1)
    this_month_start = today.replace(day=1)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(
            year=this_month_start.year - 1, month=12, day=1)
    else:
        last_month_start = this_month_start.replace(
            month=this_month_start.month - 1, day=1)
    last_month_end = this_month_start - timedelta(days=1)

    def _count_between(start_d: date, end_d: date) -> int:
        return (
            db.query(func.count(SavedPoster.id))
              .filter(SavedPoster.username == u.username,
                      SavedPoster.deleted_at.is_(None),
                      SavedPoster.original_save_date >= start_d,
                      SavedPoster.original_save_date <= end_d,
                      scope)
              .scalar() or 0
        )

    this_week_count  = _count_between(this_week_start, today)
    last_week_count  = _count_between(last_week_start, last_week_end)
    this_month_count = _count_between(this_month_start, today)
    last_month_count = _count_between(last_month_start, last_month_end)

    def _pct_delta(now: int, then: int) -> Optional[float]:
        if then == 0:
            return None  # undefined when prior period was zero
        return ((now - then) / then) * 100.0

    # Projected end-of-week count (linear extrapolation by days elapsed).
    days_in_week = (today - this_week_start).days + 1
    if days_in_week > 0 and this_week_count > 0:
        projected_week = round(this_week_count * (7.0 / days_in_week))
    else:
        projected_week = this_week_count
    projected_week_kes = str(rate_dec * projected_week)

    # ── Records ─────────────────────────────────────────────────────────
    best_day_n, best_day_date = _best_day(db, username=u.username, scope=scope)
    best_week_n, best_week_start_d = _best_week(
        db, username=u.username, week_start_dow=week_start_dow, scope=scope,
    )
    all_dates = _save_dates(db, username=u.username, scope=scope)
    streak_len, streak_start, streak_end = _longest_streak(all_dates)

    out = {
        "ok": True,
        "worker_id":   u.id,
        "username":    u.username,
        "today":       today.isoformat(),
        "rate_kes":    str(rate_dec),
        "week_start_day": week_start_dow,
        # Echoed back so the page can state what it is showing. A stats
        # screen that silently filters is worse than one that cannot.
        "project_id":  project_id,

        "totals": {
            "saved":            total_saved,
            "completed_titles": total_completed,
            "paid_kes":         total_paid_str,
            "eligible_unpaid":      eligible_unpaid,
            "eligible_unpaid_kes":  eligible_unpaid_kes,
            "earned_kes":       total_earned_str,   # paid + unpaid eligible
        },

        "series_30": series,    # oldest → newest, 30 entries

        "this_week": {
            "start": this_week_start.isoformat(),
            "count": this_week_count,
            "kes":   str(rate_dec * this_week_count),
            "projected_count": projected_week,
            "projected_kes":   projected_week_kes,
        },
        "last_week": {
            "start": last_week_start.isoformat(),
            "end":   last_week_end.isoformat(),
            "count": last_week_count,
            "kes":   str(rate_dec * last_week_count),
        },
        "this_month": {
            "start": this_month_start.isoformat(),
            "count": this_month_count,
            "kes":   str(rate_dec * this_month_count),
        },
        "last_month": {
            "start": last_month_start.isoformat(),
            "end":   last_month_end.isoformat(),
            "count": last_month_count,
            "kes":   str(rate_dec * last_month_count),
        },
        "deltas": {
            "week_vs_last":   _pct_delta(this_week_count, last_week_count),
            "month_vs_last":  _pct_delta(this_month_count, last_month_count),
        },

        "records": {
            "best_day": {
                "count": best_day_n,
                "date":  best_day_date.isoformat() if best_day_date else None,
            },
            "best_week": {
                "count": best_week_n,
                "start": best_week_start_d.isoformat() if best_week_start_d else None,
            },
            "longest_streak": {
                "days":  streak_len,
                "start": streak_start.isoformat() if streak_start else None,
                "end":   streak_end.isoformat() if streak_end else None,
            },
        },
    }

    if is_admin_view:
        # Flag rate: % of this worker's live saves that have an active or
        # historical (resolved) revision attached.
        flagged_n = (
            db.query(func.count(func.distinct(Revision.saved_poster_id)))
              .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
              .filter(SavedPoster.username == u.username,
                      SavedPoster.deleted_at.is_(None),
                      scope)
              .scalar() or 0
        )
        flag_rate = (flagged_n / total_saved * 100.0) if total_saved > 0 else None

        # Revision turnaround: time from revision.created_at → resolved_at,
        # average across resolved revisions on this worker's posters.
        resolved = (
            db.query(Revision.created_at, Revision.resolved_at)
              .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
              .filter(SavedPoster.username == u.username,
                      Revision.status == "resolved",
                      Revision.resolved_at.isnot(None),
                      scope)
              .all()
        )
        if resolved:
            total_sec = sum((r.resolved_at - r.created_at).total_seconds()
                            for r in resolved if r.created_at and r.resolved_at)
            avg_sec = total_sec / len(resolved)
            avg_hours = avg_sec / 3600.0
        else:
            avg_hours = None

        out["admin_only"] = {
            "flagged_posters": flagged_n,
            "flag_rate_pct":   flag_rate,
            "resolved_revisions":     len(resolved),
            "avg_turnaround_hours":   avg_hours,
        }

    return out
