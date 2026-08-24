"""
The TeePublic tab — listing health, master level.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS MASTER LEVEL
════════════════════════════════════════════════════════════════════════════
A design belongs to an ACCOUNT, and an account may serve several projects or
none — the nine TeePublic accounts earn passively with nothing uploaded to
them. Scoping this to the active project would hide almost all of it, and
would have to be undone the moment a TeePublic project with uploading
appears. Same reasoning as the Earnings screen, which sits beside it.

════════════════════════════════════════════════════════════════════════════
TWO GATES, AND THEY ARE THE POINT
════════════════════════════════════════════════════════════════════════════
Nothing is deactivated until a person has seen the list and pressed the
button, and nothing is reactivated until a person has seen the result and
pressed it again. Deactivating a live listing costs real money if the scan
misread the site, and a scan runs unattended for hours.

════════════════════════════════════════════════════════════════════════════
THE STORE ADDRESS IS EDITED HERE
════════════════════════════════════════════════════════════════════════════
Not on the Pipeline page's account form, even though that is where the field
technically belongs. This is the screen it is USED on, the screen that stops
working without it, and the screen the owner will be looking at when he
wonders why an account was skipped. A setting you cannot find where you are
looking is a setting you do not have.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from .. import pipeline as P
from ..audit import log as log_activity
from ..auth import require_admin
from ..db import get_db
from ..earnings import store_health as SH
from ..earnings import service as earnings_service
from ..earnings import wall
from ..models import StoreListing, StoreScanRun, UploadAccount, User
from ..templating import templates

router = APIRouter(prefix="/admin", tags=["store-health"])

MARKETPLACE = "teepublic"


@router.get("/store", response_class=HTMLResponse)
def store_page(request: Request, admin: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request, "admin_store.html",
        {"user": admin, "admin": admin, "active_tab": "store"},
    )


@router.get("/api/store/overview")
def api_overview(admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """
    The catalogue, summarised per account, plus whatever run is going.

    Per account rather than one long list, because nine accounts of a
    thousand designs each is not something anyone reads top to bottom. The
    detail for one account is a separate request, made when you open it.
    """
    accounts = SH.accounts_for(db, MARKETPLACE)
    ready, blocked = SH.scannable(accounts)
    run = SH.active_run(db)
    after = int(P.get_setting(db, "scan_vague_after_fixes"))

    rows = db.query(StoreListing).filter(
        StoreListing.marketplace == MARKETPLACE).all()
    by_account: dict[int, list] = {}
    for r in rows:
        by_account.setdefault(r.account_id, []).append(r)

    def summarise(items: list) -> dict:
        live = [r for r in items if r.removed_at is None]
        return {
            "designs":  len(live),
            "visible":  sum(1 for r in live if r.status == "visible"),
            "missing":  sum(1 for r in live if r.status == "missing"),
            "unknown":  sum(1 for r in live if r.status == "unknown"),
            "errors":   sum(1 for r in live if r.status == "error"),
            "excluded": sum(1 for r in live if r.excluded),
            "vague":    sum(1 for r in live if SH.looks_vague(r, after)),
            "removed":  sum(1 for r in items if r.removed_at),
            "checked_at": max(
                (r.last_checked_at for r in live if r.last_checked_at),
                default=None),
        }

    return JSONResponse({
        "accounts": [{
            "id": a.id,
            "name": a.name,
            "store_url": a.profile_url or "",
            "ready": bool((a.profile_url or "").strip()),
            **{k: (v.isoformat() if hasattr(v, "isoformat") else v)
               for k, v in summarise(by_account.get(a.id, [])).items()},
        } for a in accounts],
        "ready": len(ready),
        "blocked": [a.name for a in blocked],
        "totals": SH.counts(db, run, MARKETPLACE),
        # What a CONTINUE would pick up right now, worked out the same way
        # the scan will work it out. The button says the number so you know
        # before pressing whether it is going to do what you expect.
        "continue_left": SH.continue_backlog(db, MARKETPLACE),
        # Live listings we switched off and never put back. Surfaced at the
        # top level rather than inside a run, because the whole point is that
        # it outlives the run that caused it.
        "stranded": len(SH.stranded(db, MARKETPLACE)),
        "continue_within_h": int(P.get_setting(db, "scan_continue_within_h")),
        "run": _run_payload(db, run) if run else None,
        # Recorded mouse paths are needed for the signed-in stages, so the
        # tab says up front if there are none — rather than discovering it
        # three hours into a scan, at the gate.
        "wall_paths": len(wall.paths_for(db, MARKETPLACE)),
        "settings": {
            "scan_parallel_accounts": int(P.get_setting(db, "scan_parallel_accounts")),
            "scan_max_search_pages":  int(P.get_setting(db, "scan_max_search_pages")),
            "scan_limit_per_account": int(P.get_setting(db, "scan_limit_per_account")),
            "scan_vague_after_fixes": after,
            "scan_continue_within_h": int(P.get_setting(db, "scan_continue_within_h")),
        },
        "history": [{
            "id": r.id,
            "status": r.status,
            "mode": r.scan_mode,
            "auto": bool(r.auto),
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "note": r.stage_note or "",
        } for r in (db.query(StoreScanRun)
                      .filter(StoreScanRun.status.in_(SH.FINISHED))
                      .order_by(StoreScanRun.id.desc()).limit(5).all())],
    })


@router.get("/api/store/designs")
def api_designs(account_id: Optional[int] = None,
                status: str = "missing",
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """
    The designs themselves — one account's, or every account's.

    Defaults to MISSING because that is the list worth reading; `status=all`
    is there for when you want to look at everything. Each row carries its
    account name so the all-accounts view can be read without cross-
    referencing anything.
    """
    after = int(P.get_setting(db, "scan_vague_after_fixes"))
    names = {a.id: a.name for a in db.query(UploadAccount).all()}

    q = db.query(StoreListing).filter(
        StoreListing.marketplace == MARKETPLACE,
        StoreListing.removed_at.is_(None))
    if account_id:
        q = q.filter(StoreListing.account_id == account_id)
    if status == "missing":
        q = q.filter(StoreListing.status == "missing")
    elif status in ("visible", "unknown", "error"):
        q = q.filter(StoreListing.status == status)
    elif status == "excluded":
        q = q.filter(StoreListing.excluded == 1)

    rows = q.order_by(StoreListing.account_id,
                      StoreListing.status,
                      StoreListing.title).limit(3000).all()

    return JSONResponse({"designs": [{
        "id": r.id,
        "design_id": r.design_id,
        "account": names.get(r.account_id, f"#{r.account_id}"),
        "account_id": r.account_id,
        "title": r.title or r.design_id,
        "tag": r.search_tag,
        "url": r.url,
        "status": r.status,
        "error": r.status_error,
        "missing_runs": r.consecutive_missing or 0,
        "fix_attempts": r.fix_attempts or 0,
        # The flag that stops a design being cycled forever for nothing.
        "vague": SH.looks_vague(r, after),
        "excluded": bool(r.excluded),
        "exclude_reason": r.exclude_reason,
        "deactivated": bool(r.deactivated_at),
        "action_error": r.action_error,
        "last_checked": (r.last_checked_at.isoformat()
                         if r.last_checked_at else None),
    } for r in rows]})


def _run_payload(db: Session, run: StoreScanRun) -> dict:
    """What the run panel needs. The designs come from /designs separately."""
    return {
        "id": run.id,
        "status": run.status,
        "note": run.stage_note or "",
        "auto": bool(run.auto),
        "mode": run.scan_mode,
        "paused": bool(run.paused_at),
        "paused_by": run.paused_by,
        "retry_at": run.retry_at.isoformat() if run.retry_at else None,
        "retry_count": run.retry_count or 0,
        "stage_jobs_total": run.stage_jobs_total or 0,
        "stage_jobs_done": run.stage_jobs_done or 0,
        "stage_attempts": run.stage_attempts or 0,
        "retry_note": run.retry_note or "",
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "counts": SH.counts(db, run, run.marketplace),
        "waiting": run.status in SH.WAITING,
        # Designs this run tried to switch and could not. They are SKIPPED
        # from here on, so they have to be said out loud — a stage quietly
        # passing over work and then reporting success is how a run claims
        # to have finished something it never did.
        "stuck": [{"title": r.title or r.design_id,
                   "why": (r.action_error or "")[:160]}
                  for r in SH.stuck_for(db, run)[:25]],
        "stuck_total": len(SH.stuck_for(db, run)),
    }


def _queue_scan(db: Session, run: StoreScanRun, *, by: str) -> int:
    """
    Queue the scan job. One definition, so START and RESUME cannot differ.

    The whole scan is ONE job because the accounts inside it run in parallel
    threads, each holding a single browser. The node loop is serial, so nine
    separate jobs would run one after another and a long sweep would become
    very much longer.
    """
    ready, _blocked = SH.scannable(SH.accounts_for(db, MARKETPLACE))
    project = P.resolve_project(db, None)
    attempts = int(P.get_setting(db, "wall_max_attempts"))

    P.create_job(db, kind="store_scan", payload={
        "run_id": run.id,
        "parallel": int(P.get_setting(db, "scan_parallel_accounts")),
        "max_search_pages": int(P.get_setting(db, "scan_max_search_pages")),
        "delay_s": P.get_setting(db, "scan_delay_s"),
        "limit_per_account": int(P.get_setting(db, "scan_limit_per_account")),
        # NOTE: scan_mode is deliberately NOT sent. The node posts the whole
        # store listing and the SERVER answers with the designs worth
        # checking — that is where missing-only and the owner's exclusions
        # are applied. Sending the mode too would be a second place that
        # decides the same thing.
        # The FULL account payload, not a hand-rolled dict. The node builds a
        # browser from it and that browser wants `selectors` and `timings` —
        # a shorter dict passed here died with KeyError: 'selectors' on the
        # first real run. Secrets are excluded: scanning reads public pages
        # and never signs in, so it has no use for a password.
        "accounts": [{
            **P.account_payload(db, a, include_secret=False, project=project),
            "store_url": a.profile_url,
        } for a in ready],
        "settings": P.upload_settings_payload(db, project=project),
        # The wall appears on SEARCH pages as well as the account page, and
        # search pages share none of the account page's labels. The header
        # logo is the marker that covers both — see service.site_markers.
        "wall_html_markers": earnings_service.site_markers(MARKETPLACE),
        "wall_paths": wall.payload_for(
            wall.next_paths(db, MARKETPLACE, attempts)),
        "wall_wait_s": P.get_setting(db, "wall_wait_s"),
        "wall_max_attempts": attempts,
    }, requested_by=by)
    return len(ready)


@router.post("/api/store/start")
def api_start(payload: dict = Body(default={}),
              admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """Begin a sweep — full or missing-only, manual or automatic."""
    try:
        run = SH.start_run(
            db, marketplace=MARKETPLACE, by=admin.username,
            auto=bool(payload.get("auto")),
            scan_mode=str(payload.get("mode") or "full"))
    except ValueError as e:
        raise HTTPException(400, str(e))

    accounts = _queue_scan(db, run, by=admin.username)
    run.scan_started_at = run.started_at
    log_activity(db, user=admin, action="store_scan_started",
                 target_type="store_run", target_id=run.id,
                 details={"accounts": accounts, "auto": bool(run.auto),
                          "mode": run.scan_mode})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id})


@router.post("/api/store/advance")
def api_advance(payload: dict = Body(default={}),
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """Pass a gate: start deactivating, or start reactivating."""
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")

    want = payload.get("stage")
    expected = {"deactivate": "reviewing", "reactivate": "confirming"}.get(want)
    if expected is None:
        raise HTTPException(400, "Unknown stage.")
    if run.status != expected:
        raise HTTPException(409, f"The run is {run.status}, not waiting to "
                                 f"{want}.")

    queued = SH.begin_stage(db, run, want, by=admin.username)
    if not queued:
        # Nothing to do is a legitimate outcome, not an error: a scan can
        # find everything visible. End the run rather than leaving the
        # pipeline held for a stage with no work in it.
        SH.finish_run(db, run, status="done",
                      note="Nothing needed doing — every design was visible.")
        db.commit()
        return JSONResponse({"ok": True, "run": run.id, "queued": 0,
                             "status": run.status})

    log_activity(db, user=admin, action=f"store_{want}_started",
                 target_type="store_run", target_id=run.id,
                 details={"accounts": queued})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id, "queued": queued,
                         "status": run.status})


@router.post("/api/store/pause")
def api_pause(payload: dict = Body(default={}),
              admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """
    Hold the run and give Photoshop and uploads the machine back.

    Not a stop: nothing is lost, nothing is undone, and RESUME picks up from
    the same place. The node hears about it through the reply to its next
    per-design post, so it winds down within one design — in the switching
    stages as well as the scan, which was not true until the action loops
    started reading that reply instead of discarding it.

    Any QUEUED job is cancelled outright. There is nothing for it to carry
    on from and leaving it would mean the node starting a fresh account the
    moment the pause began, which is the opposite of pausing.
    """
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")
    if run.paused_at:
        raise HTTPException(409, "Already paused.")

    SH.pause_run(db, run, by=admin.username)
    for job in SH.jobs_for_run(db, run, statuses=("queued",)):
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
        P.append_job_log(db, job, "Cancelled — run paused", level="warn")

    stranded = len(SH.deactivated_for(db, run))
    log_activity(db, user=admin, action="store_run_paused",
                 target_type="store_run", target_id=run.id,
                 details={"left_off": stranded})
    db.commit()
    return JSONResponse({"ok": True, "left_deactivated": stranded})


@router.post("/api/store/resume")
def api_resume(admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    """
    Carry on from where it stopped.

    A paused run keeps its stage, so resuming a scan re-dispatches the scan
    job and it skips everything already checked; resuming between stages
    just re-opens the gate.
    """
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")
    if not run.paused_at:
        raise HTTPException(409, "Not paused.")

    SH.resume_run(db, run)
    queued = 0

    # ── NEVER ACT ON AN UNFINISHED SCAN ──────────────────────────────────
    #
    # Asked as a question about the DATA, not read from a flag: are there
    # designs this run has not looked at yet? If so the scan is not done,
    # whatever the run's status happens to say — which also repairs a run
    # that an earlier version pushed to the review gate when it was merely
    # paused. Resuming an automatic run from there would have dispatched a
    # mass deactivation based on designs nobody had checked.
    left = SH.scan_incomplete(db, run)
    if left and run.status in ("scanning",) + SH.WAITING:
        run.status = "scanning"
        run.stage_note = f"Carrying on — {left} design(s) still to check."
        queued = _queue_scan(db, run, by=admin.username)
    elif run.status in ("deactivating", "reactivating"):
        # ── RESUMING AN ACTION STAGE HAS TO SEND THE WORK AGAIN ──────────
        #
        # Pausing stops the node mid-account, so there is no job left to
        # carry on by itself. This branch did not exist: resuming a paused
        # deactivation cleared the pause and dispatched nothing, so the run
        # sat in `deactivating` for ever holding Photoshop and the uploads,
        # while the screen showed it running. It was only ever tested from
        # a paused SCAN, where re-dispatch happens above.
        stage = ("deactivate" if run.status == "deactivating"
                 else "reactivate")
        run.stage_attempts = 0
        queued = SH.dispatch_stage(db, run, stage, by=admin.username)
        if not queued:
            SH.advance_after_stage(db, run, stage)
    elif run.auto:
        nxt = SH.next_stage(run)
        if nxt:
            queued = SH.begin_stage(db, run, nxt, by=admin.username)

    log_activity(db, user=admin, action="store_run_resumed",
                 target_type="store_run", target_id=run.id, details={})
    db.commit()
    return JSONResponse({"ok": True, "queued": queued, "status": run.status})


@router.post("/api/store/listing")
def api_listing(payload: dict = Body(...),
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """
    Exclude a design from scanning, or put it back.

    The answer to a vague tag. A design whose primary tag is something like
    "Queen" can never be found inside 25 pages of search, so it reads MISSING
    every sweep and gets cycled forever for nothing. Excluding it stops that;
    it stays in the catalogue and stays counted, it is simply not checked.

    Reversible on purpose — a tag can be edited on the marketplace, and then
    it should be scanned again.
    """
    from ..models import StoreListing

    row = db.query(StoreListing).filter_by(id=int(payload.get("id") or 0)).first()
    if row is None:
        raise HTTPException(404, "No such design.")

    row.excluded = 1 if payload.get("excluded") else 0
    row.exclude_reason = (payload.get("reason") or "").strip() or None
    if not row.excluded:
        # Back in the queue with a clean slate: its history of failures was
        # about a tag we have now presumably fixed, and carrying the old
        # count forward would flag it as vague on the very next sweep.
        row.consecutive_missing = 0
        row.fix_attempts = 0

    log_activity(db, user=admin, action="store_listing_excluded",
                 target_type="store_listing", target_id=row.id,
                 details={"excluded": bool(row.excluded),
                          "title": row.title})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/store/stop-scanning")
def api_stop_scanning(admin: User = Depends(require_admin),
                      db: Session = Depends(get_db)):
    """
    Stop scanning, but KEEP what has been found and go to the gate.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS NOT THE SAME AS ABANDONING
    ════════════════════════════════════════════════════════════════════════
    Abandoning throws the run away. This ends the scan early and moves
    straight to "here is what we found, shall I deactivate it" — which is
    what you want when you have seen enough, or when you are testing the
    later stages and do not want to sit through nine accounts first.

    Everything already checked is kept, because every design was written the
    moment it was checked rather than at the end of the account.

    The node hears about this through the reply to its next per-design post,
    so it stops within one design rather than carrying on to the end.
    """
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")
    if run.status != "scanning":
        raise HTTPException(409, f"The run is {run.status}, not scanning.")

    counts = SH.counts(db, run)
    if not counts["checked"]:
        raise HTTPException(
            409, "Nothing has been checked yet — there would be nothing to "
                 "review. Use STOP THIS RUN instead.")

    run.status = "reviewing"
    run.stage_note = (f"Scan stopped early — {counts['missing']} missing of "
                      f"{counts['checked']} checked.")
    log_activity(db, user=admin, action="store_scan_stopped_early",
                 target_type="store_run", target_id=run.id,
                 details=counts)
    db.commit()
    return JSONResponse({"ok": True, "checked": counts["checked"],
                         "missing": counts["missing"]})


@router.post("/api/store/abandon")
def api_abandon(payload: dict = Body(default={}),
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """
    Stop a run and let the pipeline go.

    Deliberately available at ANY stage. The hold is the expensive part —
    with a Photoshop backlog measured in weeks, a run stuck half-finished
    must never be something only a developer can clear.

    Designs already deactivated are listed in the reply, because abandoning
    between the two stages leaves them off, and that is money until someone
    puts them back.
    """
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")

    stranded = SH.deactivated_for(db, run)
    SH.finish_run(db, run, status="abandoned",
                  note=(payload.get("reason") or "Stopped by hand.")
                       + (f" {len(stranded)} design(s) left deactivated."
                          if stranded else ""))

    # ── STOPPING THE RUN MUST STOP THE WORK ──────────────────────────────
    #
    # It did not. Ending the run set a status and released the pipeline, and
    # the node's queued jobs sat untouched — so it claimed the next account
    # and carried on switching designs off for two more hours while the tab
    # read "abandoned". The screen and the machine disagreed and only the
    # machine was right. A running job also hears about it through the reply
    # to its next per-design report.
    killed = SH.cancel_run_jobs(db, run, why="run stopped by hand")

    log_activity(db, user=admin, action="store_run_abandoned",
                 target_type="store_run", target_id=run.id,
                 details={"left_off": len(stranded), "jobs_cancelled": killed})
    db.commit()
    return JSONResponse({"ok": True, "left_deactivated": len(stranded),
                         "jobs_cancelled": killed})


@router.post("/api/store/account-url")
def api_account_url(payload: dict = Body(...),
                    admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """
    Set an account's public store address.

    Writes `UploadAccount.profile_url`, which already exists and is already
    what the uploader uses to find its way around a marketplace. No new
    column, and nothing to re-create — the nine accounts already on file just
    need the field filled in.
    """
    account = db.query(UploadAccount).filter_by(
        id=int(payload.get("id") or 0)).first()
    if account is None:
        raise HTTPException(404, "No such account.")

    url = (payload.get("store_url") or "").strip()
    if url and "/user/" not in url:
        raise HTTPException(
            400, "That does not look like a store address. It should be like "
                 "https://www.teepublic.com/user/yourname")

    account.profile_url = url or None
    log_activity(db, user=admin, action="store_url_set",
                 target_type="upload_account", target_id=account.id,
                 details={"account": account.name, "url": url})
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/api/store/settings")
def api_settings(payload: dict = Body(...),
                 admin: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """
    The handful of knobs that govern a sweep, edited HERE.

    They are ordinary pipeline settings and could be edited on the Pipeline
    page — but this is the screen where their effect is visible, and a
    setting you cannot find where you are looking is a setting you do not
    have. Written through the same `set_setting`, so there is still exactly
    one stored value behind both screens.
    """
    allowed = {
        "scan_parallel_accounts": (1, 12),
        "scan_max_search_pages":  (1, 200),
        "scan_limit_per_account": (0, 100000),
        "scan_vague_after_fixes": (1, 20),
        # In HOURS, because the natural sentence is "checked in the last N
        # hours". 720 is a month, which is as long as a status is worth
        # trusting at all.
        "scan_continue_within_h": (1, 720),
    }
    changed = {}
    for key, (low, high) in allowed.items():
        if key not in payload:
            continue
        try:
            value = int(payload[key])
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} must be a whole number.")
        if not low <= value <= high:
            raise HTTPException(
                400, f"{key} must be between {low} and {high}.")
        P.set_setting(db, key, value, by=admin.username)
        changed[key] = value

    log_activity(db, user=admin, action="store_settings_changed",
                 target_type="store", target_id=None, details=changed)
    db.commit()
    return JSONResponse({"ok": True, "changed": changed})


def wake_due_retries(db: Session) -> int:
    """
    Re-dispatch any sweep whose waiting time is up. Called by the scheduler.

    Lives here rather than in the service module because it needs to QUEUE A
    JOB, and `_queue_scan` is the one definition of how a scan is queued. A
    second copy that built the payload itself is exactly how the retried
    sweep would end up subtly different from the original — and the retry is
    the one nobody is watching.
    """
    woken = 0
    for run in SH.due_retries(db):
        run.retry_at = None
        if run.status == "scanning":
            _queue_scan(db, run, by="retry")
        elif run.status in ("deactivating", "reactivating"):
            stage = "deactivate" if run.status == "deactivating" else "reactivate"
            SH.dispatch_stage(db, run, stage, by="retry")
        elif run.auto:
            nxt = SH.next_stage(run)
            if nxt:
                SH.begin_stage(db, run, nxt, by="retry")
        run.stage_note = (f"Trying again after waiting "
                          f"(attempt {run.retry_count}).")
        woken += 1
    if woken:
        db.commit()
    return woken


def sweep_stalled_runs(db: Session) -> list[str]:
    """
    Get any stuck run moving again, or end it. Called by the scheduler tick.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS AT ALL
    ════════════════════════════════════════════════════════════════════════
    A run that is switching designs must have one job switching them. If the
    worker machine reboots, or Chrome will not start, or somebody cancels
    the job, nothing will ever move that run again: it sits in `deactivating`
    for ever, holding Photoshop and the daily uploads, while the screen
    politely reports work in progress. That costs a whole night and there is
    no symptom you could see.

    The invariant is stated about STATE — "a run in an action stage has a
    live job" — rather than about the sequence of events, so it holds
    however the job died, including in ways nobody thought of. That is the
    difference between this and a test: a test can only check the ways its
    author imagined.

    Recovery is tried before giving up, because the usual cause is dull and
    the work itself is fine. `repair_stalled` owns that judgement.
    """
    notes: list[str] = []
    for run in SH.stalled_runs(db):
        try:
            notes.append(f"run #{run.id}: {SH.repair_stalled(db, run)}")
        except Exception as e:
            # Repair itself failing must still END the run. Leaving it
            # stalled would hold the pipeline exactly as before, and the
            # sweeper would try again for ever with nothing on screen.
            SH.finish_run(db, run, status="failed",
                          note=f"Could not restart this run: {e}")
            notes.append(f"run #{run.id}: failed to restart — {e}")
    if notes:
        db.commit()
    return notes


@router.post("/api/store/reactivate-all")
def api_reactivate_all(admin: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """
    Switch back on everything we ever switched off and did not restore.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS SEPARATELY FROM A RUN
    ════════════════════════════════════════════════════════════════════════
    A design left switched off is a live listing earning nothing, and it can
    end up that way for reasons that have nothing to do with the run you are
    looking at: a stage that ended early, a run abandoned between the two
    halves of the cure, the node dying mid-account.

    Recovering from that should not require reasoning about WHICH run did it,
    or walking the whole scan/review/deactivate cycle again to reach a
    reactivate stage. The question is simply "what is off?" and the answer
    comes from the catalogue.

    It runs as an ordinary run so it shows its progress, holds the pipeline
    while it works, and ends cleanly — rather than being an invisible
    background action with no way to see how far it got.
    """
    if SH.active_run(db) is not None:
        raise HTTPException(
            409, "A run is already in progress. Wait for it to finish, or "
                 "stop it, then try again.")

    left = SH.stranded(db, MARKETPLACE)
    if not left:
        raise HTTPException(
            404, "Nothing is switched off — there is nothing to put back.")

    run = StoreScanRun(marketplace=MARKETPLACE, status="reactivating",
                       started_by=admin.username, scan_mode="recover",
                       stage_note=f"Putting {len(left)} design(s) back on.")
    db.add(run)
    db.flush()

    queued = SH.begin_stage(db, run, "reactivate", by=admin.username)
    log_activity(db, user=admin, action="store_reactivate_all",
                 target_type="store_run", target_id=run.id,
                 details={"designs": len(left), "accounts": queued})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id,
                         "designs": len(left), "accounts": queued})


@router.post("/api/store/deactivate-missing")
def api_deactivate_missing(admin: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """
    Switch off everything currently marked missing — without a scan first.

    ════════════════════════════════════════════════════════════════════════
    THE MIRROR OF SWITCH BACK ON, AND FOR THE SAME REASON
    ════════════════════════════════════════════════════════════════════════
    "Missing" is a fact recorded on the DESIGN, not on the run that found
    it, so it survives the run being stopped, failing, or being abandoned
    halfway. Requiring a fresh scan to act on facts we already hold would
    mean hours of re-reading the marketplace to learn nothing new.

    It is deliberately the mirror image: reactivation asks "what is switched
    off?", this asks "what is missing?", and both ask the catalogue.

    The usual caution still applies and is not bypassed — vague tags and
    excluded designs are held back by `missing_for`, exactly as they are
    when a scan leads into this stage. The only thing skipped is the
    re-reading, not the judgement.
    """
    if SH.active_run(db) is not None:
        raise HTTPException(
            409, "A run is already in progress. Wait for it to finish, or "
                 "stop it, then try again.")

    run = StoreScanRun(marketplace=MARKETPLACE, status="deactivating",
                       started_by=admin.username, scan_mode="fix")
    db.add(run)
    db.flush()

    todo = SH.stage_work(db, run, "deactivate")
    if not todo:
        db.rollback()
        raise HTTPException(
            404, "Nothing is marked missing — there is nothing to switch off.")

    designs = sum(len(v) for v in todo.values())
    run.stage_note = f"Switching off {designs} design(s) already known missing."
    queued = SH.begin_stage(db, run, "deactivate", by=admin.username)
    log_activity(db, user=admin, action="store_deactivate_missing",
                 target_type="store_run", target_id=run.id,
                 details={"designs": designs, "accounts": len(todo)})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id, "designs": designs,
                         "accounts": len(todo), "queued": queued})
