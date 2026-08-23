"""
Marketplace listing health — are our designs actually findable?

════════════════════════════════════════════════════════════════════════════
THE PROBLEM THIS SOLVES
════════════════════════════════════════════════════════════════════════════
TeePublic designs quietly stop appearing in search. The listing is still
there, the design page still loads, but nobody can find it — so it earns
nothing and there is no notification, no email, and nothing on the account
page to say so. Left alone it is money leaking out of a catalogue of
thousands, invisibly.

The fix the owner found by hand: deactivate the design, then reactivate it.
That usually puts it back in the index.

════════════════════════════════════════════════════════════════════════════
SIBLING OF diagnostics.py AND earnings/, NOT OF THE PIPELINE
════════════════════════════════════════════════════════════════════════════
Nothing here processes, uploads or changes a design of ours. It reads a
marketplace we do not control, reports what it found, and — on an explicit
human instruction — toggles listings off and on again. That makes it a
read-mostly reporting tool with two deliberate actions, exactly like the
reconciliation work described in the project brief.

It is MASTER level, not per project. A design belongs to an ACCOUNT, and an
account may serve several projects or none.

════════════════════════════════════════════════════════════════════════════
WHY IT STOPS EVERYTHING ELSE
════════════════════════════════════════════════════════════════════════════
A scan is hours of browser work across nine accounts on a machine that is
also running Photoshop and uploads. Rather than compete, a run HOLDS the
pipeline — and the hold is a property of the run, not a switch someone has
to remember to turn back. A run that finishes, fails or is abandoned
releases it on the way out. There is no second edge to lose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models import StoreDesign, StoreScanRun, UploadAccount


# Marketplaces this tool understands. A closed list, like MARKETPLACES: an
# account for anything else simply has no health tab rather than a broken one.
SUPPORTED = ("teepublic",)

# Stages where the run is waiting for a PERSON. Named once so the screen, the
# dispatcher and the hold cannot disagree about what "waiting" means.
WAITING = ("reviewing", "confirming")

# Stages where the run is over, however it ended.
FINISHED = ("done", "failed", "abandoned")


def active_run(db: Session, marketplace: Optional[str] = None) -> Optional[StoreScanRun]:
    """
    The run currently holding the pipeline, if any.

    One at a time, across all marketplaces. Two runs would mean two sets of
    browsers on one node plus two holds, and the second to finish would
    release work the first still wanted stopped.
    """
    q = db.query(StoreScanRun).filter(~StoreScanRun.status.in_(FINISHED))
    if marketplace:
        q = q.filter(StoreScanRun.marketplace == marketplace.lower())
    return q.order_by(StoreScanRun.id.desc()).first()


def holds_pipeline(db: Session) -> Optional[str]:
    """
    Why work is stopped right now, in words the owner can act on — or None.

    Returns a SENTENCE rather than a boolean because work stopping with no
    visible reason is indistinguishable from a fault, and that has cost this
    project an evening before. `intake_open` asks this; the dashboard prints
    the same string.
    """
    run = active_run(db)
    if run is None:
        return None

    done = db.query(StoreDesign).filter(
        StoreDesign.run_id == run.id,
        StoreDesign.status != "pending").count()
    total = db.query(StoreDesign).filter(StoreDesign.run_id == run.id).count()

    label = run.marketplace.title()
    if run.status == "scanning":
        seen = f"{done} of {total}" if total else "starting up"
        return (f"Paused for the {label} visibility scan ({seen} designs "
                f"checked). Work resumes when the run finishes.")
    if run.status in WAITING:
        return (f"Paused — the {label} run is waiting for you on the "
                f"{label} tab.")
    return f"Paused for the {label} run ({run.status})."


# ════════════════════════════════════════════════════════════════════════════
#  THE RUN
# ════════════════════════════════════════════════════════════════════════════

def accounts_for(db: Session, marketplace: str) -> list[UploadAccount]:
    """
    Accounts we can scan: right marketplace, and a store address on file.

    An account with no `store_url` is REPORTED rather than skipped silently —
    a missing address is why nine accounts would become eight without anyone
    noticing which one dropped out.
    """
    return [
        a for a in db.query(UploadAccount).order_by(UploadAccount.name).all()
        if (a.target_site or "").lower() == marketplace.lower()
    ]


def scannable(accounts: list[UploadAccount]) -> tuple[list, list]:
    """(ready, missing an address) — so the screen can name the second list."""
    ready = [a for a in accounts if (a.profile_url or "").strip()]
    blocked = [a for a in accounts if not (a.profile_url or "").strip()]
    return ready, blocked


def start_run(db: Session, *, marketplace: str, by: str) -> StoreScanRun:
    """Begin a sweep. Refuses if one is already going."""
    if active_run(db) is not None:
        raise ValueError("A run is already in progress.")

    ready, _blocked = scannable(accounts_for(db, marketplace))
    if not ready:
        raise ValueError(
            "No accounts have a store address yet. Add one to each account "
            "on this tab first — it looks like "
            "https://www.teepublic.com/user/yourname")

    run = StoreScanRun(marketplace=marketplace.lower(), status="scanning",
                       started_by=by)
    db.add(run)
    db.flush()
    return run


def finish_run(db: Session, run: StoreScanRun, *, status: str,
               note: Optional[str] = None) -> None:
    """
    End a run, however it ended — and RELEASE THE HOLD by doing so.

    Every exit routes through here for that reason. A stage that ended the
    run by setting the status directly would leave the pipeline stopped with
    nothing to say why, which is the exact shape the quiet window was
    redesigned to make impossible.
    """
    run.status = status
    run.stage_note = note
    run.finished_at = datetime.utcnow()


def record_design(db: Session, *, run: StoreScanRun, account_id: int,
                  design_id: str, url: str, title: Optional[str],
                  search_tag: Optional[str], status: str,
                  error: Optional[str] = None) -> StoreDesign:
    """
    One design's verdict. Idempotent on (run, design id).

    Written per design rather than per account, so a node that dies four
    hours into a scan keeps everything it had already checked and the run
    carries on rather than starting again.
    """
    row = db.query(StoreDesign).filter_by(
        run_id=run.id, design_id=str(design_id)).first()
    if row is None:
        row = StoreDesign(run_id=run.id, account_id=account_id,
                          design_id=str(design_id))
        db.add(row)

    row.url = url or row.url
    row.title = title or row.title
    row.search_tag = search_tag or row.search_tag
    row.status = status
    row.error = error
    row.checked_at = datetime.utcnow()
    return row


def missing_for(db: Session, run: StoreScanRun,
                account_id: Optional[int] = None) -> list[StoreDesign]:
    """The designs a run found missing. What stage 3 acts on."""
    q = db.query(StoreDesign).filter(StoreDesign.run_id == run.id,
                                     StoreDesign.status == "missing")
    if account_id:
        q = q.filter(StoreDesign.account_id == account_id)
    return q.order_by(StoreDesign.account_id, StoreDesign.title).all()


def deactivated_for(db: Session, run: StoreScanRun,
                    account_id: Optional[int] = None) -> list[StoreDesign]:
    """
    Exactly what WE turned off, and nothing else. What stage 5 acts on.

    This is the whole reason a run exists as a record. The previous tool
    reactivated by opening the marketplace's inactive list and republishing
    the first N it found — which on one real account would have republished
    379 designs the owner had deactivated himself, deliberately, over months.
    There is no way to tell those apart from the outside. There is no need
    to: we know which ones we touched.
    """
    q = db.query(StoreDesign).filter(StoreDesign.run_id == run.id,
                                     StoreDesign.deactivated_at.isnot(None),
                                     StoreDesign.reactivated_at.is_(None))
    if account_id:
        q = q.filter(StoreDesign.account_id == account_id)
    return q.order_by(StoreDesign.account_id, StoreDesign.title).all()


def counts(db: Session, run: StoreScanRun) -> dict:
    """Totals for the screen and for deciding when a stage is over."""
    rows = db.query(StoreDesign).filter(StoreDesign.run_id == run.id).all()
    return {
        "total": len(rows),
        "checked": sum(1 for r in rows if r.status != "pending"),
        "visible": sum(1 for r in rows if r.status == "visible"),
        "missing": sum(1 for r in rows if r.status == "missing"),
        "under_review": sum(1 for r in rows if r.status == "under_review"),
        "errors": sum(1 for r in rows if r.status == "error"),
        "deactivated": sum(1 for r in rows if r.deactivated_at),
        "reactivated": sum(1 for r in rows if r.reactivated_at),
        "action_errors": sum(1 for r in rows if r.action_error),
    }
