"""
Email — daily summary to admin.

Configuration is stored in `app_settings` (key/value table) so admin can
edit via the UI without redeploying:

    email_enabled        "1" | "0"
    email_recipient      "you@example.com"
    email_smtp_host      "smtp.gmail.com"
    email_smtp_port      "587"
    email_smtp_username  "you@gmail.com"
    email_smtp_password  "app-password"
    email_smtp_use_tls   "1" | "0"   (default 1; STARTTLS for port 587, SMTPS for 465)
    email_from           "Poster Downloader <you@gmail.com>"
    email_send_time      "23:55"     (HH:MM in APP_TZ)

The scheduler in backups.py wakes once per minute past midnight to do
the daily backup; we hook a check there to also send the day's summary
when the configured send_time passes.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Optional

from sqlalchemy.orm import Session

from .models import AppSetting, MasterTitle, Revision, SavedPoster, User
from .payments import (
    eligible_poster_ids, get_rate_kes, get_week_start_day,
    parse_decimal, week_bounds_containing,
)
from .timeutil import local_today


log = logging.getLogger(__name__)


# ─── Settings access ─────────────────────────────────────────────────────────

def _setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(AppSetting).filter_by(key=key).first()
    return row.value if row else default


def _set(db: Session, key: str, value: str, *, by: Optional[str] = None) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=by)
        db.add(row)
    else:
        row.value = value
        row.updated_by = by
        row.updated_at = datetime.utcnow()


def get_email_config(db: Session) -> dict:
    return {
        "enabled":       _setting(db, "email_enabled", "0") == "1",
        "recipient":     _setting(db, "email_recipient", ""),
        "smtp_host":     _setting(db, "email_smtp_host", ""),
        "smtp_port":     int(_setting(db, "email_smtp_port", "587") or "587"),
        "smtp_username": _setting(db, "email_smtp_username", ""),
        "smtp_password": _setting(db, "email_smtp_password", ""),
        "smtp_use_tls":  _setting(db, "email_smtp_use_tls", "1") == "1",
        "from_addr":     _setting(db, "email_from", ""),
        "send_time":     _setting(db, "email_send_time", "23:55"),
    }


def save_email_config(db: Session, *, by: str, data: dict) -> None:
    """Persist all email_* settings from a dict. Missing keys preserved."""
    for ui_key, db_key in [
        ("enabled",       "email_enabled"),
        ("recipient",     "email_recipient"),
        ("smtp_host",     "email_smtp_host"),
        ("smtp_port",     "email_smtp_port"),
        ("smtp_username", "email_smtp_username"),
        ("smtp_password", "email_smtp_password"),
        ("smtp_use_tls",  "email_smtp_use_tls"),
        ("from_addr",     "email_from"),
        ("send_time",     "email_send_time"),
    ]:
        if ui_key in data:
            v = data[ui_key]
            if isinstance(v, bool):
                v = "1" if v else "0"
            _set(db, db_key, str(v), by=by)


# ─── Sending ─────────────────────────────────────────────────────────────────

def _send_smtp(cfg: dict, subject: str, body_text: str) -> None:
    """Send a single email via SMTP. Raises on failure."""
    if not cfg.get("recipient"):
        raise ValueError("No recipient address configured.")
    if not cfg.get("smtp_host"):
        raise ValueError("No SMTP host configured.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = cfg.get("from_addr") or cfg.get("smtp_username") or "noreply@localhost"
    msg["To"]      = cfg["recipient"]
    msg.set_content(body_text)

    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    use_tls    = cfg.get("smtp_use_tls", True)
    username   = cfg.get("smtp_username") or None
    password   = cfg.get("smtp_password") or None

    # Port 465 = SMTPS (TLS from connect); other ports = SMTP, with optional STARTTLS.
    if port == 465:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
            if username:
                smtp.login(username, password or "")
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if use_tls:
                ctx = ssl.create_default_context()
                smtp.starttls(context=ctx)
                smtp.ehlo()
            if username:
                smtp.login(username, password or "")
            smtp.send_message(msg)


def send_test_email(cfg: dict) -> None:
    """Used by the 'send test' button — proves the settings work."""
    body = (
        "This is a test message from your Poster Downloader install.\n\n"
        "If you received this, your SMTP settings are working. The daily "
        "summary email will use these same settings.\n"
    )
    _send_smtp(cfg, "Poster Downloader — test email", body)


# ─── Daily summary ───────────────────────────────────────────────────────────

def _count_eligible(db: Session, *, worker_id: int, start: date, end: date) -> int:
    return len(eligible_poster_ids(db, worker_id=worker_id, start=start, end=end))


def _format_kes(rate_dec, count: int) -> str:
    total = rate_dec * count
    s = str(total)
    if "." in s:
        s = s.rstrip("0").rstrip(".") or "0"
    return s


def build_daily_summary(db: Session, *, target_day: date) -> tuple[str, str]:
    """
    Return (subject, body_text) for the day-of summary email.

    Body has TWO sections:
      "Today's activity"     — raw counts for target_day
      "Week so far …"        — running total from week_start through target_day
                              (omitted on day-1 of the week — same as today)

    Per-worker breakdown is included in both sections.
    """
    rate     = get_rate_kes(db)
    rate_dec = parse_decimal(rate)
    week_start_day = get_week_start_day(db)
    week_start, week_end = week_bounds_containing(target_day, week_start_day)

    workers = (
        db.query(User)
          .filter(User.role == "worker", User.is_active == 1, User.is_deleted == 0)
          .order_by(User.username.asc())
          .all()
    )

    # Per-worker stats for "today" and "week-so-far".
    today_total = 0
    week_total  = 0
    today_rows: list[tuple[str, int]] = []
    week_rows:  list[tuple[str, int]] = []
    for w in workers:
        n_today = _count_eligible(db, worker_id=w.id, start=target_day, end=target_day)
        n_week  = _count_eligible(db, worker_id=w.id, start=week_start, end=target_day)
        if n_today: today_rows.append((w.username, n_today))
        if n_week:  week_rows.append((w.username, n_week))
        today_total += n_today
        week_total  += n_week

    # Aggregate counts (across all workers, without payment eligibility) so we
    # also surface flags + deletions independent of pay-eligibility filters.
    new_flags_today = (
        db.query(Revision)
          .filter(
              Revision.created_at >= datetime.combine(target_day, datetime.min.time()),
              Revision.created_at <  datetime.combine(target_day + timedelta(days=1), datetime.min.time()),
          )
          .count()
    )
    deletions_today = (
        db.query(SavedPoster)
          .filter(
              SavedPoster.deleted_at.isnot(None),
              SavedPoster.deleted_at >= datetime.combine(target_day, datetime.min.time()),
              SavedPoster.deleted_at <  datetime.combine(target_day + timedelta(days=1), datetime.min.time()),
          )
          .count()
    )

    # Distinct titles touched today (any save in target_day).
    titles_today = (
        db.query(SavedPoster.master_title_id)
          .filter(SavedPoster.original_save_date == target_day)
          .distinct()
          .count()
    )

    weekday_name = target_day.strftime("%A")
    is_first_day_of_week = (target_day == week_start)

    subj = f"Poster Downloader · daily summary · {target_day.isoformat()} ({weekday_name})"

    lines: list[str] = []
    lines.append(f"Daily summary for {target_day.isoformat()} ({weekday_name})")
    lines.append("=" * 60)
    lines.append("")

    lines.append("Today's activity")
    lines.append("-" * 60)
    lines.append(f"  Posters saved (eligible for pay):  {today_total}")
    lines.append(f"  Distinct titles touched:           {titles_today}")
    lines.append(f"  New flags raised:                  {new_flags_today}")
    lines.append(f"  Deletions:                         {deletions_today}")
    lines.append(f"  Eligible amount today:             KES {_format_kes(rate_dec, today_total)}")
    lines.append("")
    if today_rows:
        lines.append("  Per worker:")
        for name, n in today_rows:
            lines.append(f"    · {name:<20s}  {n:>4d} posters   KES {_format_kes(rate_dec, n)}")
        lines.append("")

    if not is_first_day_of_week:
        lines.append(f"Week so far ({week_start.isoformat()} → {target_day.isoformat()})")
        lines.append("-" * 60)
        lines.append(f"  Total eligible posters:            {week_total}")
        lines.append(f"  Total eligible amount:             KES {_format_kes(rate_dec, week_total)}")
        lines.append("")
        if week_rows:
            lines.append("  Per worker:")
            for name, n in week_rows:
                lines.append(f"    · {name:<20s}  {n:>4d} posters   KES {_format_kes(rate_dec, n)}")
            lines.append("")
    else:
        lines.append("(Week just started — tomorrow's email will include a running tally.)")
        lines.append("")

    if target_day == week_end:
        lines.append("─" * 60)
        lines.append("→ END OF WEEK — ready to pay out via /admin/payments")
        lines.append("")

    lines.append("(Rate per poster: KES {})".format(rate))
    lines.append("(Eligibility excludes deleted posters and any poster currently")
    lines.append(" under an open / awaiting-approval revision.)")

    return subj, "\n".join(lines)


def send_daily_summary(db: Session, *, target_day: date) -> bool:
    """
    If email is enabled and configured, send the day's summary.
    Returns True if sent, False if disabled / unconfigured.
    """
    cfg = get_email_config(db)
    if not cfg["enabled"]:
        log.info("Daily email disabled; skipping.")
        return False
    if not cfg["recipient"] or not cfg["smtp_host"]:
        log.info("Daily email enabled but recipient/host missing; skipping.")
        return False
    subj, body = build_daily_summary(db, target_day=target_day)
    _send_smtp(cfg, subj, body)
    log.info("Daily summary email sent: %s", subj)
    return True
