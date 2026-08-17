"""
Attributing a marketplace sale to one of our designs.

════════════════════════════════════════════════════════════════════════════
WHY THIS IS HARDER THAN IT LOOKS
════════════════════════════════════════════════════════════════════════════
The listing title on the marketplace is not always the title we stored, for
three unrelated reasons:

  1. THE MARKETPLACE REWRITES IT. FineArtAmerica deletes every character it
     does not accept and folds accents — "Guns N' Roses" lists as "Guns N
     Roses", "Beyoncé" as "Beyonce". Rows written before we started storing
     the normalised form still hold the original.

  2. IT TRUNCATES AT 100 CHARACTERS. Long titles are cut, and cut
     differently depending on when they were made — our renderer protects
     the suffix and trims the name at a word boundary, which earlier uploads
     did not do.

  3. THE OLDEST LISTINGS WERE UPLOADED BY HAND. The movie project's back
     catalogue predates all of this. It follows no rule we control.

So exact matching fails on precisely the oldest and most valuable data.

════════════════════════════════════════════════════════════════════════════
TIERS, AND THEN A HUMAN
════════════════════════════════════════════════════════════════════════════
Each tier is exact about something. None of them is fuzzy:

    alias   a correction you made once, stored forever
    exact   the normalised listing title equals a stored remote_title
    suffix  the per-image suffix (#A, - 1994 A) plus an exact name match
    name    the name alone, once the suffix is stripped

Anything left is UNMATCHED, and stays that way until you say otherwise.

There is deliberately no similarity scoring. A sale credited to the wrong
design is worse than a sale credited to nothing, because you would act on
it — make more of something that never sold. Unmatched is visible and
fixable; wrong is invisible and permanent.

One correction fixes every past and future sale of that design at once,
because the alias is stored against the marketplace's product name rather
than against the individual sale.
"""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    LedgerEntry, MasterTitle, SavedPoster, TitleAlias, UploadTracking,
)


# The trailing per-image marker our own templates produce:
#   "Rihanna #A"                 -> "#A"
#   "Pulp Fiction - 1994 A"      -> "1994 A"  (year + letter)
#   "Carla Bruni - 1"            -> "1"       (the old numeric form)
_SUFFIX_RE = re.compile(
    r"\s*(?:#\s*(?P<letter>[A-Z]\d*)"
    r"|[-–]\s*(?:(?P<year>\d{4})\s+)?(?P<letter2>[A-Z]\d*)"
    r"|[-–]\s*(?P<index>\d{1,2}))\s*$"
)


def normalise(text: Optional[str]) -> str:
    """
    Reduce a title to what two spellings of it have in common.

    Case, punctuation and spacing are removed — the same information the
    marketplace itself throws away. Accents are folded through the SAME
    table used when rendering a listing title, so "Beyoncé" and "Beyonce"
    land on one value rather than two.
    """
    from ..pipeline import clean_for_marketplace

    folded = clean_for_marketplace(text or "", max_length=10_000)
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def split_suffix(title: Optional[str]) -> tuple[str, str]:
    """
    "Rihanna #A" -> ("Rihanna", "A").  No suffix -> (title, "").

    The suffix is short and structured, which makes it the part most likely
    to survive truncation intact — so it is worth matching on separately
    when the name has been cut.
    """
    text = (title or "").strip()
    m = _SUFFIX_RE.search(text)
    if not m:
        return text, ""
    marker = m.group("letter") or m.group("letter2") or m.group("index") or ""
    return text[: m.start()].strip(), marker.strip()


class MatchIndex:
    """
    Every lookup the matcher needs, built ONCE per run.

    Without this, matching is quadratic: each sale would re-read every
    upload row and every master title, normalising as it went. On 101,605
    titles and a few thousand sales that is minutes of work to answer a
    question that should take milliseconds — and it would be paid again on
    every alias you add, because adding one re-matches the backlog.

    Built from the database and then read-only, so it is also the thing that
    makes the tiers testable without a database at all.
    """

    def __init__(self, db: Session, marketplace: str):
        self.marketplace = marketplace

        # Corrections you made. Keyed on the RAW name, because an alias is a
        # decision about a specific string rather than a derivation.
        self.aliases: dict[str, int] = {
            a.artwork_name: a.master_title_id
            for a in db.query(TitleAlias)
                       .filter(TitleAlias.marketplace == marketplace).all()
        }

        # What we actually sent to the marketplace -> which design it was.
        # The strongest signal there is, and it needs no reasoning about
        # suffixes or truncation.
        self.remote: dict[str, int] = {}
        rows = (
            db.query(UploadTracking.remote_title, SavedPoster.master_title_id)
              .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
              .filter(UploadTracking.remote_title.isnot(None))
              .all()
        )
        for remote_title, master_id in rows:
            if not remote_title or not master_id:
                continue
            key = normalise(remote_title)
            # A key claimed by two different designs is ambiguous and is
            # dropped rather than resolved by whichever came last.
            if key in self.remote and self.remote[key] != master_id:
                self.remote[key] = 0
            else:
                self.remote.setdefault(key, master_id)

        # Our own title list, for listings we never sent (the hand-uploaded
        # back catalogue) — mapped to a LIST so collisions stay visible.
        self.titles: dict[str, list[int]] = {}
        for tid, title in db.query(MasterTitle.id, MasterTitle.title).all():
            if not title:
                continue
            self.titles.setdefault(normalise(title), []).append(tid)

    def lookup(self, artwork_name: str) -> tuple[Optional[int], Optional[str]]:
        """
        Which design is this sale for?

        Returns (master_title_id, how) — or (None, None) when nothing
        matched exactly enough to be worth asserting.
        """
        if not artwork_name:
            return None, None

        # ── 1 · An alias you set. Nothing below may override it. ────────
        if artwork_name in self.aliases:
            return self.aliases[artwork_name], "alias"

        target = normalise(artwork_name)
        if not target:
            return None, None

        # ── 2 · The exact title we told the marketplace to use ──────────
        hit = self.remote.get(target)
        if hit:
            return hit, "exact"

        # ── 3 · The name alone, once the suffix is stripped ─────────────
        name, _marker = split_suffix(artwork_name)
        name_key = normalise(name)
        if not name_key:
            return None, None

        candidates = self.titles.get(name_key) or []
        if len(candidates) == 1:
            return candidates[0], "name"
        if len(candidates) > 1:
            # Two designs whose titles normalise identically. Refusing to
            # choose is the point: picking one would be a coin toss
            # recorded as a fact.
            return None, None

        # ── 4 · Truncated names ─────────────────────────────────────────
        # The marketplace cuts at 100 characters, so a stored title may
        # merely START with what we can see. Accepted only when exactly one
        # candidate matches and the visible part is long enough to mean
        # something — a short prefix would match half the library.
        if len(name_key) >= 20:
            prefixed = [
                ids[0] for key, ids in self.titles.items()
                if len(ids) == 1 and key.startswith(name_key)
            ]
            if len(prefixed) == 1:
                return prefixed[0], "suffix"

        return None, None


def find_match(
    db: Session, *, marketplace: str, artwork_name: str,
) -> tuple[Optional[int], Optional[str]]:
    """One-off lookup. Prefer MatchIndex directly when matching many rows."""
    return MatchIndex(db, marketplace).lookup(artwork_name)


def match_entry(db: Session, entry: LedgerEntry,
                index: Optional[MatchIndex] = None) -> bool:
    """Attribute one ledger row. True if it now points at a design."""
    if entry.entry_type not in ("sale", "refund") or not entry.artwork_name:
        return False
    idx = index or MatchIndex(db, entry.marketplace)
    title_id, how = idx.lookup(entry.artwork_name)
    if not title_id:
        return False
    entry.master_title_id = title_id
    entry.match_method = how
    return True


def rematch_unmatched(db: Session, *, marketplace: Optional[str] = None) -> int:
    """
    Try again on everything currently unattributed.

    Run after an alias is added — which is what makes one correction apply
    to every past sale of that design, not just the next one.
    """
    q = db.query(LedgerEntry).filter(
        LedgerEntry.master_title_id.is_(None),
        LedgerEntry.entry_type.in_(("sale", "refund")),
        LedgerEntry.artwork_name.isnot(None),
    )
    if marketplace:
        q = q.filter(LedgerEntry.marketplace == marketplace)

    entries = q.all()
    if not entries:
        return 0

    # One index per marketplace, reused across every row.
    indexes: dict[str, MatchIndex] = {}
    fixed = 0
    for entry in entries:
        idx = indexes.get(entry.marketplace)
        if idx is None:
            idx = indexes[entry.marketplace] = MatchIndex(db, entry.marketplace)
        if match_entry(db, entry, idx):
            fixed += 1
    return fixed


def add_alias(
    db: Session, *, marketplace: str, artwork_name: str,
    master_title_id: int, by: str = "",
) -> int:
    """
    Record a manual match and apply it everywhere.

    Returns how many previously unmatched rows it resolved — which is the
    useful feedback, because one correction on a design that has sold ten
    times fixes all ten.
    """
    existing = (
        db.query(TitleAlias)
          .filter(TitleAlias.marketplace == marketplace,
                  TitleAlias.artwork_name == artwork_name)
          .first()
    )
    if existing:
        existing.master_title_id = master_title_id
        existing.created_by = by or existing.created_by
    else:
        db.add(TitleAlias(marketplace=marketplace, artwork_name=artwork_name,
                          master_title_id=master_title_id, created_by=by))
    db.flush()
    return rematch_unmatched(db, marketplace=marketplace)


def unmatched_summary(db: Session, limit: int = 200) -> list[dict]:
    """
    The work queue: one entry per distinct product name, not per sale.

    Grouped because ten sales of one design are ONE decision. A list of
    individual sales would ask you the same question ten times.
    """
    from sqlalchemy import func

    rows = (
        db.query(
            LedgerEntry.marketplace,
            LedgerEntry.artwork_name,
            func.count(LedgerEntry.id).label("sales"),
            func.min(LedgerEntry.occurred_at).label("first_seen"),
        )
        .filter(LedgerEntry.master_title_id.is_(None),
                LedgerEntry.entry_type == "sale",
                LedgerEntry.artwork_name.isnot(None))
        .group_by(LedgerEntry.marketplace, LedgerEntry.artwork_name)
        .order_by(func.count(LedgerEntry.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {"marketplace": m, "artwork_name": name, "sales": int(n or 0),
         "first_seen": first.isoformat() if first else None}
        for m, name, n, first in rows
    ]
