"""
Storing what the marketplaces say, and answering the questions a screen asks.

════════════════════════════════════════════════════════════════════════════
ABSOLUTE ROWS, DELTAS AT READ TIME
════════════════════════════════════════════════════════════════════════════
Every row stored here is a THING THAT HAPPENED — one sale, one payout, one
refund, with its own timestamp and its own amount. Nothing stores "earnings
today" or "+$25 since yesterday".

That is deliberate and it is the single most important rule in this module.
A marketplace's own "This Month" figure resets to zero on the 1st, so a
stored delta would show a large negative every month and the history would be
unreconstructable. Deltas are arithmetic over rows, computed when asked, and
they can be computed for any window because the rows are still there.

The same rule is why a refund is its own row rather than an edit to the sale.
FineArtAmerica's figures are estimates that revise DOWNWARD, and their own
help says a sale can take up to 48 hours to appear. A total that went down is
not a bug and must remain explainable afterwards.

════════════════════════════════════════════════════════════════════════════
WHAT IS AND IS NOT KNOWABLE
════════════════════════════════════════════════════════════════════════════
FineArtAmerica pays on the SHIP date, not the order date, and does not
publish which orders have shipped. So the next payout can only ever be
estimated, and this module says "probably" rather than stating a figure as
fact. Anything here presented as certain is a defect.

════════════════════════════════════════════════════════════════════════════
ADDING A MARKETPLACE
════════════════════════════════════════════════════════════════════════════
Write a reader module with `login()` and something that returns rows, then
add it to READERS below. Nothing outside this file should learn its name —
the screens filter on `marketplace` as data.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import LedgerEntry, MasterTitle, UploadAccount
from . import faa
from .matching import MatchIndex, match_entry

log = logging.getLogger(__name__)


# marketplace -> the module that can read it. `target_site` on the account is
# what selects the entry, so an account for a marketplace with no reader is
# simply skipped rather than being an error.
READERS: dict[str, Any] = {
    "faa": faa,
    "fineartamerica": faa,
}

# How long after a sale FineArtAmerica may still revise it. Used only to
# caveat what is shown, never to hide a row.
REVISION_WINDOW_H = 48


def dec(text: Optional[str]) -> Decimal:
    """Money as arithmetic. Stored as text precisely so this is the only path."""
    if not text:
        return Decimal("0")
    try:
        return Decimal(str(text).replace(",", "").replace("$", "").strip() or "0")
    except Exception:
        return Decimal("0")


def _fmt(amount: Decimal) -> str:
    return f"{amount:.2f}"


# ═══════════════════════════════════════════════════════════════════════════
#  READING
# ═══════════════════════════════════════════════════════════════════════════

def readable_accounts(db: Session) -> list[UploadAccount]:
    """
    Accounts we could read tonight.

    Banned accounts are included on purpose: a banned account may still be
    owed a final payout, and the money is real whether or not the listings
    are. Disabled ones are included too — `is_enabled` governs UPLOADING,
    which is a different capability from earning. An account exists once.
    """
    return [
        a for a in db.query(UploadAccount).order_by(UploadAccount.name).all()
        if (a.target_site or "").lower() in READERS
    ]


def read_account(db: Session, account: UploadAccount,
                 *, on_log: Optional[Callable[[str], None]] = None) -> dict:
    """
    Read one account and store whatever is new.

    Incremental by construction: both readers stop at the first row already
    stored, so a nightly run touches one page and the FIRST run walks to the
    end — which is the backfill, paid once, with no separate import step to
    remember.
    """
    from ..pipeline import decrypt_secret

    say = on_log or (lambda m: log.info("%s: %s", account.name, m))
    marketplace = (account.target_site or "").lower()
    reader = READERS.get(marketplace)
    if reader is None:
        return {"account": account.name, "skipped": "no reader for this marketplace"}

    known = {
        k for (k,) in db.query(LedgerEntry.dedupe_key)
                        .filter(LedgerEntry.account_id == account.id).all()
    }
    is_known = known.__contains__

    session = reader.login(account.email, decrypt_secret(account.password_enc))

    # Sales FIRST. That page carries the artwork name, the product and the
    # order's detail panel inline, so reading it costs one request per page
    # rather than one per order. The ledger is read afterwards for the things
    # only it has: payouts, refunds, and the running balance.
    sales = reader.read_sales(session, is_known=is_known)
    say(f"sales: {len(sales.rows)} new over {sales.pages_read} page(s)")

    ledger = reader.read_ledger(session, is_known=is_known)
    say(f"ledger: {len(ledger.rows)} new over {ledger.pages_read} page(s)")

    index = MatchIndex(db, marketplace)
    stored = matched = 0

    for row in list(sales.rows) + list(ledger.rows):
        if row.dedupe_key in known:
            continue                      # the two pages overlap on sales
        known.add(row.dedupe_key)

        entry = LedgerEntry(
            account_id=account.id,
            marketplace=marketplace,
            occurred_at=row.occurred_at,
            entry_type=row.entry_type,
            remote_order_id=row.order_id,
            description=row.description,
            artwork_name=row.artwork_name,
            product=row.product,
            credit=row.credit,
            debit=row.debit,
            balance_after=row.balance_after,
            dedupe_key=row.dedupe_key,
        )
        detail = getattr(row, "detail", None)
        if detail is not None:
            entry.website = detail.website
            entry.quantity = detail.quantity
            entry.gross_price = detail.gross_price
            entry.discount = detail.discount
            entry.buyer_location = detail.buyer_location
            entry.product = entry.product or detail.product
            entry.details_read = 1

        db.add(entry)
        stored += 1
        if match_entry(db, entry, index):
            matched += 1

    account.last_earnings_read_at = datetime.utcnow()
    db.flush()

    return {
        "account": account.name,
        "marketplace": marketplace,
        "stored": stored,
        "matched": matched,
        "unmatched": stored - matched,
        "balance": ledger.current_balance,
        "pages": sales.pages_read + ledger.pages_read,
    }


def run_nightly(db: Session, *, on_log: Optional[Callable[[str], None]] = None) -> dict:
    """
    Read every readable account.

    One account failing must not stop the others — a wrong password on one
    marketplace is not a reason to have no figures at all. Failures are
    collected and reported rather than raised.
    """
    say = on_log or (lambda m: log.info("%s", m))
    results, errors = [], []

    for account in readable_accounts(db):
        try:
            say(f"reading {account.name}…")
            results.append(read_account(db, account, on_log=say))
            db.commit()
        except Exception as e:
            db.rollback()
            msg = f"{account.name}: {type(e).__name__}: {e}"
            say(msg)
            errors.append(msg)

    from ..pipeline import set_setting
    set_setting(db, "earnings_last_run_at", datetime.utcnow().isoformat(),
                by="scheduler")
    db.commit()

    return {
        "accounts": len(results), "errors": errors,
        "stored": sum(r.get("stored", 0) for r in results),
        "results": results,
    }


def run_daily_if_due(db: Session) -> Optional[dict]:
    """
    The nightly read, once per LOCAL day, called from the scheduler tick.

    Guarded by a date marker rather than by a clock time. The loop ticks
    every minute, so the read happens on the first tick after local midnight
    — and if the server was down at midnight it happens on the first tick
    after it comes back, instead of being silently skipped for a day. Same
    pattern as the auto-backup beside it, for the same reason.
    """
    from ..pipeline import get_setting, set_setting
    from ..timeutil import local_today

    today = local_today().isoformat()
    if str(get_setting(db, "earnings_last_run_day") or "") == today:
        return None

    # Written BEFORE the work, not after. A read that throws must not leave
    # the marker unset, or every tick for the rest of the day retries it —
    # sixty login attempts an hour against a marketplace is how an account
    # gets locked. One attempt a day, and the error is visible on the page.
    set_setting(db, "earnings_last_run_day", today, by="scheduler")
    db.commit()
    return run_nightly(db)


# ═══════════════════════════════════════════════════════════════════════════
#  FIGURES
# ═══════════════════════════════════════════════════════════════════════════

def _filtered(db: Session, *, marketplace: Optional[str] = None,
              account_ids: Optional[list[int]] = None,
              since: Optional[datetime] = None,
              until: Optional[datetime] = None):
    q = db.query(LedgerEntry)
    if marketplace:
        q = q.filter(LedgerEntry.marketplace == marketplace)
    if account_ids:
        q = q.filter(LedgerEntry.account_id.in_(account_ids))
    if since:
        q = q.filter(LedgerEntry.occurred_at >= since)
    if until:
        q = q.filter(LedgerEntry.occurred_at < until)
    return q


def summary(db: Session, *, marketplace: Optional[str] = None,
            account_ids: Optional[list[int]] = None,
            days: int = 30) -> dict:
    """
    The headline numbers, for whatever subset is being looked at.

    Everything is derived from rows in the window, so the same function
    answers "all accounts" and "this one account on TeePublic" without a
    special case.
    """
    now = datetime.utcnow()
    start = now - timedelta(days=days)
    rows = _filtered(db, marketplace=marketplace, account_ids=account_ids,
                     since=start).all()

    sales   = [r for r in rows if r.entry_type == "sale"]
    refunds = [r for r in rows if r.entry_type == "refund"]
    payouts = [r for r in rows if r.entry_type == "payment"]

    earned   = sum((dec(r.credit) for r in sales), Decimal("0"))
    refunded = sum((dec(r.debit) or dec(r.credit) for r in refunds), Decimal("0"))
    paid     = sum((dec(r.debit) for r in payouts), Decimal("0"))

    def window(hours: int) -> dict:
        edge = now - timedelta(hours=hours)
        recent = [r for r in sales if r.occurred_at >= edge]
        return {"sales": len(recent),
                "amount": _fmt(sum((dec(r.credit) for r in recent), Decimal("0")))}

    return {
        "days": days,
        "earned": _fmt(earned),
        "refunded": _fmt(refunded),
        "paid_out": _fmt(paid),
        "net": _fmt(earned - refunded),
        "sales_count": len(sales),
        "today": window(24),
        "week": window(24 * 7),
        "unmatched": sum(1 for r in sales if r.master_title_id is None),
        "revision_window_h": REVISION_WINDOW_H,
    }


def next_payout_estimate(db: Session, *, marketplace: Optional[str] = None,
                         account_ids: Optional[list[int]] = None) -> dict:
    """
    Roughly what the next payout will contain — deliberately not a fact.

    FineArtAmerica pays on the date an order SHIPS, and never publishes which
    orders have shipped. So this is "sales credited since the last payout",
    which is the closest honest answer: it will include things that have not
    shipped yet and may still be revised.

    Presented with the word "probably" everywhere it is shown. If a future
    version can read a ship date, this becomes a fact and the wording should
    change with it — not before.
    """
    q = _filtered(db, marketplace=marketplace, account_ids=account_ids)

    last_payout = (
        q.filter(LedgerEntry.entry_type == "payment")
         .order_by(LedgerEntry.occurred_at.desc()).first()
    )
    since = last_payout.occurred_at if last_payout else None

    credited = _filtered(db, marketplace=marketplace, account_ids=account_ids,
                         since=since)
    sales = credited.filter(LedgerEntry.entry_type == "sale").all()
    refunds = credited.filter(LedgerEntry.entry_type == "refund").all()

    gross = sum((dec(r.credit) for r in sales), Decimal("0"))
    back = sum((dec(r.debit) or dec(r.credit) for r in refunds), Decimal("0"))
    now = datetime.utcnow()
    unsettled = sum(1 for r in sales
                    if (now - r.occurred_at) < timedelta(hours=REVISION_WINDOW_H))

    return {
        "amount": _fmt(gross - back),
        "sales": len(sales),
        "since": since.isoformat() if since else None,
        "last_payout": _fmt(dec(last_payout.debit)) if last_payout else None,
        "unsettled": unsettled,
        "caveat": ("They pay on the date an order ships, which they do not "
                   "publish — so this is what has been credited since your "
                   "last payout, not a promise."),
    }


def by_design(db: Session, *, marketplace: Optional[str] = None,
              account_ids: Optional[list[int]] = None,
              days: int = 365, limit: int = 500,
              sort: str = "amount") -> list[dict]:
    """
    What each design has actually earned, best first.

    Sorted by MONEY by default rather than by count, because two sales of a
    framed print and two of a sticker are not the same result — and deciding
    what to make more of is the only reason this list exists.
    """
    start = datetime.utcnow() - timedelta(days=days)
    rows = (
        _filtered(db, marketplace=marketplace, account_ids=account_ids, since=start)
        .filter(LedgerEntry.entry_type.in_(("sale", "refund")))
        .all()
    )

    buckets: dict[Any, dict] = defaultdict(
        lambda: {"sales": 0, "amount": Decimal("0"), "last": None,
                 "title": None, "title_id": None, "artwork_name": None})

    for r in rows:
        key = r.master_title_id or f"?{r.artwork_name}"
        b = buckets[key]
        if r.entry_type == "sale":
            b["sales"] += 1
            b["amount"] += dec(r.credit)
        else:
            b["amount"] -= (dec(r.debit) or dec(r.credit))
        if b["last"] is None or r.occurred_at > b["last"]:
            b["last"] = r.occurred_at
        b["title_id"] = r.master_title_id
        b["artwork_name"] = b["artwork_name"] or r.artwork_name

    # One query for every title named, rather than one per bucket.
    ids = [b["title_id"] for b in buckets.values() if b["title_id"]]
    titles = {
        t.id: t.title for t in
        db.query(MasterTitle).filter(MasterTitle.id.in_(ids)).all()
    } if ids else {}

    out = [
        {"title_id": b["title_id"],
         "title": titles.get(b["title_id"]) or b["artwork_name"] or "(unknown)",
         "matched": b["title_id"] is not None,
         "sales": b["sales"],
         "amount": _fmt(b["amount"]),
         "last_sold": b["last"].isoformat() if b["last"] else None}
        for b in buckets.values()
    ]

    keys = {
        "amount": lambda r: (Decimal(r["amount"]), r["sales"]),
        "sales":  lambda r: (r["sales"], Decimal(r["amount"])),
        "recent": lambda r: (r["last_sold"] or "",),
    }
    out.sort(key=keys.get(sort, keys["amount"]), reverse=True)
    return out[:limit]


def accounts_overview(db: Session) -> list[dict]:
    """One line per account, for the filter list and the totals strip."""
    totals = dict(
        db.query(LedgerEntry.account_id,
                 func.count(LedgerEntry.id))
          .filter(LedgerEntry.entry_type == "sale")
          .group_by(LedgerEntry.account_id).all()
    )
    out = []
    for a in db.query(UploadAccount).order_by(UploadAccount.name).all():
        marketplace = (a.target_site or "").lower()
        out.append({
            "id": a.id,
            "name": a.name,
            "marketplace": marketplace,
            "readable": marketplace in READERS,
            "banned": bool(a.banned_at),
            "sales": int(totals.get(a.id, 0) or 0),
            "last_read": (a.last_earnings_read_at.isoformat()
                          if getattr(a, "last_earnings_read_at", None) else None),
        })
    return out
