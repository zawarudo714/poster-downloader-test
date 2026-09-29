"""
Payments helper — eligible-poster counting and payment-run bookkeeping.

A poster counts toward pay only if ALL of:
  - It's a non-deleted SavedPoster (deleted_at IS NULL) — OR it was retired
    with pay (pay_despite_delete=1): the owner binned the picture because
    the place has no good photograph anywhere, which nobody could know
    before the worker spent the time looking, so the search is still paid.
  - Saved by the worker in question (matched by user_id).
  - Created on a date inside the requested period.
  - It has NO open or awaiting-approval revision against it RIGHT NOW.
  - It hasn't already been paid (poster_ids_json across past PaymentRuns).
  - Its TITLE has not already been paid for (a replacement on a paid title
    is not paid again — owner, 2026-09-29), and it is not a replacement
    still waiting for the owner's look. See unpayable_reasons, which is the
    one place every "can this be paid now" question is answered.

The "no open revision" check is intentional — if admin flagged it but the
worker hasn't fixed yet, we don't pay for it. If/when the revision resolves,
the poster becomes eligible for the NEXT payment run.

Replacement counts as 1 unit because there's only one SavedPoster row regardless
of how many times its bytes were swapped.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from .models import AppSetting, PaymentRun, Revision, SavedPoster
from .timeutil import local_today


# ─── Settings helpers ──────────────────────────────────────────────────────

DEFAULT_RATE_KES = "10"          # change defaults via Admin → Payments
DEFAULT_WEEK_START = "0"         # 0 = Monday, 6 = Sunday (ISO weekday convention - 1)


def get_setting(db: Session, key: str, default: str) -> str:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        return default
    return row.value


def set_setting(db: Session, key: str, value: str, *, by: str | None = None) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=by)
        db.add(row)
    else:
        row.value = value
        row.updated_by = by
        row.updated_at = datetime.utcnow()


def get_rate_kes(db: Session, project=None) -> str:
    """
    Pay per item, for one project.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS DELEGATES TO pipeline.get_setting
    ════════════════════════════════════════════════════════════════════════
    The rate used to be a single `pay_rate_kes` row, which was right when
    there was one niche. A movie poster and a MUSIK image are different work
    and can be worth different money, so the rate has to resolve per project.

    Rather than invent a second per-project settings mechanism, this reuses
    the pipeline's cascade — `pipeline.<slug>.pay_rate_kes` -> `pipeline
    .pay_rate_kes` -> DEFAULTS. Same resolution order as every other
    per-project value, one place to look, nothing new to learn.

    `project=None` returns the global rate, which is what the legacy callers
    and any cross-project total want.
    """
    from .pipeline import get_setting as pipeline_setting
    try:
        return str(pipeline_setting(db, "pay_rate_kes", project=project))
    except Exception:
        # Never let a settings problem block a payment run.
        return get_setting(db, "pay_rate_kes", DEFAULT_RATE_KES)


def split_poster_ids_by_project(db: Session, poster_ids: list[int]) -> dict:
    """
    Group paid posters by the project they belong to.

    Returns {project_id_or_None: [poster_id, ...]}. NULL project_id means the
    default project — the 101k imported titles have never been backfilled, so
    this must not be treated as "unassigned".
    """
    if not poster_ids:
        return {}
    from .models import MasterTitle
    from .pipeline import ensure_default_project

    default_id = ensure_default_project(db).id
    out: dict = {}
    for i in range(0, len(poster_ids), 500):
        chunk = poster_ids[i:i + 500]
        rows = (
            db.query(SavedPoster.id, MasterTitle.project_id)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .filter(SavedPoster.id.in_(chunk))
              .all()
        )
        for pid, proj_id in rows:
            key = proj_id if proj_id is not None else default_id
            out.setdefault(key, []).append(pid)
    return out


def price_run(db: Session, poster_ids: list[int]) -> dict:
    """
    Price a payment run across however many projects it spans.

    A worker covering two niches is paid ONCE — splitting the payout would
    mean two M-Pesa transfers for one week's work, which is worse for
    everyone. What differs per project is the RATE, so the total is
    sum(count_in_project x rate_of_project).

    Returns:
        {
          "total": Decimal,
          "by_project": {
              "MUSIK": {"count": 120, "rate": "6", "subtotal": "720"},
              ...
          },
        }
    """
    from decimal import Decimal
    from .models import Project

    grouped = split_poster_ids_by_project(db, poster_ids)
    by_project: dict = {}
    total = Decimal("0")

    for project_id, ids in grouped.items():
        project = db.query(Project).filter_by(id=project_id).first()
        rate = parse_decimal(get_rate_kes(db, project=project))
        subtotal = rate * len(ids)
        total += subtotal
        name = project.name if project else "Unassigned"
        by_project[name] = {
            "count": len(ids),
            "rate": _trim(rate),
            "subtotal": _trim(subtotal),
        }
    return {"total": total, "by_project": by_project}


def _trim(value) -> str:
    """Render a Decimal without trailing zeros: 720.00 -> 720."""
    text = str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def get_week_start_day(db: Session) -> int:
    """0..6 where 0 = Monday."""
    raw = get_setting(db, "week_start_day", DEFAULT_WEEK_START)
    try:
        n = int(raw)
        return n if 0 <= n <= 6 else 0
    except ValueError:
        return 0


def parse_decimal(raw: str) -> Decimal:
    """Parse a decimal-ish string. Raises ValueError on bad input."""
    try:
        return Decimal((raw or "0").strip())
    except InvalidOperation as e:
        raise ValueError(f"Invalid number: {raw!r}") from e


# ─── Date helpers ──────────────────────────────────────────────────────────

def week_bounds_containing(d: date, week_start: int) -> tuple[date, date]:
    """
    Return [start, end] (both inclusive) of the week that contains `d`,
    where the week starts on `week_start` (0=Mon..6=Sun).
    """
    # Python's date.weekday() returns 0=Mon..6=Sun, perfect for our convention.
    delta = (d.weekday() - week_start) % 7
    start = d - timedelta(days=delta)
    end   = start + timedelta(days=6)
    return start, end


# ─── Eligibility queries ──────────────────────────────────────────────────

def _already_paid_poster_ids(db: Session, worker_id: int) -> set[int]:
    """All saved_poster IDs that were already counted in a past PaymentRun for this worker."""
    return _paid_ids_from(
        db.query(PaymentRun.poster_ids_json).filter_by(worker_id=worker_id).all())


def _every_paid_poster_id(db: Session) -> set[int]:
    """Every saved_poster ID any payment run has ever paid, for any worker.
    "A title is never paid twice" is about the TITLE, so it must see a
    picture paid to somebody else too."""
    return _paid_ids_from(db.query(PaymentRun.poster_ids_json).all())


def _paid_ids_from(rows) -> set[int]:
    paid: set[int] = set()
    for (raw,) in rows:
        if not raw:
            continue
        try:
            ids = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for pid in ids:
            if isinstance(pid, int):
                paid.add(pid)
    return paid


def payable_criteria(worker_id: int) -> list:
    """
    THE definition of "a poster this worker could be paid for". One copy.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS A FUNCTION AND NOT THREE COPIES OF A FILTER
    ════════════════════════════════════════════════════════════════════════
    `eligible_poster_ids` excluded admin-added posters — correct, you do not
    pay a worker for an image you added yourself. `unpaid_dates_before`,
    which draws the "you forgot to pay these older days" banner, did not.

    So one poster the owner added by hand on 2026-05-18 was counted as
    unpaid for ever and could never be paid. The banner said "1 unpaid
    poster", clicking it totalled nothing, and it had been doing that for
    months. Its own docstring said it returned "currently-eligible" posters,
    which is what it meant to do and not what it did.

    Two queries that must agree about what counts will eventually disagree
    if they each carry their own copy of the rules. Now they cannot.
    """
    return [
        SavedPoster.user_id == worker_id,
        # Deleted means unpaid — EXCEPT a retire-with-pay (2026-09-20): the
        # RETIRE TITLE flow soft-deletes the picture but the search time was
        # honest work, so that one deletion stays payable. The flag lives on
        # the row, the rule lives here, once.
        or_(SavedPoster.deleted_at.is_(None),
            SavedPoster.pay_despite_delete == 1),
        # Anything the ADMIN added is not the worker's work and is never
        # payable. This is the line that was missing from the banner.
        SavedPoster.added_by.is_(None),
    ]


# Why a picture that passes payable_criteria cannot be paid RIGHT NOW. The
# words are what the PAID band and the worker's history show; the keys are
# what the code compares.
UNPAYABLE_WORDS = {
    "paid":       "already paid",
    "title_paid": "this title was already paid for, and a title is paid once",
    "flag":       "flag still open, so it is paid once you approve the fix",
    "look":       "a replacement you have not looked at yet — it is paid "
                  "once you approve it on Changes Requested",
}


def _chunks(ids, size=500):
    ids = sorted(ids)
    for at in range(0, len(ids), size):
        yield ids[at:at + size]


def unpayable_reasons(db: Session, worker_id: int, candidate_ids) -> dict:
    """
    THE one answer to "which of these pictures cannot be paid now, and
    why". Returns {poster_id: key}; a picture missing from the answer IS
    payable. Every screen that counts money owed asks this — the payment
    run itself, the "older unpaid days" list, the PAID band on Worker
    Images and the worker's own history — so they cannot disagree.

    The candidates must already pass payable_criteria. In order:

      paid        already in one of this worker's payment runs.
      title_paid  the title already had its pictures paid (by ANY run, to
                  anybody), so a replacement is not paid again. Owner,
                  2026-09-29: "pay only when the old picture was not paid,
                  so a title is never paid twice". The limit is the
                  project's images_per_title (1 for travel), so a project
                  that takes three pictures per title still pays three. A
                  project with no number set has no limit. When two unpaid
                  pictures compete for the last place, the OLDER one wins.
      flag        an open or awaiting flag on it (or on a similar pair).
      look        a replacement the owner has not looked at yet
                  (utils.awaiting_your_look_by_title). Owner, 2026-09-29:
                  payment holds these back until he approves them.

    Found 2026-09-29: four pictures showed up as payable in "everything
    before this week" although the owner had approved nothing — they were
    redos on titles he had flagged, rejected or already paid for.
    """
    from .models import MasterTitle, Project
    from .pipeline import _default_project_id
    from .utils import awaiting_your_look_by_title

    asked = {i for i in (candidate_ids or []) if i is not None}
    if not asked:
        return {}
    # The answer for one picture must not depend on which others the caller
    # happened to pass: "the older unpaid picture on this title wins" needs
    # to see that older picture even when it is deleted-but-payable or on
    # another page. So every payable picture of this worker on the same
    # titles joins the question, and only the asked ones are answered.
    asked_titles: set = set()
    for chunk in _chunks(asked):
        asked_titles |= {r[0] for r in (
            db.query(SavedPoster.master_title_id)
              .filter(SavedPoster.id.in_(chunk)).all()) if r[0] is not None}
    cand = set(asked)
    for chunk in _chunks(asked_titles):
        cand |= {r[0] for r in (
            db.query(SavedPoster.id)
              .filter(SavedPoster.master_title_id.in_(chunk),
                      *payable_criteria(worker_id)).all())}
    out: dict = {}

    for pid in cand & _already_paid_poster_ids(db, worker_id):
        out[pid] = "paid"
    rest = cand - set(out)

    # Where each remaining picture sits.
    info: dict = {}
    for chunk in _chunks(rest):
        for pid, tid, created, proj in (
                db.query(SavedPoster.id, SavedPoster.master_title_id,
                         SavedPoster.created_at, MasterTitle.project_id)
                  .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
                  .filter(SavedPoster.id.in_(chunk)).all()):
            info[pid] = (tid, created, proj)
    title_ids = {tid for tid, _c, _p in info.values() if tid is not None}

    # How many pictures each title has ALREADY had paid, by anybody.
    every_paid = _every_paid_poster_id(db)
    paid_on_title: dict = {}
    for chunk in _chunks(title_ids):
        for pid, tid in (db.query(SavedPoster.id, SavedPoster.master_title_id)
                           .filter(SavedPoster.master_title_id.in_(chunk)).all()):
            if pid in every_paid:
                paid_on_title[tid] = paid_on_title.get(tid, 0) + 1
    default_pid = _default_project_id(db)
    per_title = {p.id: int(p.images_per_title)
                 for p in db.query(Project).all() if p.images_per_title}

    def limit_of(proj):
        return per_title.get(proj if proj is not None else default_pid)

    for pid, (tid, _c, proj) in info.items():
        lim = limit_of(proj)
        if lim is not None and paid_on_title.get(tid, 0) >= lim:
            out[pid] = "title_paid"
    rest -= set(out)

    # Open or awaiting flags, on the picture itself or in a similar pair.
    flagged: set = set()
    for chunk in _chunks(rest):
        flagged |= {r[0] for r in (
            db.query(Revision.saved_poster_id)
              .filter(Revision.saved_poster_id.in_(chunk),
                      Revision.status.in_(("open", "awaiting_approval")))
              .all())}
    for (raw,) in (db.query(Revision.related_poster_ids)
                     .filter(Revision.status.in_(("open", "awaiting_approval")),
                             Revision.revision_type == "similar")
                     .all()):
        if not raw:
            continue
        try:
            flagged |= {i for i in json.loads(raw) if isinstance(i, int)}
        except (TypeError, ValueError):
            pass
    for pid in rest & flagged:
        out[pid] = "flag"
    rest -= set(out)

    # Replacements the owner has not looked at.
    waiting = awaiting_your_look_by_title(
        db, {info[p][0] for p in rest if p in info})
    unseen = {sp.id for lst in waiting.values() for sp in lst}
    for pid in rest & unseen:
        out[pid] = "look"
    rest -= set(out)

    # Two unpaid pictures on one title with room for one: the older is paid,
    # the other is not — otherwise the title is paid twice in ONE run.
    by_title: dict = {}
    for pid in rest:
        if pid in info:
            by_title.setdefault(info[pid][0], []).append(pid)
    for tid, pids in by_title.items():
        lim = limit_of(info[pids[0]][2])
        if lim is None:
            continue
        room = max(lim - paid_on_title.get(tid, 0), 0)
        pids.sort(key=lambda p: (info[p][1] is None, info[p][1], p))
        for pid in pids[room:]:
            out[pid] = "title_paid"
    return {pid: why for pid, why in out.items() if pid in asked}


def eligible_poster_ids(
    db: Session,
    *,
    worker_id: int,
    start: date,
    end: date,
) -> list[int]:
    """
    Return the saved_poster IDs that count toward pay for this worker
    over [start, end]. See module docstring for the rules.

    Used by both the preview endpoint (admin browsing what they'd pay for)
    and mark_paid (the actual payment run write).
    """
    base_q = (
        db.query(SavedPoster.id)
          .filter(
              *payable_criteria(worker_id),
              SavedPoster.original_save_date >= start,
              SavedPoster.original_save_date <= end,
          )
    )
    candidate_ids = {row[0] for row in base_q.all()}
    if not candidate_ids:
        return []
    # Already paid, title already paid, flagged, or waiting for the owner's
    # look — one function decides, for every screen (unpayable_reasons).
    candidate_ids -= set(unpayable_reasons(db, worker_id, candidate_ids))
    return sorted(candidate_ids)


def count_pending_revisions_today(db: Session, worker_id: int) -> int:
    """
    How many of *this worker's* live, today-saved posters are sitting under
    an open / awaiting-approval revision. Surfaced on the worker's dashboard
    as "X not counted until revised" for transparency.
    """
    # The same definition again. This is shown to the WORKER as "not counted
    # until revised" — a promise that it will count once the revision is
    # settled. For an admin-added poster that is never true, so it must not
    # be in this number either.
    today = local_today()
    base_ids = {
        row[0] for row in db.query(SavedPoster.id).filter(
            *payable_criteria(worker_id),
            SavedPoster.original_save_date == today,
        ).all()
    }
    if not base_ids:
        return 0
    blocked = (
        db.query(Revision.saved_poster_id)
          .filter(
              Revision.saved_poster_id.in_(base_ids),
              Revision.status.in_(("open", "awaiting_approval")),
          )
          .count()
    )
    return blocked


# ─── Payment-run summary helpers ──────────────────────────────────────────

def all_runs(db: Session, *, limit: int = 200):
    return (
        db.query(PaymentRun)
          .order_by(PaymentRun.created_at.desc())
          .limit(limit)
          .all()
    )


def pending_receipts_for_worker(db: Session, worker_id: int):
    """Pushed but not yet acknowledged/disputed receipts the worker should see."""
    return (
        db.query(PaymentRun)
          .filter(
              PaymentRun.worker_id == worker_id,
              PaymentRun.pushed_at.isnot(None),
              PaymentRun.ack_at.is_(None),
              PaymentRun.not_received_at.is_(None),
          )
          .order_by(PaymentRun.pushed_at.desc())
          .all()
    )


def unpaid_dates_before(
    db: Session,
    *,
    worker_id: int,
    today: date,
) -> list[dict]:
    """
    For each PAST day this worker has at least one *currently-eligible*
    poster (saved that day, not deleted, no active revision, not yet paid),
    return:
        {"date": "2026-04-30", "count": 5}

    Used to surface "you forgot to pay these older days" on the Payments
    UI. Days strictly before `today` only — today's eligible posters are
    visible by default through the normal preview.

    Backed by a single SQL aggregate so it stays fast even with thousands
    of posters.
    """
    from sqlalchemy import func as sa_func

    # The SAME definition the payment run uses — see payable_criteria. This
    # query used to carry its own copy without the admin-added exclusion, so
    # it counted posters that could never be paid.
    rows = (
        db.query(SavedPoster.id, SavedPoster.original_save_date)
          .filter(
              *payable_criteria(worker_id),
              SavedPoster.original_save_date < today,
          )
          .all()
    )
    if not rows:
        return []

    candidate = {pid for pid, _d in rows}
    candidate -= set(unpayable_reasons(db, worker_id, candidate))
    if not candidate:
        return []

    # Bucket survivors back into per-day counts.
    by_day: dict[date, int] = {}
    for pid, d in rows:
        if pid in candidate:
            by_day[d] = by_day.get(d, 0) + 1
    return [
        {"date": d.isoformat(), "count": n}
        for d, n in sorted(by_day.items(), reverse=True)
    ]


def per_day_breakdown(
    db: Session,
    *,
    poster_ids: list[int],
) -> dict[str, int]:
    """
    Given a list of saved_poster IDs (typically just paid in a run),
    return {"YYYY-MM-DD": count} of how many fall on each save_date.
    Used to render the per-day breakdown on receipts.
    """
    if not poster_ids:
        return {}
    rows = (
        db.query(SavedPoster.original_save_date)
          .filter(SavedPoster.id.in_(poster_ids))
          .all()
    )
    by_day: dict[str, int] = {}
    for (d,) in rows:
        if d is None:
            continue
        key = d.isoformat()
        by_day[key] = by_day.get(key, 0) + 1
    return by_day
