"""
Timezone helpers.

Storage strategy:
  All DateTime columns store UTC (via datetime.utcnow). This is the
  conventional, portable thing to do — the DB is always interpretable
  no matter which TZ the server runs in.

Display strategy:
  Anywhere a timestamp is rendered to a human (templates, JSON
  responses), convert it to APP_TZ first. APP_TZ is read from the
  APP_TZ environment variable (e.g. "Africa/Nairobi"); falls back to
  the system local TZ if unset, and finally UTC if that fails.

Date-bucket strategy:
  "Today" / "this week" buckets need a wall-clock concept of date,
  not UTC. local_today() returns date.today() in APP_TZ — use this
  instead of datetime.utcnow().date() anywhere a worker's "today"
  is being computed.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional

try:
    # Python 3.9+ ships zoneinfo in stdlib. We rely on it; if the
    # tzdata package is missing on bare-bones containers, fall back
    # gracefully to the system local TZ via .astimezone().
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover — Python <3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


def _resolve_tz():
    """
    Resolve APP_TZ → ZoneInfo, or None to fall back to system local time.

    Cached at import time. Restart the container to pick up changes
    to the APP_TZ env var.
    """
    name = os.environ.get("APP_TZ", "").strip()
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # The image lacks the tzdata files for this zone. Caller will
        # fall back to system local time, which is usually correct
        # because docker-compose also sets `TZ=` to the same value.
        return None


APP_TZ = _resolve_tz()


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Convert a stored UTC datetime to APP_TZ for display.

    Existing DB rows have naive datetimes (no tzinfo) created from
    `datetime.utcnow()` — we tag them as UTC, then convert. Naive
    datetimes that are NOT UTC would render incorrectly, but this
    codebase only ever writes via utcnow() so that's fine.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if APP_TZ is not None:
        return dt.astimezone(APP_TZ)
    # Fall back to system local TZ — this works correctly when
    # docker-compose sets TZ=Africa/Nairobi alongside APP_TZ.
    return dt.astimezone()


def fmt_local(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Convert UTC → APP_TZ and format. Empty string if dt is None."""
    if dt is None:
        return ""
    return to_local(dt).strftime(fmt)


def local_today() -> date:
    """
    Today's date in APP_TZ. Use this everywhere the app needs a
    wall-clock date (counters, save buckets, payment periods, etc.).
    """
    if APP_TZ is not None:
        return datetime.now(APP_TZ).date()
    return date.today()


def local_now() -> datetime:
    """Wall-clock 'now' in APP_TZ as a naive datetime (for display only)."""
    if APP_TZ is not None:
        return datetime.now(APP_TZ).replace(tzinfo=None)
    return datetime.now()
