"""
Dashboard API for listing reconciliation — /admin/listings.

MASTER level, not per project. A listing belongs to an ACCOUNT, and one
FineArtAmerica account carries both niches; showing this inside a project
would hide half of it from the other one.

Everything here is started by hand. There is no schedule and no automatic
mode, by the owner's instruction — the same as the TeePublic tab. He runs it
when he wants to know.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from .. import listing_check as LC
from .. import pipeline as P
from ..audit import log as log_activity
from ..auth import require_admin
from ..db import get_db
from ..models import ListingSweep, PipelineJob, UploadAccount, UploadTracking
from ..templating import templates

router = APIRouter(prefix="/admin", tags=["listing-admin"])

JOB_KIND = "listing_check"
LIVE_JOB = ("queued", "running")


@router.get("/listings", response_class=HTMLResponse)
def listings_page(request: Request, admin=Depends(require_admin),
                  db: Session = Depends(get_db)):
    # `user` is NOT optional and is not the same as `admin`: base.html picks
    # which navigation to draw with `user.role`, and Jinja treats a missing
    # name as falsy rather than raising. Omitting it rendered the page with
    # NO navigation at all — no error, no warning, just a screen you cannot
    # leave. `active_tab` is what highlights the current one.
    return templates.TemplateResponse(
        request, "admin_listings.html",
        {"user": admin, "admin": admin, "active_tab": "listings"},
    )


# ════════════════════════════════════════════════════════════════════════════
#  READING
# ════════════════════════════════════════════════════════════════════════════

@router.get("/api/listings/overview")
def api_overview(admin=Depends(require_admin), db: Session = Depends(get_db)):
    sweep = LC.active(db)
    have, missing = LC.ready(db)

    return JSONResponse({
        "accounts": [{
            "id": a.id,
            "name": a.name,
            "artist_name": a.artist_name or "",
            "ready": bool((a.artist_name or "").strip()),
            # Scoped to THIS marketplace, exactly as `counts()` scopes it.
            # Without the target_site filter this column counted every
            # upload row on the account and the panel above counted only
            # FineArtAmerica ones — so the screen said "19 listings" and
            # "0 can be checked" at the same time, with no way to tell which
            # was lying. Two counts on one screen must be countable the same
            # way or they will eventually disagree.
            "claimed": db.query(UploadTracking).filter_by(
                account_id=a.id, status="uploaded",
                target_site=LC.MARKETPLACE).count(),
        } for a in LC.accounts(db)],
        "counts": LC.counts(db, sweep),
        "sweep": _sweep_payload(db, sweep) if sweep else None,
        "history": [{
            "id": s.id, "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "note": s.note or "",
        } for s in (db.query(ListingSweep)
                      .filter(ListingSweep.status.in_(LC.FINISHED))
                      .order_by(ListingSweep.id.desc()).limit(5).all())],
        "settings": {
            "listing_check_chunk":  int(P.get_setting(db, "listing_check_chunk")),
            "listing_check_gap_ms": int(P.get_setting(db, "listing_check_gap_ms")),
        },
    })


@router.get("/api/listings/findings")
def api_findings(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return JSONResponse(LC.findings(db))


def _sweep_payload(db: Session, sweep: ListingSweep) -> dict:
    return {
        "id": sweep.id,
        "status": sweep.status,
        "note": sweep.note or "",
        "started_at": sweep.started_at.isoformat() if sweep.started_at else None,
        "started_by": sweep.started_by,
        "attempts": sweep.attempts or 0,
        "counts": LC.counts(db, sweep),
        # The guard against a wrong artist name. Surfaced on the RUNNING
        # sweep, not only at the end, so a bad address is caught two minutes
        # in rather than an hour in.
        "suspect": LC.artist_name_suspect(db, sweep),
        "working": bool(_live_jobs(db, sweep)),
    }


def _live_jobs(db: Session, sweep: ListingSweep) -> list[PipelineJob]:
    """Jobs belonging to THIS sweep that are still queued or running."""
    import json

    out = []
    for job in (db.query(PipelineJob)
                  .filter(PipelineJob.kind == JOB_KIND,
                          PipelineJob.status.in_(LIVE_JOB)).all()):
        try:
            payload = json.loads(job.payload_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        if payload.get("sweep_id") == sweep.id:
            out.append(job)
    return out


# ════════════════════════════════════════════════════════════════════════════
#  DOING
# ════════════════════════════════════════════════════════════════════════════

def dispatch_chunk(db: Session, sweep: ListingSweep, *, by: str) -> int:
    """
    Send the next batch of addresses to the worker machine.

    ONE definition, three callers — the START button, the node's own report
    handing over to the next chunk, and the stall sweeper picking up after a
    job that died. A second copy of "how does a chunk go out" is how the
    automatic path and the manual path drift into behaving differently, and
    the automatic one is the path nobody is watching.

    Returns how many addresses went out. Zero means there is nothing left.
    """
    size = int(P.get_setting(db, "listing_check_chunk"))
    items = LC.next_chunk(db, sweep, size)
    if not items:
        return 0

    P.create_job(db, kind=JOB_KIND, payload={
        "sweep_id": sweep.id,
        "marketplace": LC.MARKETPLACE,
        "items": items,
        "gap_ms": int(P.get_setting(db, "listing_check_gap_ms")),
        # How often the node posts back mid-chunk. Also how quickly STOP
        # lands, since the reply to each post is the only way it can hear.
        "report_every": 25,
    }, requested_by=by)
    return len(items)


@router.post("/api/listings/start")
def api_start(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """
    Begin a sweep. Manual only — there is no schedule, by instruction.
    """
    if LC.active(db) is not None:
        raise HTTPException(409, "A sweep is already running.")

    have, missing = LC.ready(db)
    if not have:
        raise HTTPException(
            409, "No account has an artist name on file, so no address can "
                 "be built. Add one to each account first.")

    sweep = ListingSweep(marketplace=LC.MARKETPLACE, started_by=admin.username)
    db.add(sweep)
    db.flush()

    sent = dispatch_chunk(db, sweep, by=admin.username)
    if not sent:
        LC.finish(db, sweep, status="done",
                  note="Nothing to check — no uploaded listings on file.")
        db.commit()
        return JSONResponse({"ok": True, "sweep": sweep.id, "queued": 0})

    total = len(LC.sweepable(db, sweep)) + sent
    sweep.note = f"Checking {total} listing(s), {sent} at a time."
    log_activity(db, user=admin, action="listing_sweep_started",
                 target_type="listing_sweep", target_id=sweep.id,
                 details={"listings": total, "accounts": len(have),
                          "skipped_accounts": [a.name for a in missing]})
    db.commit()
    return JSONResponse({"ok": True, "sweep": sweep.id, "queued": sent,
                         "total": total})


@router.post("/api/listings/stop")
def api_stop(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """
    Stop the sweep, and stop the WORK — not just the screen.

    Cancelling the outstanding job is the important half. Ending a run
    without it is how a stopped TeePublic sweep carried on switching live
    listings off for two hours while the tab read "abandoned". Nothing here
    changes a listing, so the cost is only wasted time, but the shape of the
    mistake is the same and so is the fix.
    """
    sweep = LC.active(db)
    if sweep is None:
        raise HTTPException(404, "No sweep is running.")

    LC.finish(db, sweep, status="abandoned", note="Stopped by hand.")
    killed = 0
    for job in _live_jobs(db, sweep):
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
        P.append_job_log(db, job, "Cancelled — sweep stopped", level="warn")
        killed += 1

    log_activity(db, user=admin, action="listing_sweep_stopped",
                 target_type="listing_sweep", target_id=sweep.id,
                 details={"jobs_cancelled": killed})
    db.commit()
    return JSONResponse({"ok": True, "jobs_cancelled": killed})


@router.post("/api/listings/artist-name")
def api_artist_name(payload: dict = Body(...), admin=Depends(require_admin),
                    db: Session = Depends(get_db)):
    """
    Set the name the marketplace prints on this account's listings.

    Typed rather than derived because it CANNOT be derived: one real
    account's profile is /profiles/elton-odhiambo while its listings live at
    …-golden-reel.html. Copy it from the Artist Name box on any Edit Image
    page, exactly as it appears there.
    """
    account = db.query(UploadAccount).filter_by(
        id=payload.get("account_id")).first()
    if account is None:
        raise HTTPException(404, "No such account.")

    name = (payload.get("artist_name") or "").strip()
    account.artist_name = name or None
    log_activity(db, user=admin, action="listing_artist_name_set",
                 target_type="upload_account", target_id=account.id,
                 details={"artist_name": name})
    db.commit()

    # Show what an address would look like, so a typo is visible before an
    # hour is spent proving it. A wrong name makes every listing read GONE.
    sample = (db.query(UploadTracking)
                .filter_by(account_id=account.id, status="uploaded").first())
    return JSONResponse({
        "ok": True,
        "example": LC.listing_url(sample, name) if (sample and name) else "",
    })


@router.post("/api/listings/explain")
def api_explain(payload: dict = Body(...), admin=Depends(require_admin),
                db: Session = Depends(get_db)):
    """
    Record what a finding MEANS, and correct the database accordingly.

    ════════════════════════════════════════════════════════════════════════
    THE SWEEP NEVER DOES THIS ON ITS OWN
    ════════════════════════════════════════════════════════════════════════
    An observation is written to `listing_status`, beside what we believe.
    Only a person moves `status`, here. That separation is the entire value:
    a sweep that quietly corrected the database would erase the very
    disagreement it was run to find, and the second time you looked there
    would be nothing to see.

    Three answers, and they go to different places:

      taken_down — it really is gone. Mark it removed with the reason, which
                   is what `removed` / `removed_reason` were added for.
      requeue    — the upload was recorded as a success and never happened.
                   Send it back to pending so the pipeline does it properly.
      ignore     — noted and left alone.
    """
    row = db.query(UploadTracking).filter_by(id=payload.get("id")).first()
    if row is None:
        raise HTTPException(404, "No such upload record.")

    answer = payload.get("answer")
    reason = (payload.get("reason") or "").strip()

    if answer == "taken_down":
        row.status = "removed"
        row.removed_at = datetime.utcnow()
        row.removed_reason = reason or "Not on the marketplace when checked."
    elif answer == "requeue":
        # Back to the start of the upload path. `attempts` is reset because
        # the previous attempts were recorded as successes, not failures —
        # leaving them would park it against the retry cap immediately.
        row.status = "pending"
        row.attempts = 0
        row.last_error = None
        row.claimed_at = None
        row.claimed_by = None
        row.removed_at = None
        row.removed_reason = reason or "Recorded as uploaded but not on the site."
    elif answer == "ignore":
        row.removed_reason = reason or "Checked by hand — no action needed."
    else:
        raise HTTPException(400, "Unknown answer.")

    log_activity(db, user=admin, action="listing_finding_explained",
                 target_type="upload_tracking", target_id=row.id,
                 details={"answer": answer, "reason": reason,
                          "title": row.remote_title})
    db.commit()
    return JSONResponse({"ok": True, "status": row.status})


@router.post("/api/listings/settings")
def api_settings(payload: dict = Body(...), admin=Depends(require_admin),
                 db: Session = Depends(get_db)):
    changed = {}
    for key in ("listing_check_chunk", "listing_check_gap_ms"):
        if key in payload:
            P.set_setting(db, key, str(int(payload[key])))
            changed[key] = int(payload[key])
    log_activity(db, user=admin, action="listing_settings_changed",
                 target_type="listing_sweep", target_id=None, details=changed)
    db.commit()
    return JSONResponse({"ok": True, "changed": changed})


# ════════════════════════════════════════════════════════════════════════════
#  THE INVARIANT, WATCHED BY THE CLOCK
# ════════════════════════════════════════════════════════════════════════════

def sweep_stalled(db: Session) -> list[str]:
    """
    A sweep with nothing working on it either carries on or ends. Never sits.

    Same invariant as the TeePublic run, stated about STATE rather than
    about the sequence of events: a sweep that is `running` must have a job
    running. If the worker machine reboots mid-chunk, nothing else would
    ever move it — it would read "checking" for ever and the owner would
    come back in the morning to a screen that had been lying all night.

    Cheaper here than there, because this holds nothing: the cost of a stuck
    sweep is a wrong screen, not an idle pipeline. It still must not happen.
    """
    notes: list[str] = []
    for sweep in (db.query(ListingSweep)
                    .filter(ListingSweep.status == "running").all()):
        if _live_jobs(db, sweep):
            continue

        if not LC.sweepable(db, sweep):
            LC.finish(db, sweep, status="done",
                      note="Finished — the last report was lost, but every "
                           "listing had already been checked.")
            notes.append(f"sweep #{sweep.id}: completed from the data")
            continue

        limit = int(P.get_setting(db, "listing_check_max_attempts"))
        sweep.attempts = (sweep.attempts or 0) + 1
        if sweep.attempts > limit:
            left = len(LC.sweepable(db, sweep))
            LC.finish(db, sweep, status="failed",
                      note=(f"Gave up: the worker machine stopped reporting "
                            f"{sweep.attempts} times with {left} listing(s) "
                            f"still to check."))
            notes.append(f"sweep #{sweep.id}: failed after {sweep.attempts}")
            continue

        sent = dispatch_chunk(db, sweep, by="stall-sweeper")
        notes.append(f"sweep #{sweep.id}: restarted with {sent} address(es) "
                     f"(attempt {sweep.attempts} of {limit})")

    if notes:
        db.commit()
    return notes
