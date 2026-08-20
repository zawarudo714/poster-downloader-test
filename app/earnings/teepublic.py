"""
Reading a TeePublic account's earnings page.

════════════════════════════════════════════════════════════════════════════
FOUR NUMBERS, NOT A LEDGER
════════════════════════════════════════════════════════════════════════════
This is the fundamental difference from FineArtAmerica, and everything else
here follows from it. FAA publishes a running ledger — one row per sale, per
refund, per payout — so we store events and add them up.

TeePublic publishes no list of sales at all. The account page shows four
summary figures and nothing else:

    Next Payment · <month> Earnings
    This Month   · <month> Earnings
    Total Earned · Lifetime
    Items Sold   · Lifetime

So a TeePublic account is stored as a daily SNAPSHOT rather than as events,
and "what did I earn on Tuesday" is the difference between Tuesday's snapshot
and Monday's. `Total Earned` only ever goes up, which is what makes those
differences trustworthy.

The per-sale detail does exist, but only in a report TeePublic EMAILS you.
Deliberately not used: reading it would mean handing this app an inbox, for
data wanted monthly at most.

════════════════════════════════════════════════════════════════════════════
WHY THE MONTH LABELS MATTER MORE THAN THE FIGURES
════════════════════════════════════════════════════════════════════════════
`Next Payment` and `This Month` are sometimes the same money and sometimes
not, and you cannot tell by comparing the numbers:

    · After the 15th   both describe the CURRENT month. Same money.
    · 1st to the 15th  Next Payment is LAST month's frozen total, waiting to
                       be paid; This Month has restarted at zero and is
                       counting the new one. Different money.

Add them blindly and you overstate what you are owed by a whole month for
half of every month. So what is owed is decided by the MONTH LABELS printed
beside each figure — their words, not our arithmetic — and the labels are
parsed with as much care as the amounts.

════════════════════════════════════════════════════════════════════════════
SIGNED IN OR NOT
════════════════════════════════════════════════════════════════════════════
TeePublic keeps a session for a long time, and visiting the sign-in page
while already signed in REDIRECTS to the home page. So "am I signed in" is
answered by whether the email field is on the page — the same test the
FineArtAmerica uploader uses, for the same reason: an element that is absent
is a more reliable signal than one we hope is present.

Nothing in this module fetches anything. Every function takes a STRING, so
the pages can be fetched by the node's browser and parsed here — which is
what FineArtAmerica forced on us and what this inherits for free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

log_prefix = "teepublic"


class TeePublicError(Exception):
    """Anything that stops a read. Text is safe to show the admin."""


@dataclass
class Snapshot:
    """
    One reading of the account page. Absolute figures, never deltas.

    Deltas are computed later by comparing two of these, for the same reason
    the FineArtAmerica side stores events: a stored delta cannot survive the
    month boundary that resets `This Month` to zero.
    """
    taken_at: datetime
    next_payment: str = "0"          # money as text — see service.dec()
    next_payment_period: str = ""    # e.g. "Aug Earnings"
    month_to_date: str = "0"
    month_to_date_period: str = ""
    total_earned: str = "0"
    items_sold: int = 0

    @property
    def owed(self) -> str:
        """
        What TeePublic says it has not yet paid you.

        The two figures are added ONLY when their own labels say they cover
        different months. Between the 1st and the 15th that is real money in
        two buckets; after the 15th they are one bucket printed twice, and
        adding them would double it.
        """
        from decimal import Decimal, InvalidOperation

        def dec(text: str) -> Decimal:
            try:
                return Decimal(str(text).replace(",", "").replace("$", "").strip() or "0")
            except (InvalidOperation, TypeError):
                return Decimal("0")

        first, second = dec(self.next_payment), dec(self.month_to_date)
        same_money = (
            self.next_payment_period.strip().lower()
            == self.month_to_date_period.strip().lower()
        )
        total = first if same_money else first + second
        return f"{total:.2f}"


_MONEY = re.compile(r"-?\$?\s*([0-9][0-9,]*\.?[0-9]*)")


def money(text: Optional[str]) -> str:
    """"$4,083.75" -> "4083.75". Blank or unreadable -> "0"."""
    if not text:
        return "0"
    m = _MONEY.search(text)
    return m.group(1).replace(",", "") if m else "0"


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def looks_signed_out(html: str) -> bool:
    """
    Is this the sign-in page rather than the account page?

    Keyed on the email field being PRESENT, because that is what the sign-in
    page uniquely has. Being signed in is inferred from its absence, which is
    exactly what TeePublic's redirect produces.
    """
    return 'name="user[email]"' in html or 'id="user_email"' in html


def login_fields(html: str) -> dict:
    """
    Everything the sign-in form needs, read off the page.

    The hidden `authenticity_token` changes on every visit — it CANNOT be
    stored in settings or guessed, which is precisely why the form is read
    rather than assumed. Same lesson as the FineArtAmerica sign-in endpoint
    that was invented and returned 403 for two deploys.
    """
    soup = _soup(html)
    for form in soup.find_all("form"):
        if not form.find("input", {"type": "password"}):
            continue
        fields = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name and (inp.get("type") or "") not in ("submit", "button"):
                fields[name] = inp.get("value") or ""
        return {"action": form.get("action") or "", "fields": fields}
    return {}


def parse_account_page(html: str) -> Snapshot:
    """
    The four figures, with the month each one describes.

    Read by pairing each label with the value beside it rather than by
    position. TeePublic could reorder the four cards tomorrow; they are far
    less likely to rename them.
    """
    if looks_signed_out(html):
        raise TeePublicError(
            "That is the sign-in page, not the account page — the session was "
            "not accepted.")

    soup = _soup(html)
    snap = Snapshot(taken_at=datetime.utcnow())
    found = 0

    for container in soup.select(".m-account__sales-info-container"):
        parts = [t.strip() for t in container.stripped_strings if t.strip()]
        if len(parts) < 2:
            continue
        label = parts[0].lower()
        period = parts[1] if len(parts) > 2 else ""
        value = parts[-1]

        if label.startswith("next payment"):
            snap.next_payment, snap.next_payment_period = money(value), period
            found += 1
        elif label.startswith("this month"):
            snap.month_to_date, snap.month_to_date_period = money(value), period
            found += 1
        elif label.startswith("total earned"):
            snap.total_earned = money(value)
            found += 1
        elif label.startswith("items sold"):
            digits = re.sub(r"[^0-9]", "", value)
            snap.items_sold = int(digits) if digits else 0
            found += 1

    if found < 3:
        raise TeePublicError(
            "Could not find the earnings figures on the account page — "
            "TeePublic has changed it. Nothing is wrong with your account.")
    return snap


def period_month(label: str, *, today: Optional[date] = None) -> Optional[str]:
    """
    "Aug Earnings" -> "2026-08". Used to tell the two figures apart.

    The year is not printed, so it is inferred: a month AHEAD of the current
    one must belong to last year, which only matters in January when "Dec
    Earnings" is still waiting to be paid.
    """
    today = today or date.today()
    m = re.match(r"\s*([A-Za-z]{3,})", label or "")
    if not m:
        return None
    try:
        month = datetime.strptime(m.group(1)[:3], "%b").month
    except ValueError:
        return None
    year = today.year - 1 if month > today.month else today.year
    return f"{year:04d}-{month:02d}"
