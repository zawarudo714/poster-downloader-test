"""
Brave image search — the source stage for projects that search in-page.

════════════════════════════════════════════════════════════════════════════
WHY THE SEARCH LIVES INSIDE THE SITE
════════════════════════════════════════════════════════════════════════════
An external-source project sends its workers to another tab: find a picture,
copy the URL, paste it back. That works when the site is one canonical source
with predictable URLs.

Celebrity photos have no such source. Doing it by hand meant searching Brave,
eyeballing a grid, and right-click-saving — which is a lot of tab-switching
per artist, and produces no record of what was rejected. Pulling the grid
into the page removes the switching, and because the server does the fetching
we get validation, format conversion and a dimension filter for free.

════════════════════════════════════════════════════════════════════════════
TWO KEYS, AND WHY THEY ARE NOT INTERCHANGEABLE
════════════════════════════════════════════════════════════════════════════
    free  ·  1 request/second   ·  2,000/month
    paid  ·  20 requests/second ·  metered, ~$0.005/query

Normal searches use the FREE key. Deep searches use the PAID one — not to
spread the cost, but because a deep search fires two queries at once and the
free key's 1/second ceiling would reject the second one. Spending half a cent
beats making the worker wait a second and then explaining a 429 to them.

The paid key is also the fallback when the free monthly quota runs out, and
when two workers happen to search within the same second.

════════════════════════════════════════════════════════════════════════════
DEEP SEARCH IS GONE — REMOVED 2026-09-06
════════════════════════════════════════════════════════════════════════════
It fired two queries and merged the results, which made sense when one
phrase could not serve both bands and solo artists. The travel project has
the owner's own phrasing buttons instead, and each one is a query he chose,
so a second automatic query added nothing a button could not do better.

THE PAID KEY STAYED. It was never only for deep search — it is the fallback
whenever the free key is inside its one-per-second window or has spent its
monthly allowance. Deleting it alongside deep search would have left a
worker looking at a rate-limit error with no way through.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import requests
from sqlalchemy.orm import Session

log = logging.getLogger("uvicorn.error")

API_URL = "https://api.search.brave.com/res/v1/images/search"
TIMEOUT_S = 12

# The free key allows one request per second. Tracked per process — good
# enough, since there is one web process and the consequence of being wrong
# is a spill to the paid key rather than an error.
_last_free_call = 0.0
_FREE_MIN_INTERVAL = 1.05


class BraveError(Exception):
    """Anything that stops a search returning results, with worker-safe text."""

    def __init__(self, message: str, *, worker_message: str | None = None):
        super().__init__(message)
        self.worker_message = worker_message or (
            "Image search is temporarily unavailable. Try refreshing the page "
            "or searching again — if it still doesn't work, tell the admin."
        )


@dataclass
class ImageResult:
    url: str                 # what we will actually download
    thumb: str               # what the grid displays
    width: int
    height: int
    source: str = ""         # page the image was found on, for context
    title: str = ""

    def as_dict(self) -> dict:
        return {
            "url": self.url, "thumb": self.thumb,
            "width": self.width, "height": self.height,
            "source": self.source, "title": self.title,
        }


@dataclass
class SearchOutcome:
    results: list[ImageResult] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    key_used: str = ""
    filtered_small: int = 0
    # Dropped for saying "map" or "flag" in their own title. Counted rather
    # than merely done, because a number that vanishes with no explanation
    # is how a filter gets blamed for a thin set of results.
    filtered_junk: int = 0
    # How many of what SURVIVED actually name the place. The screen uses it
    # to offer "N hidden that do not mention Kisumu — show them".
    on_topic: int = 0
    # How many were hidden for naming a DIFFERENT region — "Newcastle Beach
    # Australia" on a search for the South African one. A separate count so
    # the screen can say WHY they are hidden rather than lumping them in
    # with the merely-untitled.
    off_place: int = 0


# ── Settings ────────────────────────────────────────────────────────────────

def _setting(db: Session, key: str, project=None):
    from .pipeline import get_setting
    return get_setting(db, key, project=project)


def build_queries(db, artist: str, *, project=None, kind: str = "",
                  template: str | None = None) -> list[str]:
    """
    Render the configured query templates for one master title.

    The name is normalised for SEARCH only — never for storage or for the
    marketplace listing. Real sheets are full of characters nobody types into
    a search box: U+2010 HYPHEN rather than an ordinary one ("blink‐182"),
    curly apostrophes ("Guns N’ Roses"), en dashes in place-name ranges.
    Searching those verbatim returns nothing, so the title would be silently
    skipped as "not found".

    ════════════════════════════════════════════════════════════════════════
    TWO PLACEHOLDERS, ONE MEANING — AND WHY
    ════════════════════════════════════════════════════════════════════════
    `{title}` is the name to use. `{artist}` is accepted as an alias because
    it was the original spelling, back when the only in-page project searched
    for musicians.

    That was a niche word baked into shared code: a travel project asks about
    a mountain, and telling its operator to type `{artist}` to mean "Mount
    Fuji" is the same defect as an "Open <somewhere>" button on a project
    that has never touched that somewhere.

    Both are substituted rather than one being migrated, because a template
    is a string typed by a person into a settings box. Renaming the only
    placeholder would leave every saved query silently searching for the
    literal text "{artist}" — which returns nothing, reports nothing, and
    looks exactly like a project with no results.
    """
    # A phrasing button hands its own sentence in directly. Everything else
    # reads the setting. The caller passes the WORDS rather than a button
    # number, so nothing in here has to know the buttons exist.
    if template is not None:
        raw = str(template)
    else:
        raw = str(_setting(db, "brave_query_normal", project) or "")

    clean = normalise_for_search(artist)
    kind_clean = normalise_for_search(kind or "")

    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        line = (line.replace("{title}", clean)
                    .replace("{artist}", clean)
                    .replace("{kind}", kind_clean))
        # A title with no kind leaves "{kind}" as nothing, which would
        # otherwise leave a double space in the middle of the query. Harmless
        # to Brave, but it makes the query printed on screen look broken.
        out.append(" ".join(line.split()))
    return out


_SEARCH_FOLD = {
    "’": "'", "‘": "'",           # curly single quotes
    "“": '"', "”": '"',           # curly double quotes
    "‐": "-", "‑": "-",           # unicode hyphens
    "‒": "-", "–": "-", "—": "-",   # figure/en/em dashes
    "…": "...",
    " ": " ",
}


def normalise_for_search(name: str) -> str:
    """Typographic punctuation to what a person would actually type."""
    import unicodedata
    text = unicodedata.normalize("NFC", name or "")
    for bad, good in _SEARCH_FOLD.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


# ── Judging a result by its own title ───────────────────────────────────────
#
# Every result Brave returns carries the title of the page it was found on,
# and until 2026-09-10 we kept it and used it for nothing. Two questions can
# be answered from it for free, with no extra request and no extra money:
# is this a map rather than a photograph, and does it mention the place at
# all. That is the whole mechanism.


def _excluded(title: str, words: list[str]) -> bool:
    """
    Does this result's own title say it is not a photograph of the place?

    WHOLE WORDS ONLY. A substring test would drop Flagstaff for saying flag
    and Mapungubwe for saying map — real places, thrown away by a filter
    nobody could see working. The boundaries also refuse a neighbouring
    hyphen, so "map-reading" is still a map.
    """
    low = (title or "").lower()
    return any(re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", low)
               for w in words if w)


def _fold_for_match(text: str) -> str:
    """
    Accents off, lower case, for comparing two strings that mean the place.

    ITS OWN FUNCTION RATHER THAN BORROWING `clean_for_marketplace`. That one
    answers "what will FineArtAmerica store", which is a different question
    that also truncates at 100 characters and deletes punctuation. Reusing it
    here would tie two unrelated rules together, and the day the marketplace
    changed its folding the search ranking would move for no reason. Reuse
    the CONDITION that is genuinely shared, never the function that happens
    to contain it.

    Without this, "Church of San Ginés" never matched a page calling it
    "Iglesia de San Gines" — caught by exercising it rather than by reading
    it (2026-09-10).
    """
    stripped = unicodedata.normalize("NFKD", normalise_for_search(text or ""))
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def _mentions_place(title: str, place_words: list[str]) -> bool:
    """
    Does this result name the place we asked about?

    ════════════════════════════════════════════════════════════════════════
    AN UNKNOWN TITLE COUNTS AS A MATCH, ON PURPOSE
    ════════════════════════════════════════════════════════════════════════
    Some results come back with no title at all. Reading that as "does not
    mention the place" would hide them, and on a title where most results
    are untitled the grid would look broken — a guard firing on the normal
    case, which the notes call a keystroke rather than a guard.

    ANY significant word is enough, not the whole phrase. "Church of San
    Ginés, Madrid" would otherwise demand that exact sentence; matching on
    "Ginés" finds the Spanish page that calls it "Iglesia de San Ginés".
    Being forgiving is the cheap direction to be wrong in, because this
    decides the ORDER and an escape hatch shows the rest anyway.
    """
    if not place_words:
        return True
    low = _fold_for_match(title)
    if not low:
        return True                      # untitled is unknown, not off-topic
    return any(w in low for w in place_words)


# Words too common to identify a place. "Church of San Martín" must not match
# every church in the world, and "Lake Como" must not match every lake.
_PLACE_STOPWORDS = {
    "the", "of", "and", "at", "in", "on", "de", "la", "le", "el", "du", "des",
    "saint", "st", "mount", "mt", "lake", "river", "church", "cathedral",
    "castle", "city", "town", "island", "national", "park", "bridge", "falls",
    "old", "new", "great", "north", "south", "east", "west", "upper", "lower",
}


def place_words(title: str) -> list[str]:
    """
    The words that identify this place, from the part before the comma.

    THE OWNER'S OWN RULE (2026-09-10): "everything before the comma, since
    adding the country would dilute the results". `Kisumu, Kenya` gives
    Kisumu; the country would have matched every Kenyan picture there is.

    Taken from the TITLE, not from the search words — the sheet holds
    `Curaçao, Netherlands` as the title and `Curaçao Netherlands` as the
    search text, and only one of those has a comma to cut at.
    """
    head = (title or "").split(",")[0]
    out = []
    for w in re.findall(r"[^\W\d_]+", _fold_for_match(head)):
        if len(w) > 3 and w not in _PLACE_STOPWORDS:
            out.append(w)
    # Nothing distinctive left — "Old Town" is all stopwords. Fall back to
    # the longest word rather than matching everything, which would rank at
    # random and be indistinguishable from the feature being off.
    if not out:
        words = re.findall(r"[^\W\d_]+", _fold_for_match(head))
        out = [max(words, key=len)] if words else []
    return out


def place_phrase(title: str) -> str:
    """
    The whole name before the comma, folded — the strongest possible match.

    "New York City" has only one distinctive word once "new" and "city" are
    set aside, so it ranked on "york" alone and a photograph of York Minster
    would have come first. The phrase settles that: a result naming the whole
    thing outranks one that shares a single word, and the single word still
    beats no match at all.
    """
    return " ".join(_fold_for_match((title or "").split(",")[0]).split())


def region_phrase(title: str) -> str:
    """
    The part AFTER the comma, folded — the country or region that tells two
    places with one name apart. Empty when the title has no comma.
    """
    parts = (title or "").split(",", 1)
    if len(parts) < 2:
        return ""
    return " ".join(_fold_for_match(parts[1]).split())


# ── Knowing when a caption names a DIFFERENT place ──────────────────────────
#
# The sheet holds SIXTEEN Newcastles. Searching "Newcastle, South Africa"
# returns pages captioned "Newcastle Beach Australia", which NAME the place
# word — so the ranking filed them as perfect matches and put them first
# (owner's find, 2026-09-10). Brave cannot be told "-australia"; the minus
# operator is measured dead. So the contradiction is read out of the caption
# instead: a result that names the place AND names a different region than
# the title's is ranked off the grid, behind the existing SHOW THEM escape.
#
# WHERE THE REGION LIST COMES FROM — THE SHEET ITSELF, never a hand-kept
# list. Every title's after-comma part is a qualifier; the ones used by many
# titles are countries and states, the ones used by one or two are cities
# ("Table Mountain, Cape Town"). Only the frequent ones may contradict:
# MEASURED 2026-09-10 on the real 88,112 rows, a cut at 20 titles keeps 271
# region-level names (Germany, Texas, New South Wales) and drops every
# city-level qualifier — so a caption saying "Newcastle near Durban" is NOT
# hidden, because Durban never makes the list.
_REGION_MIN_TITLES = 20

# Cache per project, keyed by how many comma-titles the project holds — the
# count is one cheap query per search, and a re-import changes it, so the
# list rebuilds itself with nothing to remember to clear.
_region_cache: dict = {}


def known_regions(db: Session, project) -> tuple[str, ...]:
    """
    The folded after-comma qualifiers this project's sheet uses 20+ times.
    """
    from sqlalchemy import func
    from .models import MasterTitle
    from .pipeline import project_scope, _default_project_id

    scope = project_scope(getattr(project, "id", None),
                          default_project_id=_default_project_id(db))
    rows = (db.query(MasterTitle.title)
              .filter(scope, MasterTitle.title.contains(",")))
    count = rows.count()

    key = getattr(project, "id", None)
    hit = _region_cache.get(key)
    if hit and hit[0] == count:
        return hit[1]

    freq: dict[str, int] = {}
    for (name,) in rows.all():
        q = region_phrase(name)
        if q:
            freq[q] = freq.get(q, 0) + 1
    regions = tuple(sorted(q for q, n in freq.items()
                           if n >= _REGION_MIN_TITLES))
    _region_cache[key] = (count, regions)
    return regions


def _contains_phrase(folded_text: str, folded_phrase: str) -> bool:
    """Whole words only — "flag" inside "Flagstaff" must not count."""
    return re.search(r"(?<![a-z0-9])" + re.escape(folded_phrase)
                     + r"(?![a-z0-9])", folded_text) is not None


def foreign_regions_for(display_title: str,
                        regions: tuple[str, ...]) -> tuple[str, ...]:
    """
    The regions that would CONTRADICT this title, from the known set.

    A region sharing any word with the title itself is left out — for
    "Newcastle, Washington" the region "washington" is ours, not foreign,
    and for "Newcastle, New South Wales" even "New York" is skipped for
    sharing "new". Over-keeping is the safe direction: a skipped region can
    only ever leave a result VISIBLE.
    """
    ours = set(_fold_for_match(display_title or "").split())
    return tuple(q for q in regions if not (set(q.split()) & ours))


def rank_tier(result_title: str, *, phrase: str, wanted: list[str],
              our_region: str, foreign: tuple[str, ...]) -> int:
    """
    One result's place in the grid, smallest first.

      0  names the whole place              "Newcastle South Africa town"
      1  shares a distinctive word          "Newcastle at dusk"
      2  names neither                      "Sunset over the beach"
      3  names the place AND a different    "Newcastle Beach Australia"
         region than the title's — hidden
         behind SHOW THEM with tier 2

    The contradiction check runs only when the title HAS a region of its
    own. "New York City" carries no comma, so nothing can contradict it and
    a caption saying "Little Italy, New York" stays exactly where it was.
    A caption that mentions OUR region can never conflict, whatever else it
    also says — a "Newcastle SA vs Newcastle NSW" page names both and is
    evidence for us, not against.

    A pure function on purpose: `tools/test_brave_ranking.py` feeds it the
    real Newcastle captions and fails the deploy if this table ever stops
    being true.
    """
    low = " ".join(_fold_for_match(result_title or "").split())
    if our_region and low and not _contains_phrase(low, our_region):
        for q in foreign:
            if _contains_phrase(low, q):
                return 3
    if phrase and low and phrase in low:
        return 0
    return 1 if _mentions_place(result_title, wanted) else 2


# ── The HTTP call ───────────────────────────────────────────────────────────

def _call(api_key: str, query: str, count: int) -> list[dict]:
    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": min(int(count or 50), 100), "safesearch": "off"}
    try:
        resp = requests.get(API_URL, headers=headers, params=params, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise BraveError(f"network error talking to Brave: {e}")

    if resp.status_code == 429:
        raise _RateLimited(f"Brave rate limit on query {query!r}")
    if resp.status_code in (401, 403):
        raise BraveError(
            f"Brave rejected the API key (HTTP {resp.status_code})",
            worker_message="Image search isn't configured correctly. Tell the admin.",
        )
    if resp.status_code != 200:
        raise BraveError(f"Brave returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json().get("results") or []
    except ValueError:
        raise BraveError("Brave returned a response that wasn't JSON")


class _RateLimited(Exception):
    """Internal: the free key is busy or exhausted. Spill to the paid one."""


def _parse(raw: list[dict], min_dimension: int) -> tuple[list[ImageResult], int]:
    """
    Turn Brave's payload into results, dropping anything too small to be
    worth a worker's time or an AI's attention.

    The rule is BOTH sides under the threshold — 299x500 is a usable portrait
    crop, 299x299 is a thumbnail of a thumbnail. Filtering here rather than in
    the browser means the worker never sees an image they can't use, and we
    never pay to download one.
    """
    out: list[ImageResult] = []
    dropped = 0
    for item in raw:
        props = item.get("properties") or {}
        thumb = (item.get("thumbnail") or {}).get("src") or ""
        url = props.get("url") or thumb
        if not url:
            continue
        w = int(props.get("width") or item.get("width") or 0)
        h = int(props.get("height") or item.get("height") or 0)
        if w and h and w < min_dimension and h < min_dimension:
            dropped += 1
            continue
        out.append(ImageResult(
            url=url,
            thumb=thumb or url,
            width=w, height=h,
            source=item.get("url") or "",
            title=(item.get("title") or "")[:200],
        ))
    return out, dropped


# ── Public entry point ──────────────────────────────────────────────────────

def search(db: Session, artist: str, *, project=None, kind: str = "",
           template: str | None = None,
           display_title: str = "") -> SearchOutcome:
    """
    Run a normal or deep search and return de-duplicated results.

    Key routing, in order:
      · deep search              -> paid key (needs burst capacity)
      · normal search            -> free key
      · free key busy/exhausted  -> paid key, once

    Raises BraveError with worker-safe text when there is no way to answer.
    """
    global _last_free_call

    from .pipeline import get_secret
    free_key = get_secret(db, "brave_api_key_free", project=project)
    paid_key = get_secret(db, "brave_api_key_paid", project=project)
    if not (free_key or paid_key):
        raise BraveError(
            "No Brave API key configured",
            worker_message="Image search isn't set up yet. Tell the admin.",
        )

    per_query = int(_setting(db, "brave_results_per_query", project) or 50)
    min_dim = int(_setting(db, "brave_min_dimension", project) or 300)
    queries = build_queries(db, artist, project=project, kind=kind,
                            template=template)
    if not queries:
        raise BraveError("No search query template configured")

    seen: set[str] = set()
    merged: list[ImageResult] = []
    filtered = 0
    junk = 0
    key_used = ""
    exclude = [w.strip().lower() for w in
               str(_setting(db, "brave_exclude_words", project) or "").split(",")
               if w.strip()]

    for query in queries:
        raw = None
        if free_key:
            try:
                wait = _FREE_MIN_INTERVAL - (time.time() - _last_free_call)
                if wait > 0:
                    # Another worker searched within the last second. Rather
                    # than making this one wait, spill to the paid key — it
                    # costs half a cent and nobody sees a delay.
                    raise _RateLimited("free key inside its 1/second window")
                _last_free_call = time.time()
                raw = _call(free_key, query, per_query)
                key_used = "free"
            except _RateLimited:
                raw = None
        if raw is None:
            # THE PAID KEY IS STILL LOAD-BEARING, EVEN THOUGH DEEP SEARCH IS
            # GONE. It was never only for deep search: it is what answers a
            # worker when the free key is inside its one-per-second window or
            # has spent its monthly two thousand. Removing it with deep search
            # would have left a worker staring at a rate-limit error.
            if not paid_key:
                raise BraveError("Brave free key is rate limited and no paid key is set")
            raw = _call(paid_key, query, per_query)
            key_used = "paid"

        parsed, dropped = _parse(raw, min_dim)
        filtered += dropped
        for r in parsed:
            if r.url in seen:
                continue          # a multi-line phrasing can overlap itself
            seen.add(r.url)
            if _excluded(r.title, exclude):
                junk += 1
                continue          # a map or a flag, by its own title
            merged.append(r)

    # ── RANK, DO NOT REMOVE ────────────────────────────────────────────────
    #
    # The owner's choice on 2026-09-10, and it is the right way round: a
    # genuinely good photograph on a Kisumu page may be titled "Sunset over
    # the lake" and naming the place is not its job. Nothing is thrown away
    # for failing to mention it — the ones that DO mention it simply come
    # first, and the screen offers the rest behind "N hidden — show them".
    #
    # A STABLE sort, so within each group Brave's own ordering survives. It
    # ranked them by relevance already, and shuffling that would throw away
    # the one judgement the search engine is actually good at.
    # THE DISPLAY TITLE, NOT THE SEARCH WORDS. The sheet holds
    # "Curaçao, Netherlands" as the title and "Curaçao Netherlands" as the
    # search text, and only the first has a comma to cut the country off at.
    # Ranking on the search words would have matched every Dutch picture
    # there is — the exact dilution the owner warned about. Falls back to
    # the search words so a caller that has no title still ranks somehow.
    wanted = place_words(display_title or artist)
    phrase = place_phrase(display_title or artist)
    our_region = region_phrase(display_title or "")
    foreign: tuple[str, ...] = ()
    if our_region:
        try:
            foreign = foreign_regions_for(display_title,
                                          known_regions(db, project))
        except Exception:
            foreign = ()          # a scoping hiccup must not kill the search

    # One tier per result, computed once — see rank_tier() for the table.
    tiers = {id(r): rank_tier(r.title, phrase=phrase, wanted=wanted,
                              our_region=our_region, foreign=foreign)
             for r in merged}
    on_topic = sum(1 for t in tiers.values() if t < 2)
    off_place = sum(1 for t in tiers.values() if t == 3)
    merged.sort(key=lambda r: tiers[id(r)])

    return SearchOutcome(results=merged, queries=queries,
                         key_used=key_used, filtered_small=filtered,
                         filtered_junk=junk, on_topic=on_topic,
                         off_place=off_place)
