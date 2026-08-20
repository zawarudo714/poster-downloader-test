"""
The Earnings screen — master level, read-only, one tab per question.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS NOT PART OF THE PIPELINE
════════════════════════════════════════════════════════════════════════════
Nothing here dispatches, claims or changes a design. It reads pages on
marketplaces we do not control and reports what they said. That makes it a
sibling of `diagnostics.py`, not of `pipeline_admin.py`, and it is why every
endpoint below is a GET except the three that record a human decision.

════════════════════════════════════════════════════════════════════════════
WHY IT IS MASTER LEVEL AND NOT PER PROJECT
════════════════════════════════════════════════════════════════════════════
A sale does not belong to a project. It belongs to an ACCOUNT, and an
account may serve several projects or none — the nine TeePublic accounts
earn passively with nothing ever uploaded to them. Scoping this screen to
the active project would hide most of the money and would have to be undone
the moment a second marketplace appears.

That also means the filters here are the real interface: marketplace, then
account, then any subset of accounts. They are query parameters rather than
session state, so a filtered view is a URL you can come back to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ..audit import log as log_activity
from ..auth import require_admin
from ..db import get_db
from ..earnings import matching, service
from ..models import LedgerEntry, MasterTitle, UploadAccount, User
from ..templating import templates

router = APIRouter(prefix="/admin", tags=["earnings"])


def _accounts(ids: Optional[str]) -> Optional[list[int]]:
    """
    "3,7,9" -> [3, 7, 9].

    A comma-separated list rather than repeated parameters, because the
    filter is a set of checkboxes and this keeps the URL short enough to
    read and to share.
    """
    if not ids:
        return None
    out = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    return out or None


@router.get("/earnings", response_class=HTMLResponse)
def earnings_page(request: Request, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    from ..pipeline import get_setting

    return templates.TemplateResponse(
        request, "admin_earnings.html",
        {"user": admin, "admin": admin, "active_tab": "earnings",
         "last_run": get_setting(db, "earnings_last_run_at")},
    )


@router.get("/api/earnings/overview")
def api_overview(
    marketplace: Optional[str] = None,
    accounts: Optional[str] = None,
    days: int = Query(30, ge=1, le=3650),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Everything the top of the page needs, in one request."""
    ids = _accounts(accounts)
    return JSONResponse({
        "summary": service.summary(db, marketplace=marketplace,
                                   account_ids=ids, days=days),
        "next_payout": service.next_payout_estimate(
            db, marketplace=marketplace, account_ids=ids),
        # Our arithmetic against the marketplace's own balance. On the page
        # rather than buried in Diagnostics, because a total that does not
        # reconcile is the one thing that makes every other figure suspect.
        "reconcile": service.reconcile(db, account_ids=ids),
        "accounts": service.accounts_overview(db),
        # What each account says it is owed, grouped by site. Always shown:
        # every marketplace publishes something like it, and with ten
        # accounts it is the figure that tells you one of them has stopped.
        "owed_by_account": service.owed_by_account(
            db, marketplace=marketplace, account_ids=ids),
        # What this SELECTION can honestly show. The page reads this instead
        # of assuming FineArtAmerica.
        "capabilities": service.capabilities_for(
            [marketplace] if marketplace
            else [a["marketplace"] for a in service.accounts_overview(db)
                  if not ids or a["id"] in ids]),
        "marketplaces": sorted({
            a["marketplace"] for a in service.accounts_overview(db)
            if a["marketplace"]
        }),
    })


@router.get("/api/earnings/designs")
def api_designs(
    marketplace: Optional[str] = None,
    accounts: Optional[str] = None,
    days: int = Query(365, ge=1, le=3650),
    sort: str = Query("amount", pattern="^(amount|sales|recent)$"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Per-design sales, best first. The list that says what to make more of."""
    return JSONResponse({"designs": service.by_design(
        db, marketplace=marketplace, account_ids=_accounts(accounts),
        days=days, sort=sort)})


@router.get("/api/earnings/entries")
def api_entries(
    marketplace: Optional[str] = None,
    accounts: Optional[str] = None,
    entry_type: Optional[str] = None,
    days: int = Query(90, ge=1, le=3650),
    limit: int = Query(300, ge=1, le=2000),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """The raw ledger — the thing to read when a total looks wrong."""
    from datetime import timedelta

    q = service._filtered(
        db, marketplace=marketplace, account_ids=_accounts(accounts),
        since=datetime.utcnow() - timedelta(days=days))
    if entry_type:
        q = q.filter(LedgerEntry.entry_type == entry_type)

    names = {a.id: a.name for a in db.query(UploadAccount).all()}
    rows = q.order_by(LedgerEntry.occurred_at.desc()).limit(limit).all()

    return JSONResponse({"entries": [{
        "id": r.id,
        "account": names.get(r.account_id, f"#{r.account_id}"),
        "marketplace": r.marketplace,
        "occurred_at": r.occurred_at.isoformat(),
        "type": r.entry_type,
        "order_id": r.remote_order_id,
        "artwork_name": r.artwork_name,
        "product": r.product,
        "website": r.website,
        "quantity": r.quantity,
        "credit": r.credit,
        "debit": r.debit,
        "matched": r.master_title_id is not None,
        "match_method": r.match_method,
    } for r in rows]})


# ═══════════════════════════════════════════════════════════════════════════
#  MATCHING BY HAND
# ═══════════════════════════════════════════════════════════════════════════
#
# The matcher refuses to guess (see matching.py), so whatever it could not
# prove lands here. This is the only part of the screen that writes, and it
# writes an ALIAS rather than editing the sale — which is what makes one
# decision fix every past and future sale of that design at once.

@router.get("/api/earnings/schedule")
def api_schedule(admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """
    The three things the nightly decision looks at, and its answer.

    Put on screen rather than left implicit so the schedule can be TESTED by
    watching it, instead of by waiting until 10pm and guessing. It is also
    what stops the quiet window looking like a fault when uploads go quiet.
    """
    from ..pipeline import get_setting, quiet_window_state

    quiet = quiet_window_state(db)
    return JSONResponse({
        "quiet": quiet,
        "run_at": get_setting(db, "earnings_run_at"),
        "quiet_from": get_setting(db, "earnings_quiet_from"),
        "last_run_at": get_setting(db, "earnings_last_run_at"),
        "max_pages": get_setting(db, "earnings_max_pages_per_run"),
    })


@router.post("/api/earnings/rearm")
def api_rearm(admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """Clear tonight's 'already done' mark so the timed path can fire again."""
    service.rearm_today(db, by=admin.username)
    log_activity(db, user=admin, action="earnings_rearmed",
                 target_type="earnings", target_id=None, details={})
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/earnings/unmatched")
def api_unmatched(admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    """Grouped by product name: ten sales of one design are ONE decision."""
    return JSONResponse({"unmatched": matching.unmatched_summary(db)})


@router.get("/api/earnings/title-search")
def api_title_search(
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Find the design a sale belongs to, to attach an alias to it."""
    term = (q or "").strip()
    if len(term) < 2:
        return JSONResponse({"titles": []})

    rows = (
        db.query(MasterTitle)
          .filter(MasterTitle.title.ilike(f"%{term}%"))
          .order_by(MasterTitle.external_id.asc().nullslast())
          .limit(limit).all()
    )
    return JSONResponse({"titles": [
        {"id": t.id, "title": t.title, "external_id": t.external_id,
         "year": t.year} for t in rows
    ]})


@router.post("/api/earnings/match")
def api_match(
    payload: dict = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Attach a listing name to one of our designs, forever.

    Returns how many previously unmatched sales it resolved — the useful
    feedback, because a design that has sold ten times is fixed ten times by
    one click.
    """
    marketplace = (payload.get("marketplace") or "").strip().lower()
    artwork_name = (payload.get("artwork_name") or "").strip()
    title_id = payload.get("master_title_id")

    if not marketplace or not artwork_name or not title_id:
        raise HTTPException(400, "Marketplace, artwork name and a title are required.")
    if db.query(MasterTitle).filter_by(id=int(title_id)).first() is None:
        raise HTTPException(404, "That title no longer exists.")

    fixed = matching.add_alias(db, marketplace=marketplace,
                               artwork_name=artwork_name,
                               master_title_id=int(title_id), by=admin.username)
    log_activity(db, user=admin, action="earnings_alias_added",
                 target_type="master_title", target_id=int(title_id),
                 details={"artwork_name": artwork_name,
                          "marketplace": marketplace, "resolved": fixed})
    db.commit()
    return JSONResponse({"ok": True, "resolved": fixed})


# ═══════════════════════════════════════════════════════════════════════════
#  READING NOW
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/api/earnings/read")
def api_read_now(
    payload: dict = Body(default={}),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Ask the node to read now — one account, or all of them.

    This queues exactly what the nightly schedule queues. There is no
    separate "manual" mode, so pressing this proves the scheduled run works
    rather than proving something next to it.

    It returns as soon as the work is QUEUED, not when it is done: the read
    happens in the node's browser, and you watch it in the live console. If
    the node is off, the job waits — visibly — instead of silently returning
    nothing.
    """
    account_id = payload.get("account_id")
    if account_id:
        account = db.query(UploadAccount).filter_by(id=int(account_id)).first()
        if account is None:
            raise HTTPException(404, "Account not found.")

    outcome = service.queue_reads(
        db, account_id=int(account_id) if account_id else None,
        requested_by=admin.username)
    log_activity(db, user=admin, action="earnings_read_queued",
                 target_type="earnings", target_id=None,
                 details={"queued": outcome["queued"]})
    db.commit()
    return JSONResponse({"ok": True, "result": outcome})
