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

import json
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


def queue_reads(db: Session, *, account_id: Optional[int] = None,
                requested_by: str = "scheduler") -> dict:
    """
    Ask the NODE to read the marketplaces. One job per account.

    ════════════════════════════════════════════════════════════════════════
    WHY THE NODE AND NOT THIS SERVER
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica answers this server with "Verify Visitor — Are you human?".
    It does not answer the node that way, because the node arrives in a real
    Chrome carrying the account's own profile — the one that cleared that
    challenge once and still holds the cookie. The uploader has been logging
    into these accounts successfully every day all along; this reuses that
    exact path rather than inventing a second one.

    ════════════════════════════════════════════════════════════════════════
    ONE JOB PER ACCOUNT
    ════════════════════════════════════════════════════════════════════════
    Not one job for all of them. A wrong password, a hung page or a banned
    account must cost you that one account's figures, not the other
    ninety-nine — and each one reports its own outcome to the dashboard
    instead of disappearing into a single pass/fail.

    Queuing is cheap and idempotent: an account that already has a queued
    read does not get a second one, so a node that was off for two days comes
    back to one job per account rather than six.
    """
    from ..pipeline import create_job
    from ..models import PipelineJob

    accounts = readable_accounts(db)
    if account_id:
        accounts = [a for a in accounts if a.id == account_id]

    already = {
        int(json.loads(j.payload_json or "{}").get("account_id") or 0)
        for j in db.query(PipelineJob)
                   .filter(PipelineJob.kind == "earnings_read",
                           PipelineJob.status.in_(("queued", "running"))).all()
    }

    queued, skipped = [], []
    for account in accounts:
        if account.id in already:
            skipped.append(account.name)
            continue
        create_job(db, kind="earnings_read",
                   payload={"account_id": account.id},
                   requested_by=requested_by)
        queued.append(account.name)

    from ..pipeline import set_setting
    set_setting(db, "earnings_last_run_at", datetime.utcnow().isoformat(),
                by=requested_by)
    db.commit()
    return {"queued": queued, "already_queued": skipped,
            "accounts": len(queued)}


def page_urls(db: Session, account: UploadAccount) -> list[dict]:
    """
    The pages the node should fetch, in the order it should fetch them.

    Sales first: that page carries the artwork name, the product and each
    order's detail panel inline, so a sale costs no extra request. The ledger
    follows for the things only it has — payouts, refunds, running balance.
    """
    from ..pipeline import get_setting, project_ids_for_account, resolve_project

    attached = project_ids_for_account(db, account.id)
    project = resolve_project(db, attached[0] if attached else None)
    return [
        {"kind": "sales", "url": get_setting(db, "earnings_sales_url", project=project)},
        {"kind": "ledger", "url": get_setting(db, "earnings_balance_url", project=project)},
    ]


def store_page(db: Session, *, account: UploadAccount, kind: str,
               page: int, url: str, html: str) -> dict:
    """
    Parse one page the node just fetched, store what is new, say whether to
    continue.

    The stop rule lives HERE because it needs the database: rows are
    newest-first, so the first one we already hold means everything below it
    is already stored. The node cannot know that, which is why it asks after
    every page rather than being told a page count up front.

    Returns {"more": bool, "next_url": str|None, "stored": int}.
    """
    marketplace = (account.target_site or "").lower()
    reader = READERS.get(marketplace)
    if reader is None:
        return {"more": False, "next_url": None, "stored": 0,
                "note": "no reader for this marketplace"}

    known = {
        k for (k,) in db.query(LedgerEntry.dedupe_key)
                        .filter(LedgerEntry.account_id == account.id).all()
    }

    if kind == "sales":
        rows, _total = reader.parse_sales_page(html)
    else:
        rows, _balance, _total = reader.parse_balance_page(html)

    index = MatchIndex(db, marketplace)
    stored = matched = 0
    hit_known = False

    for row in rows:
        if row.dedupe_key in known:
            # Newest-first, so this is the end of what is new.
            hit_known = True
            break
        known.add(row.dedupe_key)
        entry = _entry_from_row(account, marketplace, row)
        db.add(entry)
        stored += 1
        if match_entry(db, entry, index):
            matched += 1

    account.last_earnings_read_at = datetime.utcnow()
    # Committed per PAGE, not per run. A node that dies mid-backfill keeps
    # everything it already read, and tomorrow resumes rather than restarts.
    db.commit()

    more = bool(rows) and not hit_known
    base = url.split("?")[0]
    return {
        "more": more,
        "next_url": f"{base}?page={page + 1}" if more else None,
        "stored": stored,
        "matched": matched,
    }


def _entry_from_row(account: UploadAccount, marketplace: str, row) -> LedgerEntry:
    """One parsed row into a stored row. Shared by both read paths."""
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
    return entry


def run_daily_if_due(db: Session) -> Optional[dict]:
    """
    Queue tonight's reads, once per LOCAL day, from the scheduler tick.

    Two guards, and they do different jobs:

      · the CLOCK — not before `earnings_run_at`
      · the DAY MARKER — not twice in one local day

    The marker is set BEFORE the work rather than after. A failure must not
    leave it unset, or every tick for the rest of the day tries again —
    sixty sign-in attempts an hour is how a marketplace account gets locked.
    One attempt a day, and the error is visible on the page.

    It is also what reopens the quiet window: whether the read succeeded or
    failed, the day has been dealt with, so new work starts flowing again
    instead of waiting for a success that may never come.
    """
    from ..pipeline import _parse_hhmm, get_setting, set_setting
    from ..timeutil import local_now, local_today

    today = local_today().isoformat()
    if str(get_setting(db, "earnings_last_run_day") or "") == today:
        return None

    run_at = _parse_hhmm(get_setting(db, "earnings_run_at"))
    if run_at:
        now = local_now()
        if (now.hour, now.minute) < run_at:
            return None

    set_setting(db, "earnings_last_run_day", today, by="scheduler")
    db.commit()
    return queue_reads(db, requested_by="scheduler")


def rearm_today(db: Session, *, by: str = "admin") -> None:
    """
    Forget that tonight's read happened, so the schedule can fire again.

    Purely for testing the timed path: without it you get one attempt a day
    and no way to try the real code again until tomorrow. Re-reading is
    harmless — every row is matched against what is already stored, so a
    second read simply finds nothing new.
    """
    from ..pipeline import set_setting

    set_setting(db, "earnings_last_run_day", "", by=by)
    db.commit()


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
