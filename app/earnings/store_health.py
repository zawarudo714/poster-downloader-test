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
        seen = (f"{tally['checked']} of {tally['total']}"
                if tally["total"] else "starting up")
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
            if not looks_vague(r, after)]


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
    return q.order_by(StoreListing.account_id).all()


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

    return {
        "total":        len(live),
        "checked":      checked,
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


def dispatch_stage(db: Session, run: StoreScanRun, stage: str,
                   *, by: str) -> int:
    """
    Queue the node jobs for a stage. ONE definition, two callers.

    The button on the tab calls it, and an automatic run calls it from
    `stage-done` when one stage hands over to the next. A second copy of
    "how do we start a deactivation" is exactly how the manual path and the
    automatic path would drift into doing subtly different things — and the
    automatic one is the path nobody is watching.

    ════════════════════════════════════════════════════════════════════════
    ONE JOB PER ACCOUNT HERE, UNLIKE THE SCAN
    ════════════════════════════════════════════════════════════════════════
    These stages are signed in, and each account needs its OWN Chrome
    profile — two accounts cannot share one browser. Serial is fine too:
    this stage is minutes, not hours.

    Returns how many jobs were queued.
    """
    from .. import pipeline as P
    from . import service as earnings_service
    from . import wall

    picker = missing_for if stage == "deactivate" else deactivated_for
    rows = picker(db, run)
    if not rows:
        return 0

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
        queued += 1

    run.status = "deactivating" if stage == "deactivate" else "reactivating"
    run.stage_note = f"{len(rows)} design(s) across {queued} account(s)."
    return queued


def scan_incomplete(db: Session, run: StoreScanRun) -> int:
    """
    How many designs this run still has not looked at. 0 means it finished.

    DERIVED from the catalogue rather than stored as a flag, so it is right
    even for a run that was interrupted before anything could be recorded
    about it — including one already sitting at the wrong stage because an
    earlier version treated "the job ended" as "the scan finished".

    A design counts as checked when its `last_checked_at` is at or after the
    run started. That is also what makes RESUMING cheap: everything already
    done is skipped.
    """
    rows = db.query(StoreListing).filter(
        StoreListing.marketplace == run.marketplace,
        StoreListing.removed_at.is_(None),
        StoreListing.excluded == 0)
    if run.scan_mode == "missing_only":
        rows = rows.filter(StoreListing.status == "missing")
    return sum(1 for r in rows.all()
               if not r.last_checked_at or r.last_checked_at < run.started_at)


# ════════════════════════════════════════════════════════════════════════════
#  WAITING OUT A TRANSIENT FAILURE
# ════════════════════════════════════════════════════════════════════════════

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
