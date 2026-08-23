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
from ..models import StoreDesign, StoreScanRun, UploadAccount, User
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
    """Everything the tab needs, in one request."""
    accounts = SH.accounts_for(db, MARKETPLACE)
    ready, blocked = SH.scannable(accounts)
    run = SH.active_run(db)

    previous = (db.query(StoreScanRun)
                  .filter(StoreScanRun.status.in_(SH.FINISHED))
                  .order_by(StoreScanRun.id.desc()).limit(5).all())

    return JSONResponse({
        "accounts": [{
            "id": a.id,
            "name": a.name,
            "store_url": a.profile_url or "",
            "ready": bool((a.profile_url or "").strip()),
        } for a in accounts],
        "ready": len(ready),
        "blocked": [a.name for a in blocked],
        "run": _run_payload(db, run) if run else None,
        # Recorded mouse paths are needed for the signed-in stages, so the
        # tab says up front if there are none — rather than discovering it
        # three hours into a scan, at the gate.
        "wall_paths": len(wall.paths_for(db, MARKETPLACE)),
        "history": [{
            "id": r.id,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "note": r.stage_note or "",
        } for r in previous],
    })


def _run_payload(db: Session, run: StoreScanRun) -> dict:
    counts = SH.counts(db, run)
    names = {a.id: a.name for a in db.query(UploadAccount).all()}

    rows = (db.query(StoreDesign)
              .filter(StoreDesign.run_id == run.id)
              .order_by(StoreDesign.status.desc(), StoreDesign.account_id,
                        StoreDesign.title)
              .all())

    return {
        "id": run.id,
        "status": run.status,
        "note": run.stage_note or "",
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "counts": counts,
        "waiting": run.status in SH.WAITING,
        # Missing first — that is the list being decided on. Everything else
        # is context, and a few thousand visible rows would bury it.
        "designs": [{
            "design_id": r.design_id,
            "account": names.get(r.account_id, f"#{r.account_id}"),
            "title": r.title or r.design_id,
            "url": r.url,
            "status": r.status,
            "error": r.error,
            "deactivated": bool(r.deactivated_at),
            "reactivated": bool(r.reactivated_at),
            "action_error": r.action_error,
        } for r in rows if r.status != "visible"][:2000],
        "visible_sample": sum(1 for r in rows if r.status == "visible"),
    }


@router.post("/api/store/start")
def api_start(admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    """
    Begin a sweep, and hand the whole scan to the node as ONE job.

    One job rather than one per account because the accounts are scanned in
    parallel THREADS inside it, each holding a single browser open. The node
    loop is serial, so nine separate jobs would run one after another and a
    ten-hour scan would become considerably longer.
    """
    try:
        run = SH.start_run(db, marketplace=MARKETPLACE, by=admin.username)
    except ValueError as e:
        raise HTTPException(400, str(e))

    ready, _blocked = SH.scannable(SH.accounts_for(db, MARKETPLACE))
    project = P.resolve_project(db, None)

    P.create_job(db, kind="store_scan", payload={
        "run_id": run.id,
        "parallel": int(P.get_setting(db, "scan_parallel_accounts")),
        "max_search_pages": int(P.get_setting(db, "scan_max_search_pages")),
        "delay_s": P.get_setting(db, "scan_delay_s"),
        "accounts": [{
            "id": a.id,
            "name": a.name,
            "store_url": a.profile_url,
            "settings": P.upload_settings_payload(db, project=project),
        } for a in ready],
    }, requested_by=admin.username)

    run.scan_started_at = run.started_at
    log_activity(db, user=admin, action="store_scan_started",
                 target_type="store_run", target_id=run.id,
                 details={"accounts": len(ready)})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id})


@router.post("/api/store/advance")
def api_advance(payload: dict = Body(default={}),
                admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    """
    Pass a gate: start deactivating, or start reactivating.

    ════════════════════════════════════════════════════════════════════════
    ONE JOB PER ACCOUNT HERE, UNLIKE THE SCAN
    ════════════════════════════════════════════════════════════════════════
    These stages are signed in, and each account needs its OWN Chrome profile
    — two accounts cannot share one browser. Serial is also fine: this stage
    is minutes, not hours.
    """
    run = SH.active_run(db, MARKETPLACE)
    if run is None:
        raise HTTPException(404, "No run in progress.")

    want = payload.get("stage")
    if want == "deactivate" and run.status != "reviewing":
        raise HTTPException(409, f"The run is {run.status}, not waiting to "
                                 f"deactivate.")
    if want == "reactivate" and run.status != "confirming":
        raise HTTPException(409, f"The run is {run.status}, not waiting to "
                                 f"reactivate.")

    picker = SH.missing_for if want == "deactivate" else SH.deactivated_for
    rows = picker(db, run)
    if not rows:
        # Nothing to do is a legitimate outcome, not an error: a scan can
        # find everything visible. End the run rather than leaving the
        # pipeline held for a stage with no work in it.
        SH.finish_run(db, run, status="done",
                      note="Nothing needed doing — every design was visible.")
        db.commit()
        return JSONResponse({"ok": True, "run": run.id, "queued": 0,
                             "status": run.status})

    by_account: dict[int, list] = {}
    for row in rows:
        by_account.setdefault(row.account_id, []).append(row)

    project = P.resolve_project(db, None)
    attempts = int(P.get_setting(db, "wall_max_attempts"))
    queued = 0

    for account_id, designs in by_account.items():
        account = db.query(UploadAccount).filter_by(id=account_id).first()
        if account is None:
            continue
        P.create_job(db, kind=f"store_{want}", payload={
            "run_id": run.id,
            "action": want,
            "account": P.account_payload(db, account, include_secret=True,
                                         project=project),
            "settings": P.upload_settings_payload(db, project=project),
            "designs": [{"design_id": d.design_id, "title": d.title,
                         "url": d.url} for d in designs],
            # The same wall that stands in front of the earnings page. These
            # stages are signed in, so it can appear here too.
            "wall_markers": earnings_service.page_markers(MARKETPLACE),
            "signed_out_markers": earnings_service.signed_out_markers(MARKETPLACE),
            "wall_paths": wall.payload_for(
                wall.next_paths(db, MARKETPLACE, attempts)),
            "wall_wait_s": P.get_setting(db, "wall_wait_s"),
            "wall_max_attempts": attempts,
        }, requested_by=admin.username)
        queued += 1

    run.status = "deactivating" if want == "deactivate" else "reactivating"
    run.stage_note = f"{len(rows)} design(s) across {queued} account(s)."
    log_activity(db, user=admin, action=f"store_{want}_started",
                 target_type="store_run", target_id=run.id,
                 details={"designs": len(rows)})
    db.commit()
    return JSONResponse({"ok": True, "run": run.id, "queued": queued,
                         "designs": len(rows), "status": run.status})


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
    log_activity(db, user=admin, action="store_run_abandoned",
                 target_type="store_run", target_id=run.id,
                 details={"left_off": len(stranded)})
    db.commit()
    return JSONResponse({"ok": True, "left_deactivated": len(stranded)})


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
