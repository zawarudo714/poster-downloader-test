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
from . import faa, teepublic
from .matching import MatchIndex, match_entry

log = logging.getLogger(__name__)


# marketplace -> the module that can read it. `target_site` on the account is
# what selects the entry, so an account for a marketplace with no reader is
# simply skipped rather than being an error.
READERS: dict[str, Any] = {
    "fineartamerica": faa,
    "teepublic": teepublic,
}

# How long a marketplace may still revise a figure after a sale. Used only to
# caveat what is shown, never to hide anything. Both sites happen to say 48
# hours; it is per-marketplace because the next one will not.
REVISION_WINDOW_H = 48


# ═══════════════════════════════════════════════════════════════════════════
#  WHAT EACH MARKETPLACE ACTUALLY PUBLISHES
# ═══════════════════════════════════════════════════════════════════════════
# The Earnings page is shared by sites that do not offer the same things, and
# the honest answer is not to average over that. FineArtAmerica publishes a
# ledger — every sale, refund and payout as its own row. TeePublic publishes
# four running totals and no list at all.
#
# So capabilities are DECLARED here and the screen reads them, rather than the
# screen assuming FineArtAmerica and quietly showing empty panels for anything
# else. A panel that cannot mean anything for the current selection is not
# shown, and the page says which sites it would have covered — because a
# figure silently missing half your money is worse than no figure.
#
# Adding a marketplace means adding a row here. Nothing on the page is keyed
# on a site's name.
CAPABILITIES: dict[str, dict] = {
    "fineartamerica": {
        "label":       "FineArtAmerica",
        "shape":       "ledger",     # one row per event
        "sales":       True,         # individual sales, with dates
        "per_design":  True,         # which design earned what
        "refunds":     True,
        "payouts":     True,         # payout history as rows
        "balance":     True,         # they state what they owe
        "payout_rule": ("Paid on the 15th, for orders that shipped before the "
                        "15th of the previous month. Ship dates are not "
                        "published, so how much of this arrives next is not "
                        "knowable — only the total owed."),
    },
    "teepublic": {
        "label":       "TeePublic",
        "shape":       "snapshot",   # running totals, read once a day
        "sales":       False,        # no list of individual sales exists
        "per_design":  False,
        "refunds":     False,
        "payouts":     False,
        "balance":     True,
        "payout_rule": ("Paid on the 15th of every month, for the previous "
                        "calendar month, in full. So this figure drops to "
                        "near zero just after the 15th — that is the payment "
                        "landing, not a fault."),
    },
}


def capabilities_for(marketplaces) -> dict:
    """
    What the CURRENT selection can honestly show.

    A capability survives only if every marketplace in view has it. One
    TeePublic account in the mix is enough to remove "per design", because
    the resulting list would silently cover only part of the money.
    """
    sites = [m for m in dict.fromkeys(marketplaces) if m in CAPABILITIES]
    if not sites:
        return {"sites": [], "single": None, "labels": [],
                **{k: False for k in ("sales", "per_design", "refunds",
                                      "payouts", "balance")}}
    caps = {
        key: all(CAPABILITIES[m].get(key) for m in sites)
        for key in ("sales", "per_design", "refunds", "payouts", "balance")
    }
    # Which sites are the REASON a capability is missing — so the page can
    # name them instead of leaving a hole.
    caps["missing_from"] = {
        key: [CAPABILITIES[m]["label"] for m in sites
              if not CAPABILITIES[m].get(key)]
        for key in ("sales", "per_design", "refunds", "payouts")
    }
    caps["sites"] = sites
    caps["labels"] = [CAPABILITIES[m]["label"] for m in sites]
    caps["single"] = sites[0] if len(sites) == 1 else None
    caps["payout_rule"] = (CAPABILITIES[sites[0]]["payout_rule"]
                           if len(sites) == 1 else "")
    return caps


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
    marketplace = (account.target_site or "").lower()
    if CAPABILITIES.get(marketplace, {}).get("shape") == "snapshot":
        # One page, read whole. The URL comes from the account's selectors so
        # it is dashboard-editable like every other marketplace address.
        selectors = dict(get_setting(db, f"selectors_{marketplace}", project=project))
        if account.selectors_json:
            try:
                selectors.update(json.loads(account.selectors_json))
            except (TypeError, ValueError):
                pass
        return [{"kind": "snapshot", "url": selectors.get("control_panel_url")}]

    return [
        {"kind": "sales", "url": get_setting(db, "earnings_sales_url", project=project)},
        {"kind": "ledger", "url": get_setting(db, "earnings_balance_url", project=project)},
    ]


# What each page is the SOURCE for. A page may CONTAIN other kinds of row —
# the ledger lists sales too — but it only gets to say "we have reached rows
# we already hold" about the kinds it owns.
PAGE_OWNS: dict[str, set[str]] = {
    "sales":  {"sale"},
    "ledger": {"payment", "refund"},
}

# What each page LISTS, which is a wider set than what it owns — the ledger
# shows sales as well. Used only to compare our row count against the "of 78"
# the page prints, so the two numbers are counting the same thing.
PAGE_LISTS: dict[str, set[str]] = {
    "sales":  {"sale"},
    "ledger": {"sale", "payment", "refund", "other"},
}


def store_page(db: Session, *, account: UploadAccount, kind: str,
               page: int, url: str, html: str) -> dict:
    """
    Parse one page the node just fetched, store what is new, say whether to
    continue.

    ════════════════════════════════════════════════════════════════════════
    STOP ON WHAT THIS PAGE IS THE SOURCE FOR
    ════════════════════════════════════════════════════════════════════════
    Rows are newest-first, so meeting one we already hold normally means
    everything below it is stored too. That rule is correct only while a page
    is the ONLY source for its rows — and it is not.

    Sales are read from the Sales page first. The ledger then lists those same
    sales, so its very first row is already known, and the read stopped there
    on every single run: eight payouts were never fetched, "paid out" read
    $0.00, and the next-payout figure became every sale ever made ($1,477.21
    against a real balance of $298.28).

    So a known row only ends the read when the page OWNS that kind. The ledger
    skips over sales it has already seen and keeps going until it meets a
    PAYOUT or REFUND it holds. Overlap between pages becomes irrelevant
    instead of fatal — which also means any future page that overlaps another
    needs no special case.

    Returns {"more": bool, "next_url": str|None, "stored": int}.
    """
    marketplace = (account.target_site or "").lower()
    reader = READERS.get(marketplace)
    if reader is None:
        return {"more": False, "next_url": None, "stored": 0,
                "note": "no reader for this marketplace"}

    # A totals-only site has one page and no paging. Routed on the declared
    # SHAPE rather than the site's name, so the next marketplace picks its
    # path from its capability row instead of from an if-statement here.
    if CAPABILITIES.get(marketplace, {}).get("shape") == "snapshot":
        return store_snapshot(db, account=account, html=html)

    known = {
        k for (k,) in db.query(LedgerEntry.dedupe_key)
                        .filter(LedgerEntry.account_id == account.id).all()
    }

    balance = None
    if kind == "sales":
        rows, total = reader.parse_sales_page(html)
    else:
        rows, balance, total = reader.parse_balance_page(html)

    owns = PAGE_OWNS.get(kind, set())
    index = MatchIndex(db, marketplace)
    stored = matched = skipped = upgraded = 0
    hit_own_known = False

    for row in rows:
        if row.dedupe_key in known:
            if row.entry_type in owns:
                hit_own_known = True
                break
            # Someone else's row that we already have — the ledger repeating
            # a sale the Sales page gave us. Step over it and keep looking.
            #
            # But take its TIME on the way past. The Sales page prints only a
            # date, so a sale stored from there sits at midnight; the ledger
            # prints the clock time. Three canvas prints sold at 9:22pm on the
            # 15th were being counted as before that day's 3:22pm payout,
            # which understated "credited since your last payout" by $105.
            # The better value wins, and only ever gets more precise.
            if row.occurred_at.time() != datetime.min.time():
                existing = (
                    db.query(LedgerEntry)
                      .filter(LedgerEntry.account_id == account.id,
                              LedgerEntry.dedupe_key == row.dedupe_key)
                      .first()
                )
                if existing is not None and existing.occurred_at.time() == datetime.min.time():
                    existing.occurred_at = row.occurred_at
                    if row.balance_after and not existing.balance_after:
                        existing.balance_after = row.balance_after
                    upgraded += 1
            skipped += 1
            continue
        known.add(row.dedupe_key)
        entry = _entry_from_row(account, marketplace, row)
        db.add(entry)
        stored += 1
        if match_entry(db, entry, index):
            matched += 1

    if balance:
        # FineArtAmerica stating what it owes. Kept because it is a FACT where
        # everything we compute is a derivation — it is both the honest
        # "next payout" figure and the checksum on our own arithmetic.
        account.marketplace_balance = balance
    account.last_earnings_read_at = datetime.utcnow()
    # Committed per PAGE, not per run. A node that dies mid-backfill keeps
    # everything it already read, and tomorrow resumes rather than restarts.
    db.commit()

    # ── Second stop: their own row count ─────────────────────────────────
    # Every page says "Displaying 1-25 of 78". Compared against how many rows
    # we actually HOLD of the kinds this page lists — not against page number
    # times page size, which undercounts the moment the last page is short and
    # sends the node after a page that does not exist.
    #
    # Also a backstop if the rule above ever fails to fire: a read that cannot
    # end would walk a marketplace all night.
    held = (
        db.query(func.count(LedgerEntry.id))
          .filter(LedgerEntry.account_id == account.id,
                  LedgerEntry.entry_type.in_(PAGE_LISTS.get(kind, ())))
          .scalar() or 0
    )
    at_end = bool(total) and held >= total

    more = bool(rows) and not hit_own_known and not at_end
    base = url.split("?")[0]
    return {
        "more": more,
        "next_url": f"{base}?page={page + 1}" if more else None,
        "stored": stored,
        "matched": matched,
        "skipped_known": skipped,
        "timestamps_upgraded": upgraded,
        "balance": balance,
    }


def store_snapshot(db: Session, *, account: UploadAccount, html: str) -> dict:
    """
    Store one reading of a totals-only marketplace.

    One row per account per LOCAL day: reading twice in an afternoon leaves
    two better readings of one day, not two days. `earned_since` is worked
    out against the previous row and carries how many days it spans, so a
    stretch when the worker machine was off shows as one honest four-day
    figure instead of a false spike on the day it came back.
    """
    from ..timeutil import local_today
    from ..models import MarketplaceSnapshot

    marketplace = (account.target_site or "").lower()
    reader = READERS.get(marketplace)
    snap = reader.parse_account_page(html)
    today = local_today()

    previous = (
        db.query(MarketplaceSnapshot)
          .filter(MarketplaceSnapshot.account_id == account.id,
                  MarketplaceSnapshot.taken_on < today)
          .order_by(MarketplaceSnapshot.taken_on.desc())
          .first()
    )

    row = (
        db.query(MarketplaceSnapshot)
          .filter(MarketplaceSnapshot.account_id == account.id,
                  MarketplaceSnapshot.taken_on == today).first()
    )
    if row is None:
        row = MarketplaceSnapshot(account_id=account.id, marketplace=marketplace,
                                  taken_on=today)
        db.add(row)

    row.marketplace = marketplace
    row.taken_at = datetime.utcnow()
    row.owed = snap.owed
    row.next_payment = snap.next_payment
    row.next_payment_period = snap.next_payment_period
    row.month_to_date = snap.month_to_date
    row.month_to_date_period = snap.month_to_date_period
    row.total_earned = snap.total_earned
    row.items_sold = snap.items_sold

    if previous is not None:
        row.earned_since = _fmt(dec(snap.total_earned) - dec(previous.total_earned))
        row.covers_days = max(1, (today - previous.taken_on).days)
    else:
        # The first ever reading has nothing to subtract from. Their lifetime
        # total is a fact from day one; a daily figure is not, and inventing
        # one would put the whole back catalogue on today's date.
        row.earned_since = None
        row.covers_days = 1

    # The account carries the latest figure too, so "what am I owed" needs no
    # join — the same reason the ledger denormalises the marketplace name.
    account.marketplace_balance = snap.owed
    account.last_earnings_read_at = datetime.utcnow()
    db.commit()

    return {"stored": 1, "owed": snap.owed, "total_earned": snap.total_earned,
            "items_sold": snap.items_sold, "earned_since": row.earned_since,
            "covers_days": row.covers_days, "more": False, "next_url": None}


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


def _snapshot_earnings(db: Session, *, marketplace: Optional[str],
                       account_ids: Optional[list[int]],
                       since_days: int) -> dict:
    """
    Earnings from totals-only accounts, over a window.

    Adds up `earned_since` — each snapshot's climb over the one before it —
    for snapshots inside the window. Two things this deliberately does NOT do:

      * it does not reach back before our first snapshot. TeePublic states a
        lifetime total, but not when any of it happened, so a window that
        starts before we began watching is only partly answerable. The caller
        gets `since` so the screen can say "since we started watching"
        instead of quoting a number that looks complete and is not.
      * it does not spread a multi-day gap evenly. A four-day jump stays one
        four-day figure, flagged, because inventing three days of detail
        would look exactly like data.
    """
    from ..models import MarketplaceSnapshot
    from ..timeutil import local_today

    start = local_today() - timedelta(days=since_days)
    q = db.query(MarketplaceSnapshot).filter(MarketplaceSnapshot.taken_on >= start)
    if marketplace:
        q = q.filter(MarketplaceSnapshot.marketplace == marketplace)
    if account_ids:
        q = q.filter(MarketplaceSnapshot.account_id.in_(account_ids))
    rows = q.order_by(MarketplaceSnapshot.taken_on.asc()).all()

    earned = sum((dec(r.earned_since) for r in rows if r.earned_since), Decimal("0"))
    items = sum((r.items_sold or 0) for r in rows[-1:])   # latest, lifetime
    first = rows[0].taken_on if rows else None
    partial = bool(rows) and first > start
    return {
        "earned": earned,
        "rows": rows,
        "first_seen": first.isoformat() if first else None,
        "partial": partial,
        "gap_days": sum((r.covers_days or 1) - 1 for r in rows),
        "items_lifetime": items,
    }


def summary(db: Session, *, marketplace: Optional[str] = None,
            account_ids: Optional[list[int]] = None,
            days: int = 30) -> dict:
    """
    The headline numbers, for whatever subset is being looked at.

    ════════════════════════════════════════════════════════════════════════
    TWO SHAPES, ONE SET OF FIGURES
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica's money comes from summing sale rows. TeePublic's comes
    from subtracting yesterday's lifetime total from today's. Both are added
    here, so "earned in the last 30 days" spans a mixed selection honestly.

    Refunds and payouts are NOT added across shapes, because only a ledger
    site publishes them. They are reported with the sites they cover so the
    screen can say so rather than showing a total that quietly means "and
    FineArtAmerica only".
    """
    now = datetime.utcnow()
    start = now - timedelta(days=days)
    rows = _filtered(db, marketplace=marketplace, account_ids=account_ids,
                     since=start).all()

    sales   = [r for r in rows if r.entry_type == "sale"]
    refunds = [r for r in rows if r.entry_type == "refund"]
    payouts = [r for r in rows if r.entry_type == "payment"]

    ledger_earned = sum((dec(r.credit) for r in sales), Decimal("0"))
    refunded = sum((dec(r.debit) or dec(r.credit) for r in refunds), Decimal("0"))
    paid     = sum((dec(r.debit) for r in payouts), Decimal("0"))

    snaps = _snapshot_earnings(db, marketplace=marketplace,
                               account_ids=account_ids, since_days=days)

    def window(hours: int) -> dict:
        """Movement over a recent stretch, from both shapes."""
        edge = now - timedelta(hours=hours)
        recent = [r for r in sales if r.occurred_at >= edge]
        amount = sum((dec(r.credit) for r in recent), Decimal("0"))

        # Snapshot sites have no per-sale times, only per-day climbs, so a
        # window is counted in whole local days.
        from ..timeutil import local_today
        edge_day = local_today() - timedelta(days=max(1, hours // 24))
        for snap in snaps["rows"]:
            if snap.taken_on > edge_day and snap.earned_since:
                amount += dec(snap.earned_since)

        return {"sales": len(recent), "amount": _fmt(amount)}

    return {
        "days": days,
        "earned": _fmt(ledger_earned + snaps["earned"]),
        "refunded": _fmt(refunded),
        "paid_out": _fmt(paid),
        "net": _fmt(ledger_earned + snaps["earned"] - refunded),
        "sales_count": len(sales),
        "today": window(24),
        "week": window(24 * 7),
        "unmatched": sum(1 for r in sales if r.master_title_id is None),
        "revision_window_h": REVISION_WINDOW_H,
        # Everything the page needs to be honest about what it cannot know.
        "snapshot_since": snaps["first_seen"],
        "snapshot_partial": snaps["partial"],
        "snapshot_gap_days": snaps["gap_days"],
        "items_lifetime": snaps["items_lifetime"],
    }


def next_payout_estimate(db: Session, *, marketplace: Optional[str] = None,
                         account_ids: Optional[list[int]] = None) -> dict:
    """
    What you are owed, and how much of it is likely to land next.

    ════════════════════════════════════════════════════════════════════════
    THEIR FIGURE IS THE FACT; OURS IS THE ESTIMATE
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica prints `Current Balance` at the top of the ledger. That is
    the marketplace stating what it owes — it is not derived, it cannot drift,
    and it is the honest headline. An earlier version dropped it in favour of
    arithmetic and displayed "probably $1,477.21" against a real balance of
    $298.28, because the payouts had never been read.

    What is still NOT knowable: which of those orders have SHIPPED. FAA pays on
    the ship date and does not publish it, so how much of the balance arrives
    on the 15th cannot be computed — only the total owed can.

    `owed` is theirs. `credited_since_payout` is ours, kept because it answers
    a different question: what has built up since the last time you were paid.
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

    # Their balance, per account, so a filtered view totals only what it shows.
    accounts = db.query(UploadAccount)
    if account_ids:
        accounts = accounts.filter(UploadAccount.id.in_(account_ids))
    if marketplace:
        accounts = accounts.filter(
            func.lower(UploadAccount.target_site) == marketplace)
    balances = [a for a in accounts.all() if a.marketplace_balance]
    owed = sum((dec(a.marketplace_balance) for a in balances), Decimal("0"))

    return {
        "owed": _fmt(owed),
        "owed_known": bool(balances),
        "accounts_reporting": len(balances),
        "credited_since_payout": _fmt(gross - back),
        "sales_since_payout": len(sales),
        "since": since.isoformat() if since else None,
        "last_payout": _fmt(dec(last_payout.debit)) if last_payout else None,
        "unsettled": unsettled,
        "rule": ("They pay on the 15th, for orders that shipped before the "
                 "15th of the previous month. Ship dates are not published, "
                 "so how much of this arrives next is not knowable — only the "
                 "total owed."),
    }


def reconcile(db: Session, *, account_ids: Optional[list[int]] = None) -> list[dict]:
    """
    Does our arithmetic land on the marketplace's own balance?

    Sales minus payouts minus refunds must equal what they say they owe. When
    it does not, we have missed rows — and this is exactly the check that
    would have caught the ledger read stopping on its first line, on the very
    first night, instead of it being spotted by eye a day later.

    Read-only and per account, because a mismatch on one account says nothing
    about the others.

    ════════════════════════════════════════════════════════════════════════
    ONLY A LEDGER MARKETPLACE CAN BE RECONCILED
    ════════════════════════════════════════════════════════════════════════
    The check compares rows WE hold against the balance THEY state. That only
    means something where we hold the events. TeePublic publishes no sales at
    all — we store its balance directly from the page — so there is nothing
    of our own to check it against, and "we hold 0 sales, they say $74" is
    not a discrepancy, it is the design.

    Reported as a scary red box, it was worse than useless: it said the
    totals were wrong when they were right. Skipped by SHAPE rather than by
    marketplace name, so the next snapshot-only site needs no change here.
    """
    accounts = db.query(UploadAccount)
    if account_ids:
        accounts = accounts.filter(UploadAccount.id.in_(account_ids))

    out = []
    for account in accounts.all():
        site = (account.target_site or "").lower()
        if CAPABILITIES.get(site, {}).get("shape") != "ledger":
            continue
        if not account.marketplace_balance:
            continue
        rows = db.query(LedgerEntry).filter(
            LedgerEntry.account_id == account.id).all()
        gross = sum((dec(r.credit) for r in rows if r.entry_type == "sale"),
                    Decimal("0"))
        paid = sum((dec(r.debit) for r in rows if r.entry_type == "payment"),
                   Decimal("0"))
        back = sum((dec(r.debit) or dec(r.credit)
                    for r in rows if r.entry_type == "refund"), Decimal("0"))
        ours = gross - paid - back
        theirs = dec(account.marketplace_balance)
        out.append({
            "account_id": account.id,
            "account": account.name,
            "ours": _fmt(ours),
            "theirs": _fmt(theirs),
            "difference": _fmt(ours - theirs),
            "agrees": ours == theirs,
            "sales": sum(1 for r in rows if r.entry_type == "sale"),
            "payouts": sum(1 for r in rows if r.entry_type == "payment"),
        })
    return out


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


def owed_by_account(db: Session, *, marketplace: Optional[str] = None,
                    account_ids: Optional[list[int]] = None) -> dict:
    """
    What each account says it is owed, grouped by site.

    ════════════════════════════════════════════════════════════════════════
    WHY PER ACCOUNT AND NOT JUST A TOTAL
    ════════════════════════════════════════════════════════════════════════
    With ten accounts the realistic failure is not "the reader broke" — it is
    ONE account quietly failing to sign in while the other nine keep working.
    A single total hides that perfectly: it just looks a bit lower than you
    expected, and there is nothing on screen to say why.

    So every account is listed with when it was last read, and anything not
    read for a couple of days is marked. That is the row that tells you a
    password changed or a session expired.
    """
    from ..timeutil import local_today

    q = db.query(UploadAccount)
    if account_ids:
        q = q.filter(UploadAccount.id.in_(account_ids))
    if marketplace:
        q = q.filter(func.lower(UploadAccount.target_site) == marketplace)

    today = local_today()
    groups: dict[str, dict] = {}
    for account in q.order_by(UploadAccount.name).all():
        site = (account.target_site or "").lower()
        if site not in CAPABILITIES:
            continue
        last = account.last_earnings_read_at
        stale_days = (today - last.date()).days if last else None
        group = groups.setdefault(site, {
            "marketplace": site,
            "label": CAPABILITIES[site]["label"],
            "total": Decimal("0"),
            "accounts": [],
        })
        owed = dec(account.marketplace_balance)
        group["total"] += owed
        group["accounts"].append({
            "id": account.id,
            "name": account.name,
            "owed": _fmt(owed) if account.marketplace_balance else None,
            "last_read": last.isoformat() if last else None,
            # Two days rather than one: a read that happens at 22:00 is
            # nearly a day old by the following evening, and flagging that
            # every day would train you to ignore the flag.
            "stale": stale_days is None or stale_days >= 2,
            "banned": bool(account.banned_at),
        })

    out = sorted(groups.values(), key=lambda g: g["label"])
    for g in out:
        g["accounts"].sort(key=lambda a: dec(a["owed"]), reverse=True)
        g["total"] = _fmt(g["total"])
    return {
        "groups": out,
        "total": _fmt(sum((dec(g["total"]) for g in out), Decimal("0"))),
        "never_read": sum(1 for g in out for a in g["accounts"]
                          if a["last_read"] is None),
        "stale": sum(1 for g in out for a in g["accounts"] if a["stale"]),
    }


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
