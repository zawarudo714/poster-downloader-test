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

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import StoreListing, StoreScanRun, UploadAccount


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

    label = run.marketplace.title()

    # ── A PAUSED RUN DOES NOT HOLD ANYTHING ──────────────────────────────
    #
    # That is the entire reason pause exists as something separate from
    # stop: you pause precisely because you want Photoshop and the daily
    # uploads to have the machine back for a while. A pause that kept the
    # hold would be an unhelpful stop with extra steps.
    if run.paused_at:
        return None

    # ── NOR DOES ONE WAITING OUT A WALL ──────────────────────────────────
    #
    # Same reasoning as a pause. Holding Photoshop and the uploads for an
    # hour and a half while we wait for a marketplace to stop having a
    # moment would cost far more than the scan is worth.
    if run.retry_at and run.retry_at > datetime.utcnow():
        return None

    tally = counts(db, run, run.marketplace)
    if run.status == "scanning":
        seen = (f"{tally['checked']} of {tally['run_total']}"
                if tally["run_total"] else "starting up")
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


def start_run(db: Session, *, marketplace: str, by: str,
              auto: bool = False, scan_mode: str = "full") -> StoreScanRun:
    """
    Begin a sweep. Refuses if one is already going.

    `auto` hands each stage straight to the next with no button in between.
    `scan_mode="missing_only"` rechecks just the designs currently missing,
    which is what you want after a cure rather than re-reading everything.
    """
    if active_run(db) is not None:
        raise ValueError("A run is already in progress.")

    ready, _blocked = scannable(accounts_for(db, marketplace))
    if not ready:
        raise ValueError(
            "No accounts have a store address yet. Add one to each account "
            "on this tab first — it looks like "
            "https://www.teepublic.com/user/yourname")

    if scan_mode == "missing_only":
        waiting = db.query(StoreListing).filter(
            StoreListing.marketplace == marketplace.lower(),
            StoreListing.status == "missing",
            StoreListing.removed_at.is_(None),
            StoreListing.excluded == 0).count()
        if not waiting:
            raise ValueError(
                "Nothing is currently marked missing, so there is nothing to "
                "recheck. Run a full sweep instead.")

    run = StoreScanRun(marketplace=marketplace.lower(), status="scanning",
                       started_by=by, auto=1 if auto else 0,
                       scan_mode=scan_mode)
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


def sync_catalogue(db: Session, *, account: UploadAccount,
                   marketplace: str, seen: list[dict]) -> dict:
    """
    Reconcile what the store shows against what we already knew.

    ════════════════════════════════════════════════════════════════════════
    THIS IS WHAT MAKES THE CATALOGUE WORTH KEEPING
    ════════════════════════════════════════════════════════════════════════
    Three answers come out of one comparison:

      · NEW      — in the store, not in our catalogue. Added since last time.
      · STILL    — in both. Its `last_seen_at` moves forward.
      · REMOVED  — in our catalogue, not in the store. Deleted at the
                   marketplace, or deactivated by hand outside this tool.

    A removed design keeps its row and gets `removed_at` — never deleted,
    because "this account lost eleven designs last month" is a question you
    can only answer if the rows survive.

    A design that comes BACK clears `removed_at`, which is how a design we
    deactivated and reactivated re-enters the catalogue cleanly rather than
    appearing to be brand new.
    """
    now = datetime.utcnow()
    by_id = {str(d["design_id"]): d for d in seen}

    existing = {
        r.design_id: r for r in
        db.query(StoreListing).filter(StoreListing.account_id == account.id).all()
    }

    added = returned = removed = 0
    for design_id, data in by_id.items():
        row = existing.get(design_id)
        if row is None:
            row = StoreListing(
                account_id=account.id, marketplace=marketplace.lower(),
                design_id=design_id, first_seen_at=now)
            db.add(row)
            added += 1
        elif row.removed_at:
            row.removed_at = None
            returned += 1
        row.url = data.get("url") or row.url
        row.last_seen_at = now

    for design_id, row in existing.items():
        if design_id not in by_id and row.removed_at is None:
            row.removed_at = now
            removed += 1

    return {"added": added, "returned": returned, "removed": removed,
            "total": len(by_id)}


def record_check(db: Session, *, account_id: int, design_id: str,
                 status: str, title: Optional[str] = None,
                 search_tag: Optional[str] = None,
                 url: Optional[str] = None,
                 error: Optional[str] = None) -> Optional[StoreListing]:
    """
    One design's verdict, written into the catalogue.

    `consecutive_missing` counts UP while it stays missing and resets the
    moment it is seen — so it means "how long has this been broken", not
    "how often has it ever broken". That distinction is what makes it usable
    as evidence for a vague tag rather than just a tally of bad luck.
    """
    row = db.query(StoreListing).filter_by(
        account_id=account_id, design_id=str(design_id)).first()
    if row is None:
        return None

    row.title = title or row.title
    row.search_tag = search_tag or row.search_tag
    row.url = url or row.url
    row.status = status
    row.status_error = error
    row.last_checked_at = datetime.utcnow()

    if status == "missing":
        row.consecutive_missing = (row.consecutive_missing or 0) + 1
    elif status == "visible":
        row.consecutive_missing = 0
    return row


def looks_vague(row: StoreListing, after_fixes: int) -> bool:
    """
    Has the cure been tried enough times to suspect the TAG, not the listing?

    The visibility check searches a design's primary tag and gives up after
    `scan_max_search_pages` (25) pages. For "Shadow of the Colossus" that is
    conclusive. For "Queen" it is nowhere near: a healthy design can sit at
    page four hundred and read MISSING every single time, forever.

    Deactivating and reactivating cannot fix that, so a design still missing
    after several attempts is flagged for the owner to search by hand rather
    than being cycled again. He can then exclude it, or edit the tag.
    """
    return bool(row.status == "missing"
                and (row.fix_attempts or 0) >= after_fixes)


def scannable_listings(db: Session, run: StoreScanRun,
                       account_id: Optional[int] = None) -> list[StoreListing]:
    """
    Which designs this run should check.

    `missing_only` is the recheck after a cure: there is no point re-reading
    two thousand healthy designs to find out whether eleven came back. It
    turns a six-hour sweep into a few minutes, which is the difference
    between checking and not bothering.
    """
    from ..pipeline import get_setting

    q = db.query(StoreListing).filter(
        StoreListing.marketplace == run.marketplace,
        StoreListing.removed_at.is_(None),
        StoreListing.excluded == 0)
    if account_id:
        q = q.filter(StoreListing.account_id == account_id)
    if run.scan_mode == "missing_only":
        q = q.filter(StoreListing.status == "missing")

    # ── WHAT COUNTS AS "ALREADY DONE" ────────────────────────────────────
    #
    # Everything checked at or after the watermark is skipped.
    #
    #   full / missing_only — watermark is when THIS run started, so a pause
    #       resumes where it stopped, and a full sweep still rechecks every
    #       design. That last part matters: a status is a fact with a DATE on
    #       it, and a sweep that skipped anything already marked would freeze
    #       the catalogue and stop noticing designs that newly drop out.
    #
    #   continue — watermark goes BACK a few hours, so work done by an
    #       earlier, interrupted sweep also counts. That is what makes "carry
    #       on from where last night died" work, and it is deliberately keyed
    #       on the DESIGN rather than on the run: after a night of stopping
    #       and starting there is no single run to chain to, and per-account
    #       would have missed the 50 designs one account stopped short of.
    watermark = run.started_at
    if run.scan_mode == "continue":
        hours = float(get_setting(db, "scan_continue_within_h") or 24)
        watermark = min(watermark, datetime.utcnow() - timedelta(hours=hours))

    return [r for r in q.order_by(StoreListing.account_id, StoreListing.id).all()
            if not r.last_checked_at or r.last_checked_at < watermark]


def missing_for(db: Session, run: StoreScanRun,
                account_id: Optional[int] = None) -> list[StoreListing]:
    """
    Designs to deactivate: missing, not excluded, and not already flagged as
    a probably-vague tag.

    The vague ones are deliberately held back. Cycling a design whose tag is
    simply too broad achieves nothing and takes a live listing offline for
    the duration, twice, for no gain.

    So are designs this run has ALREADY failed on — see `_not_failed_this_run`.
    """
    from ..pipeline import get_setting

    after = int(get_setting(db, "scan_vague_after_fixes"))
    rows = db.query(StoreListing).filter(
        StoreListing.marketplace == run.marketplace,
        StoreListing.status == "missing",
        StoreListing.removed_at.is_(None),
        StoreListing.excluded == 0,
        StoreListing.deactivated_at.is_(None))
    if account_id:
        rows = rows.filter(StoreListing.account_id == account_id)
    return [r for r in rows.order_by(StoreListing.account_id).all()
            if not looks_vague(r, after)
            and _not_failed_this_run(r, run, "deactivate")]


def deactivated_for(db: Session, run: StoreScanRun,
                    account_id: Optional[int] = None) -> list[StoreListing]:
    """
    Exactly what WE turned off and have not yet turned back on.

    Never the marketplace's inactive list, which on one real account holds
    379 designs the owner deactivated himself over months. There is no way to
    tell those apart from the outside, and no need to: we wrote down what we
    touched.
    """
    q = db.query(StoreListing).filter(
        StoreListing.marketplace == run.marketplace,
        StoreListing.deactivated_at.isnot(None))
    if account_id:
        q = q.filter(StoreListing.account_id == account_id)
    return [r for r in q.order_by(StoreListing.account_id).all()
            if _not_failed_this_run(r, run, "reactivate")]


def _not_failed_this_run(row: StoreListing, run: StoreScanRun,
                         stage: str) -> bool:
    """
    True unless we already tried THIS ACTION on this design in this run and
    it would not go.

    ════════════════════════════════════════════════════════════════════════
    THE ACTION MATTERS, AND LEAVING IT OUT LEFT A LISTING HIDDEN
    ════════════════════════════════════════════════════════════════════════
    The first version ignored which action had failed, so one error field
    served two opposite jobs. A design that was successfully switched OFF and
    then failed a second, duplicate switch-off attempt carried that error
    into the reactivate stage — and was skipped. It stayed off, earning
    nothing, with the screen reporting the run finished cleanly.

    Skipping is only ever right for the SAME action: a deactivate that
    cannot find its button will not find it next time either, whereas a
    reactivate is cheap, idempotent, and the alternative is a live listing
    left hidden. Wrong in the cheap direction.

    ════════════════════════════════════════════════════════════════════════
    THIS IS WHAT STOPS THE STAGE LOOPING FOREVER
    ════════════════════════════════════════════════════════════════════════
    A stage now ends when no account has any work left, and "work left" is
    derived from the catalogue rather than counted. That is deliberate — a
    derived answer cannot drift out of step the way a counter did when the
    first account to finish ended the stage for all five.

    But it has a failure mode of its own: a design that CANNOT be switched
    off — deleted at the marketplace, already inactive, the session not
    signed in as its owner — never leaves the list. It would be handed out,
    fail, and be handed straight back, for as long as the machine is on.

    Comparing the failure's timestamp against the run's start date closes
    that: failed this run means skip, failed last week means try again. The
    designs are not hidden — `stuck_for()` counts them and the screen names
    them, because silently skipping work is how a run reports success over
    nothing having happened.
    """
    if not row.action_error:
        return True
    # An error recorded before the action was tracked. Only the DEACTIVATE
    # side inherits the old caution: for reactivate, an unattributed failure
    # must not be a reason to leave a listing switched off.
    if row.action_error_kind is None and stage != "deactivate":
        return True
    if row.action_error_kind and row.action_error_kind != stage:
        return True
    if row.action_error_at is None:
        # Written before this column existed. Trust the error and skip it;
        # the next successful action clears all three.
        return False
    return row.action_error_at < run.started_at


def would_deactivate(db: Session, marketplace: str) -> list[StoreListing]:
    """
    What a deactivation started RIGHT NOW would switch off.

    Uses an unsaved probe run rather than a second copy of the filtering,
    because the number on the button has to be the number the button will
    actually do — and two definitions of "what is missing enough to switch
    off" would drift apart the first time the vague-tag rule changed. The
    probe is never added to the session; it exists only to carry the
    marketplace and a start time.
    """
    probe = StoreScanRun(marketplace=marketplace, started_at=datetime.utcnow())
    return missing_for(db, probe)


def stuck_for(db: Session, run: StoreScanRun) -> list[StoreListing]:
    """Designs this run tried to switch and could not. Named on the screen."""
    return [
        r for r in db.query(StoreListing).filter(
            StoreListing.marketplace == run.marketplace,
            StoreListing.action_error.isnot(None),
            StoreListing.action_error_at.isnot(None)).all()
        if r.action_error_at >= run.started_at
    ]


def counts(db: Session, run: Optional[StoreScanRun] = None,
           marketplace: str = "teepublic") -> dict:
    """Totals for the screen and for deciding when a stage is over."""
    from ..pipeline import get_setting

    after = int(get_setting(db, "scan_vague_after_fixes"))
    rows = db.query(StoreListing).filter(
        StoreListing.marketplace == marketplace).all()
    live = [r for r in rows if r.removed_at is None]

    checked = 0
    if run is not None and run.started_at:
        checked = sum(1 for r in live
                      if r.last_checked_at and r.last_checked_at >= run.started_at)

    # ── THE DENOMINATOR THE PERSON IS LOOKING AT ─────────────────────────
    #
    # `total` is the whole catalogue. `run_total` is what THIS run set out to
    # do — which for a CONTINUE is only the backlog. Reporting "17 of 1543"
    # on a run covering 627 was technically true and completely misleading:
    # the only way to tell was to open the node's console.
    #
    # checked + still-to-do, so it stays put as the run progresses instead of
    # drifting the way a snapshot taken at the start would.
    run_total = checked
    # What a DEACTIVATE will actually act on — fewer than "missing", because
    # vague tags and excluded designs are held back and anything already
    # switched off is not switched off twice. The button must promise the
    # number it will do.
    #
    # Computed with no run as well, because the standalone SWITCH OFF button
    # exists when nothing is running and a button whose number is always
    # zero when you can actually press it is worse than no number.
    to_deactivate = len(would_deactivate(db, marketplace))
    if run is not None and run.status not in FINISHED:
        run_total = checked + len(scannable_listings(db, run))
        to_deactivate = len(missing_for(db, run))

    return {
        "total":        len(live),
        "checked":      checked,
        "run_total":    run_total,
        "to_deactivate": to_deactivate,
        "visible":      sum(1 for r in live if r.status == "visible"),
        "missing":      sum(1 for r in live if r.status == "missing"),
        "unknown":      sum(1 for r in live if r.status == "unknown"),
        "errors":       sum(1 for r in live if r.status == "error"),
        "excluded":     sum(1 for r in live if r.excluded),
        "vague":        sum(1 for r in live if looks_vague(r, after)),
        "removed":      sum(1 for r in rows if r.removed_at),
        "deactivated":  sum(1 for r in live if r.deactivated_at),
        "action_errors": sum(1 for r in live if r.action_error),
    }


# ════════════════════════════════════════════════════════════════════════════
#  AUTOMATIC MODE
# ════════════════════════════════════════════════════════════════════════════

def next_stage(run: StoreScanRun) -> Optional[str]:
    """
    What an automatic run does after the stage that just finished.

    Returns None when a person has to decide — which is every gate on a
    MANUAL run, and nothing at all on an automatic one.
    """
    if not run.auto or run.paused_at:
        return None
    return {"reviewing": "deactivate", "confirming": "reactivate"}.get(run.status)


def pause_run(db: Session, run: StoreScanRun, *, by: str) -> None:
    """
    Hold the run where it is and give the machine back.

    Nothing is lost and nothing is undone: designs already checked stay
    checked, and designs already deactivated stay deactivated — which is why
    the screen has to SAY so, because pausing between the two action stages
    leaves live listings switched off.
    """
    run.paused_at = datetime.utcnow()
    run.paused_by = by


def resume_run(db: Session, run: StoreScanRun) -> None:
    """Pick up exactly where it stopped."""
    run.paused_at = None
    run.paused_by = None


def stage_work(db: Session, run: StoreScanRun,
               stage: str) -> dict[int, list[StoreListing]]:
    """
    What is left to do in this stage, by account. Empty means the stage is over.

    Derived from the catalogue every single time, never stored. Both pickers
    drain themselves as the work happens — deactivating sets `deactivated_at`
    so the row leaves `missing_for`, reactivating clears it so the row leaves
    `deactivated_for` — which means "is there anything left" is a question
    about the world rather than about a counter somebody has to remember to
    increment.

    That distinction is the whole fix. The counter version advanced the run
    the moment the FIRST of five accounts reported, and 178 live listings
    were left switched off with the screen showing everything as fine.
    """
    picker = missing_for if stage == "deactivate" else deactivated_for
    out: dict[int, list[StoreListing]] = {}
    for row in picker(db, run):
        out.setdefault(row.account_id, []).append(row)
    return out


def dispatch_stage(db: Session, run: StoreScanRun, stage: str,
                   *, by: str) -> int:
    """
    Send ONE account's worth of switching to the node. ONE definition, three
    callers: the button, an automatic run handing over, and the stall
    sweeper picking up after a dead job. A second copy of "how do we start a
    deactivation" is how the manual path and the automatic path drift into
    doing subtly different things — and the automatic one is the path nobody
    is watching.

    ════════════════════════════════════════════════════════════════════════
    ONE ACCOUNT AT A TIME, AND THAT IS THE POINT
    ════════════════════════════════════════════════════════════════════════
    The first version created every account's job UP FRONT — five jobs, one
    per account, all queued together. Two consequences, both real:

      · Stopping the run did nothing to them. The node claimed the next
        queued job and carried on switching designs off for another two
        hours while the screen read "abandoned".
      · A node that died mid-account left the stage counter short of its
        total forever, so the run hung, holding Photoshop and the uploads
        all night for nothing.

    One at a time means there is only ever ONE thing to cancel and ONE thing
    to restart. Nothing is lost by it: these stages are signed in, each
    account needs its own Chrome profile, and the node runs one job at a
    time regardless — so the jobs were queueing behind each other anyway.

    ════════════════════════════════════════════════════════════════════════
    MEASURED 2026-08-24: ABOUT AN HOUR PER ACCOUNT, NOT MINUTES
    ════════════════════════════════════════════════════════════════════════
    This docstring used to say "serial is fine, this stage is minutes not
    hours". That was a guess and it was wrong by a factor of about thirty.
    Every design needs its own freshly loaded page, because the deactivate
    form carries a one-time token, so the cost is one page load per design
    and there is no batching to be had. Five neglected accounts came to
    roughly ten hours across the two stages.

    NOTHING here may assume the stage is short. It is long enough that it
    must be stoppable, resumable, and survivable across a restart.

    Returns 1 when an account was dispatched, 0 when there is nothing left.
    """
    from .. import pipeline as P
    from . import service as earnings_service
    from . import wall

    work = stage_work(db, run, stage)
    if not work:
        return 0

    # Same order every time, so "account 3 of 5" means the same account it
    # meant a minute ago. Dictionary order would follow the query, which
    # changes underneath us as rows drain out of the picker.
    account_id = sorted(work)[0]
    designs = work[account_id]
    account = db.query(UploadAccount).filter_by(id=account_id).first()
    if account is None:
        # Its designs would never drain and the stage could never end. Give
        # up loudly rather than spinning on a row that points nowhere.
        raise RuntimeError(
            f"Design(s) in the catalogue belong to account {account_id}, "
            f"which no longer exists.")

    project = P.resolve_project(db, None)
    attempts = int(P.get_setting(db, "wall_max_attempts"))

    P.create_job(db, kind=f"store_{stage}", payload={
        "run_id": run.id,
        "action": stage,
        "account": P.account_payload(db, account, include_secret=True,
                                     project=project),
        "settings": P.upload_settings_payload(db, project=project),
        "designs": [{"design_id": d.design_id, "title": d.title,
                     "url": d.url} for d in designs],
        # The same wall that stands in front of the earnings page. These
        # stages are signed in, so it can appear here too.
        "wall_html_markers": earnings_service.site_markers(run.marketplace),
        "signed_out_markers": earnings_service.signed_out_markers(run.marketplace),
        "wall_paths": wall.payload_for(
            wall.next_paths(db, run.marketplace, attempts)),
        "wall_wait_s": P.get_setting(db, "wall_wait_s"),
        "wall_max_attempts": attempts,
    }, requested_by=by)

    run.status = "deactivating" if stage == "deactivate" else "reactivating"
    run.stage_account_id = account_id

    # ── THE FIGURES ARE FOR THE SCREEN, NOT FOR THE STATE MACHINE ────────
    #
    # `stage_jobs_total` is fixed by `begin_stage` and then left alone, so
    # the denominator does not shrink under the reader as accounts drain
    # out — "account 3 of 5" has to keep meaning the same thing until the
    # stage ends. What ENDS the stage is `stage_work` coming back empty.
    if not run.stage_jobs_total:
        run.stage_jobs_total = len(work)
    run.stage_jobs_done = max(0, run.stage_jobs_total - len(work))

    verb = "off" if stage == "deactivate" else "back on"
    run.stage_note = (
        f"Switching {verb}: {account.name}, {len(designs)} design(s). "
        f"Account {run.stage_jobs_done + 1} of {run.stage_jobs_total}.")
    return 1


def begin_stage(db: Session, run: StoreScanRun, stage: str, *, by: str) -> int:
    """
    Start a stage from scratch — resets the screen's account counter first.

    Separate from `dispatch_stage` because that one is also called to hand
    over to the NEXT account mid-stage, where resetting the total would make
    the denominator count down instead of holding still.
    """
    run.stage_jobs_total = 0
    run.stage_jobs_done = 0
    run.stage_attempts = 0
    run.stage_account_id = None
    return dispatch_stage(db, run, stage, by=by)


def scan_incomplete(db: Session, run: StoreScanRun) -> int:
    """
    How many designs this run still has to check. 0 means it finished.

    Deliberately just a count of `scannable_listings` rather than its own
    copy of the filtering — two definitions of "what is left" would drift,
    and this one decides both whether a run may advance to deactivation and
    what the screen tells you.

    DERIVED rather than stored, so it is right even for a run interrupted
    before anything could be recorded about it — including one an earlier
    version pushed to the review gate when it had merely been paused.
    """
    return len(scannable_listings(db, run))


def retry_delays(db: Session) -> list[int]:
    """
    The gaps, in minutes, from the dashboard. Length = how many attempts.

    Stored as "30,60,90" rather than three settings because they are one
    decision — how patient to be — and because adding a fourth attempt
    should not need a code change.
    """
    from ..pipeline import get_setting

    raw = str(get_setting(db, "scan_retry_delays_min") or "30,60,90")
    out = []
    for part in raw.replace(" ", "").split(","):
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out or [30, 60, 90]


# ════════════════════════════════════════════════════════════════════════════
#  THE JOBS A RUN OWNS
# ════════════════════════════════════════════════════════════════════════════
#
# A run and its node jobs are two different things, and forgetting that is
# what let a stopped run keep working. Everything below exists so the run is
# the ONLY thing anyone has to think about: stop the run and its work stops,
# because stopping the run is what cancels the job.

ACTION_KINDS = ("store_deactivate", "store_reactivate")
JOB_KINDS = ("store_scan",) + ACTION_KINDS
LIVE_JOB = ("queued", "running")


def _run_id_of(job) -> Optional[int]:
    import json
    try:
        return (json.loads(job.payload_json or "{}") or {}).get("run_id")
    except (TypeError, ValueError):
        return None


def jobs_for_run(db: Session, run: StoreScanRun, *,
                 statuses: tuple[str, ...] = LIVE_JOB) -> list:
    """Node jobs belonging to this run, in whichever states were asked for."""
    from ..models import PipelineJob

    return [
        j for j in db.query(PipelineJob).filter(
            PipelineJob.kind.in_(JOB_KINDS),
            PipelineJob.status.in_(statuses)).all()
        if _run_id_of(j) == run.id
    ]


def cancel_run_jobs(db: Session, run: StoreScanRun, *, why: str) -> int:
    """
    Cancel every job this run still owns. Returns how many.

    ════════════════════════════════════════════════════════════════════════
    STOPPING A RUN MUST STOP THE WORK
    ════════════════════════════════════════════════════════════════════════
    It did not. `finish_run` set a status and released the pipeline hold, and
    that was all — the node's queued jobs sat untouched, so it claimed the
    next account and carried on switching designs off for two more hours
    while the tab showed the run as abandoned. The screen and the machine
    disagreed, and only the machine was right.

    A cancelled QUEUED job is never claimed, which is the important half. A
    cancelled RUNNING one cannot be reached out and stopped — the node hears
    about it through the reply to its next per-design report, which is why
    `stage_should_stop` exists below.
    """
    from .. import pipeline as P

    killed = 0
    for job in jobs_for_run(db, run):
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
        P.append_job_log(db, job, f"Cancelled — {why}", level="warn")
        killed += 1
    return killed


def stage_should_stop(db: Session, run: Optional[StoreScanRun],
                      stage: str) -> bool:
    """
    Should the node stop switching designs right now?

    Asked on every single per-design report, which is the only channel that
    exists: a node cannot hear a button, it can only hear an ANSWER to
    something it was already asking. The scan already worked this way; the
    action stages threw the reply away, so PAUSE and STOP reached the screen
    and nothing else, and a paused run went on switching designs off.

    Every reason to stop is DERIVED here rather than stored in a flag,
    because a flag has two edges and the second one gets lost.
    """
    if run is None:
        return True
    if run.paused_at is not None:
        return True
    # The run has moved on — a stall sweep gave this account to a newer job,
    # or an admin sent the run somewhere else. Either way this job is stale.
    #
    # This one line also covers every ENDED run, because done/failed/
    # abandoned can never equal deactivating/reactivating. An explicit
    # `status in FINISHED` above it was written first and then deleted: a
    # sabotage test showed removing it changed no answer, which means it was
    # not protecting anything and would have read like a guard that was.
    expected = "deactivating" if stage == "deactivate" else "reactivating"
    return run.status != expected


def stalled_runs(db: Session) -> list[StoreScanRun]:
    """
    Runs in an action stage with no live job doing the work.

    ════════════════════════════════════════════════════════════════════════
    THE INVARIANT THIS WATCHES
    ════════════════════════════════════════════════════════════════════════
    A run that is switching designs must have exactly one job switching
    them. If it does not, nothing will ever move it again: the node died, the
    job errored, someone cancelled it. The run then sits in `deactivating`
    for ever, holding Photoshop and the daily uploads, with the screen
    politely reporting work in progress.

    That is stated as a property of STATE, not of flow, which is the whole
    point — it holds however the job died, including ways nobody thought of.

    The claim timeout is borrowed from `reap_stale_claims` deliberately.
    Two different ideas of "long enough that it must be dead" would drift,
    and the reaper is the thing that marks the job dead in the first place.
    """
    from ..models import PipelineJob
    from ..pipeline import get_setting

    cutoff = datetime.utcnow() - timedelta(
        minutes=int(get_setting(db, "claim_timeout_min")))

    out = []
    for run in db.query(StoreScanRun).filter(
            StoreScanRun.status.in_(("deactivating", "reactivating"))).all():
        if run.paused_at or (run.retry_at and run.retry_at > datetime.utcnow()):
            continue
        # ── SILENT PAST THE TIMEOUT, NOT MERELY SLOW ─────────────────────
        #
        # This compared `started_at`, which meant a job was called dead the
        # moment it had been RUNNING for 45 minutes — regardless of whether
        # it was working perfectly. A switching stage takes about an hour
        # and reports once per design, so on 2026-08-24 a live job with 8
        # designs left was cancelled and those 8 were dispatched again.
        #
        # The question is when the job last SAID something, which is what
        # `last_report_at` records. `started_at` remains the fallback for a
        # job that has never reported at all — the case this was written
        # for, a node that died between claiming and starting.
        live = [j for j in jobs_for_run(db, run)
                if j.status == "queued"
                or (j.last_report_at or j.started_at
                    or datetime.utcnow()) > cutoff]
        if not live:
            out.append(run)
    return out


def repair_stalled(db: Session, run: StoreScanRun) -> str:
    """
    Get a stalled run moving again, or end it. Returns what was done, in words.

    Repair is tried FIRST and giving up comes second, because the common
    cause is dull — the node rebooted, Chrome would not start once — and the
    work itself is fine. Retrying the same account more than a few times is
    not persistence though: it is a loop, and it would hold the pipeline all
    night doing nothing.
    """
    from ..pipeline import get_setting

    stage = "deactivate" if run.status == "deactivating" else "reactivate"
    limit = int(get_setting(db, "store_stage_max_attempts"))

    if not stage_work(db, run, stage):
        # Nothing left to do — the work finished and only the report was
        # lost. Advancing is right, and it is the same decision `stage-done`
        # would have made, so it goes through the same function.
        return advance_after_stage(db, run, stage)

    run.stage_attempts = int(run.stage_attempts or 0) + 1
    if run.stage_attempts > limit:
        left = sum(len(v) for v in stage_work(db, run, stage).values())
        finish_run(db, run, status="failed",
                   note=(f"Gave up: the worker machine stopped reporting "
                         f"{run.stage_attempts} times in a row with {left} "
                         f"design(s) still to switch "
                         f"{'off' if stage == 'deactivate' else 'back on'}. "
                         f"Photoshop and uploads have the machine back."))
        cancel_run_jobs(db, run, why="run gave up")
        return f"failed after {run.stage_attempts} attempts"

    cancel_run_jobs(db, run, why="worker stopped reporting")
    dispatch_stage(db, run, stage, by="stall-sweeper")
    return (f"restarted {stage} (attempt {run.stage_attempts} of {limit})")


def advance_after_stage(db: Session, run: StoreScanRun, stage: str) -> str:
    """
    One stage's work is finished — what happens next. Returns it in words.

    ════════════════════════════════════════════════════════════════════════
    ONE PLACE DECIDES THIS
    ════════════════════════════════════════════════════════════════════════
    Three things can discover that a stage is over: the node reporting, the
    stall sweeper finding the work already done, and an admin pressing a
    button. All three come here. When the node's report was the only path,
    the other two had no way to move a run on at all — which is precisely
    how a run with every design already switched off still sat in
    `deactivating` for ever.
    """
    if stage_work(db, run, stage):
        return "still work left"

    run.stage_account_id = None
    run.stage_attempts = 0

    if stage == "reactivate":
        stuck = len(stuck_for(db, run))
        finish_run(db, run, status="done",
                   note=("Everything we switched off is back on."
                         + (f" {stuck} design(s) could not be switched and "
                            f"are listed below." if stuck else "")))
        return "run finished"

    # Deactivation done. On an automatic run reactivation follows straight
    # on; on a manual one a person confirms first.
    run.status = "confirming"
    run.stage_note = "Everything is switched off. Ready to switch back on."
    nxt = next_stage(run)
    if not nxt:
        return "waiting for you to confirm"

    # ── A NEXT STAGE WITH NOTHING IN IT MUST END THE RUN ─────────────────
    #
    # If every design refused to switch off there is nothing to switch back
    # on. Ignoring that leaves an automatic run parked at `confirming` — a
    # gate, so it holds Photoshop and the uploads and waits for a button
    # that an unattended run will never get. Overnight that is the whole
    # night lost, and the screen would say it was waiting for the owner.
    if begin_stage(db, run, nxt, by="auto"):
        return "moved straight on to switching back on"

    finish_run(db, run, status="done",
               note="Nothing was left switched off, so there was nothing to "
                    "put back.")
    return "run finished — nothing to switch back on"


def schedule_retry(db: Session, run: StoreScanRun, *, reason: str) -> Optional[int]:
    """
    Sleep, then try the stage again. Returns the delay, or None if out of tries.

    ════════════════════════════════════════════════════════════════════════
    WHY RETRY AT ALL — AND WHY SPACED OUT
    ════════════════════════════════════════════════════════════════════════
    The wall failed three times in 54 seconds and the run gave up, wasting
    six and a half hours of an unattended night. Three attempts inside one
    minute is not three chances: whatever was wrong at 23:13 was still wrong
    at 23:14. Real gaps turn them into three genuinely different moments.

    This does NOT contradict the rule that a retry can be the cause of a
    problem — that one is about things the far side COUNTS. Sign-in attempts
    are counted, and hammering them is how a suspicious address becomes a
    blocked one. This scan is signed out and reading public pages; loading a
    search page again half an hour later is indistinguishable from an
    ordinary visitor. There is no counter to trip.

    Giving up is still the right answer eventually. Three spaced attempts
    failing means it is not a moment, and something has actually changed.
    """
    delays = retry_delays(db)
    attempt = int(run.retry_count or 0)
    if attempt >= len(delays):
        return None

    minutes = delays[attempt]
    run.retry_count = attempt + 1
    run.retry_at = datetime.utcnow() + timedelta(minutes=minutes)
    run.retry_note = reason[:400]
    run.stage_note = (f"Waiting {minutes} minutes before trying again "
                      f"(attempt {run.retry_count} of {len(delays)}). {reason[:200]}")
    return minutes


def due_retries(db: Session) -> list[StoreScanRun]:
    """Runs whose waiting time is up. Asked by the scheduler tick."""
    now = datetime.utcnow()
    return [r for r in db.query(StoreScanRun)
                        .filter(~StoreScanRun.status.in_(FINISHED))
                        .filter(StoreScanRun.retry_at.isnot(None)).all()
            if r.retry_at <= now and not r.paused_at]


def continue_backlog(db: Session, marketplace: str) -> int:
    """
    How many designs a CONTINUE would check right now.

    Computed the same way the scan computes it — anything not checked inside
    the window — so the number on the button is the number that will actually
    be done. A button that promises 627 and then does 1,543 is worse than no
    button.
    """
    from ..pipeline import get_setting

    hours = float(get_setting(db, "scan_continue_within_h") or 24)
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    rows = db.query(StoreListing).filter(
        StoreListing.marketplace == marketplace,
        StoreListing.removed_at.is_(None),
        StoreListing.excluded == 0).all()
    return sum(1 for r in rows
               if not r.last_checked_at or r.last_checked_at < cutoff)


def stranded(db: Session, marketplace: str) -> list[StoreListing]:
    """
    Designs we switched OFF and never switched back on.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS ITS OWN QUESTION
    ════════════════════════════════════════════════════════════════════════
    These are live listings, earning nothing, because something interrupted
    a run between the two halves of the cure. It happens for boring reasons —
    a stage that ended early, a run abandoned at the wrong moment, the node
    dying — and there is no reason the owner should have to reason about
    WHICH run left them off.

    So the question is asked of the catalogue, not of a run: what is off
    right now that we turned off? That is also why reactivation has always
    worked from this rather than from a run's own list.
    """
    return (db.query(StoreListing)
              .filter(StoreListing.marketplace == marketplace,
                      StoreListing.deactivated_at.isnot(None))
              .order_by(StoreListing.account_id, StoreListing.title).all())
