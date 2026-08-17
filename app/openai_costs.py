"""
Reconciling our spend figures against OpenAI's own billing.

════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
════════════════════════════════════════════════════════════════════════════
Our cost figure is arithmetic WE do: OpenAI returns the token counts for each
image, and we multiply those by prices held as constants in gpt_images.py.

The token counts are measured, so that half is solid. The prices are not —
they are a snapshot of what OpenAI charged on the day the code was written.
If OpenAI changes them, nothing breaks, nothing errors, and our number simply
becomes wrong. The monthly cap then guards a budget that does not exist:
either it stops generation early, or it lets a real overspend sail past.

That is the failure this catches, and it is the only one worth catching here.
Rounding differences and promotional credits are pennies.

════════════════════════════════════════════════════════════════════════════
IT REPORTS, IT NEVER CORRECTS
════════════════════════════════════════════════════════════════════════════
When the two figures disagree, this records the gap and leaves both numbers
alone.

Overwriting ours with theirs would be worse than the problem: OpenAI's costs
endpoint covers the whole ORGANISATION, so it includes anything else the
account is used for, and it lags. Our figure is per-image, immediate, and is
what the cap and the per-image cost are built on. The two answer different
questions and both are worth keeping — what matters is being told when they
stop agreeing.

════════════════════════════════════════════════════════════════════════════
THE ADMIN KEY
════════════════════════════════════════════════════════════════════════════
The costs endpoint needs an ADMIN key (`sk-admin-…`), which is a different,
higher-privilege credential from the one that generates images. It is stored
encrypted like every other secret and is used ONLY here — image generation
never touches it. With no admin key configured this does nothing at all,
quietly, which is the correct behaviour for an optional cross-check.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import requests
from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

COSTS_URL = "https://api.openai.com/v1/organization/costs"
TIMEOUT_S = 30

# Where the last comparison is kept. One settings row rather than a table:
# it is a single small snapshot that is overwritten each night, and a table
# would imply a history nobody would read.
RESULT_KEY = "openai_reconcile_result"
MARKER_KEY = "openai_reconcile_date"

# How far apart the two figures may drift before it is worth saying. Set in
# BOTH absolute and relative terms: 20% of $0.40 is noise, and $5 on $500 is
# noise too. It has to be both to be interesting.
GAP_MIN_USD = Decimal("2.00")
GAP_MIN_RATIO = Decimal("0.15")


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def fetch_month_to_date(admin_key: str) -> Optional[Decimal]:
    """
    What OpenAI says the organisation has spent this month, in USD.

    Returns None when it cannot be determined — no key, a refused key, an
    endpoint that has changed shape. None means "no opinion", which is
    deliberately different from Decimal("0") meaning "they say nothing was
    spent"; reporting a false zero would look exactly like our metering
    having broken.
    """
    if not admin_key:
        return None

    start = int(_month_start().timestamp())
    try:
        resp = requests.get(
            COSTS_URL,
            headers={"Authorization": f"Bearer {admin_key}"},
            params={"start_time": start, "bucket_width": "1d", "limit": 31},
            timeout=TIMEOUT_S,
        )
    except requests.RequestException as e:
        log.warning("OpenAI costs request failed: %s", e)
        return None

    if resp.status_code in (401, 403):
        log.warning("OpenAI rejected the admin key for the costs endpoint "
                    "(HTTP %s). It must be an admin key, not the image key.",
                    resp.status_code)
        return None
    if resp.status_code != 200:
        log.warning("OpenAI costs endpoint returned HTTP %s", resp.status_code)
        return None

    try:
        payload = resp.json()
    except ValueError:
        log.warning("OpenAI costs endpoint did not return JSON")
        return None

    # Shape: {"data": [{"results": [{"amount": {"value": 1.23, ...}}, ...]}]}
    # Walked defensively — this is somebody else's API and a shape change
    # should degrade to "no opinion", never to a wrong number.
    total = Decimal("0")
    found = False
    for bucket in (payload.get("data") or []):
        for result in (bucket.get("results") or []):
            amount = (result or {}).get("amount") or {}
            value = amount.get("value")
            if value is None:
                continue
            try:
                total += Decimal(str(value))
                found = True
            except Exception:
                continue

    return total if found else None


def reconcile(db: Session) -> Optional[dict]:
    """
    Compare the two figures and store the result. Returns it, or None when
    there is nothing to compare.
    """
    from . import gpt_images as G
    from .pipeline import get_secret, set_setting

    admin_key = get_secret(db, "openai_admin_key")
    if not admin_key:
        return None

    theirs = fetch_month_to_date(admin_key)
    if theirs is None:
        return None

    ours = G.month_to_date_usd(db, service="openai")
    gap = theirs - ours
    ratio = (abs(gap) / theirs) if theirs > 0 else Decimal("0")

    result = {
        "checked_at": datetime.utcnow().isoformat(timespec="seconds"),
        "ours": str(ours),
        "theirs": str(theirs),
        "gap": str(gap),
        # Flagged only when the difference is both large enough to matter in
        # absolute terms AND a meaningful share of the bill.
        "significant": bool(abs(gap) >= GAP_MIN_USD and ratio >= GAP_MIN_RATIO),
    }

    import json
    set_setting(db, RESULT_KEY, json.dumps(result), by="reconciler")
    db.commit()

    if result["significant"]:
        log.warning("OpenAI cost mismatch: we metered $%s, OpenAI reports $%s",
                    ours, theirs)
    return result


def last_result(db: Session) -> Optional[dict]:
    """The stored comparison, for the dashboard. None if never run."""
    import json

    from .pipeline import get_setting
    raw = get_setting(db, RESULT_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def run_daily(db: Session) -> None:
    """
    Called once a day by the scheduler.

    Guarded by a date marker rather than a timer, so a restart cannot cause
    it to run repeatedly — and skipping a day is harmless, since the figure
    is month-to-date rather than a delta.
    """
    from .pipeline import get_setting, set_setting
    from .timeutil import local_today

    today = local_today().isoformat()
    if str(get_setting(db, MARKER_KEY) or "") == today:
        return

    result = reconcile(db)
    # The marker is set even when there was nothing to compare, so a missing
    # admin key does not mean retrying the whole thing every minute.
    set_setting(db, MARKER_KEY, today, by="reconciler")
    db.commit()

    if result:
        log.info("OpenAI reconciliation: ours $%s, theirs $%s",
                 result["ours"], result["theirs"])
