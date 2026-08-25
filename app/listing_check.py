"""
Does the marketplace still show what our database believes it shows?

════════════════════════════════════════════════════════════════════════════
WHAT THIS ANSWERS
════════════════════════════════════════════════════════════════════════════
The database says 4,811 images are live on FineArtAmerica. Nobody has ever
checked. Three ways that can be wrong, and only the first costs money
quietly:

  · we think it is up and it is NOT — a copyright takedown, or an upload
    that failed and was recorded as a success
  · marked uploaded but never processed — an impossible state, so bad data
  · on the site but not in our records — uploaded outside the pipeline

The first two are answered here. The third needs the shop's own listing
pages read and compared, which is a different mechanism and a rarer
question; it is deliberately not in this module.

════════════════════════════════════════════════════════════════════════════
SIBLING OF diagnostics.py — IT REPORTS, A PERSON DECIDES
════════════════════════════════════════════════════════════════════════════
Nothing here changes a listing, and nothing here rewrites `status` on its
own. An observation goes into `listing_status`, which sits BESIDE what we
believe rather than replacing it, because the disagreement between the two
is the entire product. A sweep that quietly corrected the database would
destroy the only interesting thing it found.

`status` changes when the ADMIN explains a finding, through the existing
`removed` / `removed_reason` columns that were put there for exactly this.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS NOTHING LIKE THE TEEPUBLIC TOOL
════════════════════════════════════════════════════════════════════════════
They look like the same job and are not. TeePublic's question is "can this
design be FOUND", which needs a real browser, twenty-five pages of search
per design, and a deactivate/reactivate cure that switches live listings off
for hours. This question is "does this page EXIST", which a HEAD request
answers in a fraction of a second.

Measured 2026-08-24, and all of it is written up in the project brief:

  · the address is derivable from the stored title and the artist name
  · a missing listing returns a real HTTP 404
  · HEAD is honoured, so ~4,811 checks cost about an hour and no bandwidth
  · a FineArtAmerica listing is live or deleted — there is no hidden state

So: no stages, no gates, no cure, and NO PIPELINE HOLD. Making Photoshop
wait an hour for something that changes nothing would be pure loss.

The one thing it does borrow is the shape that was learned the hard way —
work goes out ONE CHUNK AT A TIME, the reply to each report carries the stop
signal, and "is the sweep finished" is DERIVED from the rows rather than
counted. The worker machine runs one job at a time, so a single hour-long
job would block Photoshop just as effectively as a hold would.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import ListingSweep, UploadAccount, UploadTracking

MARKETPLACE = "fineartamerica"
BASE = "https://fineartamerica.com/featured/"

FINISHED = ("done", "failed", "abandoned")

# Statuses the sweep is willing to look at. Only `uploaded` rows claim to be
# on the marketplace; a pending or failed one is not a disagreement, it is
# simply work that has not happened.
CLAIMS_LIVE = ("uploaded",)


# ════════════════════════════════════════════════════════════════════════════
#  THE ADDRESS
# ════════════════════════════════════════════════════════════════════════════

def slug(text: str) -> str:
    """
    The address form of a title or an artist name.

    Measured against six real listings across two shops: lower-case, and
    every run of non-alphanumeric characters becomes one hyphen. The " - "
    separator and the "#" in the MUSIK titles both dissolve into that, so
    neither needs a special case.

        "The Killing - 2011 C" -> the-killing-2011-c
        "Alicia Keys - #B"     -> alicia-keys-b
        "White And Black"      -> white-and-black
    """
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def listing_url(row: UploadTracking, artist: str) -> Optional[str]:
    """
    Where this listing lives, or None if we cannot say.

    ════════════════════════════════════════════════════════════════════════
    IT USES THE STORED TITLE, WHICH IS THE ONE FAA KEPT
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica rewrites titles on save — accents folded, most
    punctuation deleted, capped at 100 characters. `render_remote_title`
    already applies that BEFORE uploading, so `remote_title` is what the
    listing actually shows and the address can be built straight from it.

    That is the payoff for normalising at upload time rather than in the
    uploader. Build the address from the title we TYPED and every accented
    or punctuated one reads MISSING while sitting there live: `E.T. - 1982 A`
    lists as `ET - 1982 A` and lives at `et-1982-a-…`.
    """
    title = (row.remote_title or "").strip()
    if not title or not (artist or "").strip():
        return None
    return f"{BASE}{slug(title)}-{slug(artist)}.html"


# ════════════════════════════════════════════════════════════════════════════
#  WHAT IS LEFT TO DO
# ════════════════════════════════════════════════════════════════════════════

def accounts(db: Session) -> list[UploadAccount]:
    """Every account on this marketplace, whether or not it can be swept."""
    return [a for a in db.query(UploadAccount)
                         .order_by(UploadAccount.name).all()
            if (a.target_site or "").lower() == MARKETPLACE]


def ready(db: Session) -> tuple[list[UploadAccount], list[UploadAccount]]:
    """
    (can be swept, cannot) — split by whether the artist name is on file.

    An account without one is REPORTED rather than skipped quietly. A sweep
    that silently covered four accounts of six would report "everything is
    fine" about listings nobody looked at.
    """
    have = [a for a in accounts(db) if (a.artist_name or "").strip()]
    missing = [a for a in accounts(db) if not (a.artist_name or "").strip()]
    return have, missing


def sweepable(db: Session, sweep: Optional[ListingSweep] = None) -> list[UploadTracking]:
    """
    Rows still to check. Empty means the sweep is over.

    DERIVED every time, never stored. A row leaves this list the moment its
    `listing_checked_at` moves past the sweep's start, so "is there anything
    left" is a question about the table rather than about a counter somebody
    has to remember to increment — which is exactly what went wrong when the
    deactivation stage counted its accounts by hand.
    """
    names = {a.id for a in ready(db)[0]}
    if not names:
        return []

    q = (db.query(UploadTracking)
           .filter(UploadTracking.target_site == MARKETPLACE,
                   UploadTracking.status.in_(CLAIMS_LIVE),
                   UploadTracking.account_id.in_(names))
           .order_by(UploadTracking.account_id, UploadTracking.id))

    if sweep is None:
        return q.all()
    return [r for r in q.all()
            if r.listing_checked_at is None
            or r.listing_checked_at < sweep.started_at]


def active(db: Session) -> Optional[ListingSweep]:
    """The sweep in progress, if any. One at a time."""
    return (db.query(ListingSweep)
              .filter(~ListingSweep.status.in_(FINISHED))
              .order_by(ListingSweep.id.desc()).first())


def next_chunk(db: Session, sweep: ListingSweep, size: int) -> list[dict]:
    """
    The next batch of addresses for the worker machine.

    Chunked rather than sent as one long job because the worker runs ONE job
    at a time: an hour-long job would stop Photoshop and the uploads for an
    hour, which this has no right to do — it changes nothing and holds
    nothing. A few minutes at a time lets real work slot in between.

    Addresses are built HERE, not on the node. The node should not know how
    a marketplace spells its URLs; it is handed a list of addresses and
    reports back status codes, which is also what makes it trivial to point
    the same job at a different marketplace later.
    """
    artist_of = {a.id: a.artist_name for a in ready(db)[0]}
    out = []
    for row in sweepable(db, sweep):
        url = listing_url(row, artist_of.get(row.account_id, ""))
        if url is None:
            # No title stored, so no address can be built. Recorded as
            # unknown rather than skipped, or it would sit in the work list
            # for ever and the sweep could never finish.
            row.listing_status = "unknown"
            row.listing_http = None
            row.listing_checked_at = datetime.utcnow()
            continue
        out.append({"id": row.id, "url": url})
        if len(out) >= size:
            break
    return out


# ════════════════════════════════════════════════════════════════════════════
#  RECORDING WHAT CAME BACK
# ════════════════════════════════════════════════════════════════════════════

def verdict(http: Optional[int]) -> str:
    """
    Turn a status code into one of three answers.

    ════════════════════════════════════════════════════════════════════════
    "NOT THERE" AND "WE COULD NOT LOOK" ARE DIFFERENT ANSWERS
    ════════════════════════════════════════════════════════════════════════
    Only a 404 is evidence that a listing is gone. A 403, a 429, a 5xx or a
    timeout means we were blocked or the site had a moment, and collapsing
    those into "gone" would report healthy listings as copyright takedowns —
    thousands of them, on a screen the owner has no other way to check.

    Measured: the Linux server gets 403 for every one of these pages, live
    or missing alike, so this is not hypothetical. It is the same shape as
    TeePublic's header-logo test, which exists to tell "no search results"
    apart from "we never actually looked".
    """
    if http == 200:
        return "live"
    if http == 404:
        return "gone"
    return "unknown"


def record(db: Session, results: list[dict]) -> dict:
    """Write one chunk's observations. Returns the tally for the log."""
    tally = {"live": 0, "gone": 0, "unknown": 0, "missing_row": 0}
    now = datetime.utcnow()
    for item in results:
        row = db.query(UploadTracking).filter_by(id=item.get("id")).first()
        if row is None:
            tally["missing_row"] += 1
            continue
        http = item.get("http")
        row.listing_http = http if isinstance(http, int) else None
        row.listing_status = verdict(row.listing_http)
        row.listing_checked_at = now
        tally[row.listing_status] += 1
    return tally


# ════════════════════════════════════════════════════════════════════════════
#  THE INVARIANT THAT STOPS A WRONG ARTIST NAME BECOMING A CATASTROPHE
# ════════════════════════════════════════════════════════════════════════════

def artist_name_suspect(db: Session, sweep: ListingSweep) -> list[dict]:
    """
    Accounts where so much reads GONE that the address is the likelier fault.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS
    ════════════════════════════════════════════════════════════════════════
    The whole address is built from a name typed in by hand. One wrong
    character and EVERY listing on that account returns 404 — and the screen
    would report thousands of copyright takedowns, confidently, with no way
    for the owner to tell it was nonsense.

    So this is stated as a property of the RESULT, not of the process: a
    sweep in which nearly everything on one account is gone is a broken
    sweep until proven otherwise. It fires whatever the cause — a typo, a
    renamed shop, FAA changing its address format — including causes nobody
    has thought of.

    It deliberately does NOT assert which it is. An account really can lose
    everything: that is what a ban looks like. The screen says both readings
    and asks for one listing to be opened by hand, which settles it in ten
    seconds.
    """
    from .pipeline import get_setting

    floor = int(get_setting(db, "listing_check_min_sample"))
    ratio = float(get_setting(db, "listing_check_alarm_ratio"))

    out = []
    for account in ready(db)[0]:
        rows = [r for r in db.query(UploadTracking)
                              .filter(UploadTracking.account_id == account.id,
                                      UploadTracking.listing_checked_at.isnot(None))
                              .all()
                if r.listing_checked_at >= sweep.started_at]
        if len(rows) < floor:
            continue
        gone = sum(1 for r in rows if r.listing_status == "gone")
        if gone / len(rows) < ratio:
            continue
        sample = next((r for r in rows if r.listing_status == "gone"), None)
        out.append({
            "account_id": account.id,
            "account": account.name,
            "artist_name": account.artist_name,
            "gone": gone,
            "checked": len(rows),
            "example_url": listing_url(sample, account.artist_name) if sample else "",
        })
    return out


# ════════════════════════════════════════════════════════════════════════════
#  FINDINGS
# ════════════════════════════════════════════════════════════════════════════

def findings(db: Session, limit: int = 500) -> dict:
    """
    The disagreements, grouped by what each one means.

    Only rows we have actually looked at appear. "Not yet checked" is not a
    finding, and mixing the two would inflate every number on the screen.
    """
    # Both looked up ONCE. The first version called accounts() inside the
    # row loop, which is a database query per row — fine against a handful
    # of test rows and 2,000 queries against the real 4,811. The kind of
    # thing that only shows up on the machine where it matters.
    every = accounts(db)
    names = {a.id: a.name for a in every}
    artists = {a.id: (a.artist_name or "") for a in every}

    checked = (db.query(UploadTracking)
                 .filter(UploadTracking.target_site == MARKETPLACE,
                         UploadTracking.listing_checked_at.isnot(None))
                 .all())

    def pack(rows):
        return [{
            "id": r.id,
            "title": r.remote_title or "(no title stored)",
            "account": names.get(r.account_id, f"#{r.account_id}"),
            "account_id": r.account_id,
            "project_id": r.project_id,
            "http": r.listing_http,
            "checked_at": r.listing_checked_at.isoformat()
                          if r.listing_checked_at else None,
            "url": listing_url(r, artists.get(r.account_id, "")) or "",
        } for r in rows[:limit]]

    # We believe it is up; the marketplace returns a real 404.
    gone = [r for r in checked
            if r.status == "uploaded" and r.listing_status == "gone"]
    # Live, and we already knew it was taken down. Not a finding — the
    # opposite: a row somebody explained and that is now back. Worth seeing.
    back = [r for r in checked
            if r.status == "removed" and r.listing_status == "live"]
    # We could not look. Never presented as evidence of anything.
    unknown = [r for r in checked
               if r.listing_status == "unknown" and r.status == "uploaded"]
    # Marked uploaded with nothing processed behind it — impossible, so the
    # data is wrong rather than the marketplace.
    impossible = [r for r in checked
                  if r.status == "uploaded" and r.processed_image_id is None]

    return {
        "gone": pack(gone), "gone_total": len(gone),
        "back": pack(back), "back_total": len(back),
        "unknown": pack(unknown), "unknown_total": len(unknown),
        "impossible": pack(impossible), "impossible_total": len(impossible),
    }


def counts(db: Session, sweep: Optional[ListingSweep] = None) -> dict:
    """Figures for the screen. All derived, none stored."""
    rows = (db.query(UploadTracking)
              .filter(UploadTracking.target_site == MARKETPLACE,
                      UploadTracking.status.in_(CLAIMS_LIVE)).all())
    have, missing = ready(db)
    scope = {a.id for a in have}

    in_scope = [r for r in rows if r.account_id in scope]
    this_run = ([r for r in in_scope
                 if r.listing_checked_at and sweep
                 and r.listing_checked_at >= sweep.started_at]
                if sweep else [])

    return {
        # Everything we believe is on the marketplace, including accounts we
        # cannot sweep — so the two numbers below can be compared honestly.
        "claimed":     len(rows),
        "in_scope":    len(in_scope),
        "no_artist":   len(rows) - len(in_scope),
        "accounts_ready":   len(have),
        "accounts_blocked": [a.name for a in missing],
        "checked_this_run": len(this_run),
        "run_total":   len(this_run) + len(sweepable(db, sweep)) if sweep else 0,
        "live":        sum(1 for r in in_scope if r.listing_status == "live"),
        "gone":        sum(1 for r in in_scope if r.listing_status == "gone"),
        "unknown":     sum(1 for r in in_scope if r.listing_status == "unknown"),
        "never":       sum(1 for r in in_scope if r.listing_checked_at is None),
    }


def finish(db: Session, sweep: ListingSweep, *, status: str,
           note: Optional[str] = None) -> None:
    """End a sweep, however it ended. One exit, so none can be forgotten."""
    sweep.status = status
    sweep.note = note
    sweep.finished_at = datetime.utcnow()


def should_stop(db: Session, sweep_id: Optional[int]) -> bool:
    """
    Should the worker machine stop mid-chunk?

    Asked on every progress report, because that is the only channel a node
    has — it cannot hear a button, only an answer to a question it was
    already asking. Derived from the sweep's state rather than a flag, so
    there is no second edge to lose.
    """
    if sweep_id is None:
        return True
    sweep = db.query(ListingSweep).filter_by(id=sweep_id).first()
    return sweep is None or sweep.status in FINISHED
