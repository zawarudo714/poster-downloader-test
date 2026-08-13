"""
Brave image search — the source stage for projects that search in-page.

════════════════════════════════════════════════════════════════════════════
WHY THE SEARCH LIVES INSIDE THE SITE
════════════════════════════════════════════════════════════════════════════
The movie project sends its workers to TMDB in another tab: find a poster,
copy the URL, paste it back. That works because TMDB is one canonical source
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
WHY DEEP SEARCH RUNS TWO QUERIES
════════════════════════════════════════════════════════════════════════════
No single phrase serves both bands and solo artists. `"U2" musician` returns
Bono alone; `"Kanye West" band` returns nothing useful. Running both and
merging costs about half a cent and needs no cleverness — the worker's eye
discards whichever half is wrong, which it would do anyway.
"""

from __future__ import annotations

import logging
import time
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


# ── Settings ────────────────────────────────────────────────────────────────

def _setting(db: Session, key: str, project=None):
    from .pipeline import get_setting
    return get_setting(db, key, project=project)


def build_queries(db, artist: str, *, deep: bool, project=None) -> list[str]:
    """
    Render the configured query templates for one artist.

    The artist name is normalised for SEARCH only — never for storage or for
    the marketplace listing. 271 names in the database contain U+2010 HYPHEN
    rather than an ordinary one ("blink‐182"), and 548 contain a curly
    apostrophe ("Guns N’ Roses"). Those are not the strings a human types
    into a search box, and searching them verbatim returns nothing, so those
    artists would be silently skipped as "not found".
    """
    key = "brave_query_deep" if deep else "brave_query_normal"
    raw = str(_setting(db, key, project) or "")
    clean = normalise_for_search(artist)
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            out.append(line.replace("{artist}", clean))
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

def search(db: Session, artist: str, *, deep: bool = False, project=None) -> SearchOutcome:
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
    queries = build_queries(db, artist, deep=deep, project=project)
    if not queries:
        raise BraveError("No search query template configured")

    seen: set[str] = set()
    merged: list[ImageResult] = []
    filtered = 0
    key_used = ""

    for query in queries:
        raw = None
        # Deep searches go straight to the paid key: two queries fired
        # together would trip the free key's one-per-second ceiling.
        if deep and paid_key:
            raw = _call(paid_key, query, per_query)
            key_used = "paid"
        else:
            if free_key:
                try:
                    wait = _FREE_MIN_INTERVAL - (time.time() - _last_free_call)
                    if wait > 0:
                        # Another worker searched within the last second. Rather
                        # than making this one wait, spill to the paid key —
                        # it costs half a cent and nobody sees a delay.
                        raise _RateLimited("free key inside its 1/second window")
                    _last_free_call = time.time()
                    raw = _call(free_key, query, per_query)
                    key_used = "free"
                except _RateLimited:
                    raw = None
            if raw is None:
                if not paid_key:
                    raise BraveError("Brave free key is rate limited and no paid key is set")
                raw = _call(paid_key, query, per_query)
                key_used = "paid"

        parsed, dropped = _parse(raw, min_dim)
        filtered += dropped
        for r in parsed:
            if r.url in seen:
                continue          # the two deep queries overlap heavily
            seen.add(r.url)
            merged.append(r)

    return SearchOutcome(results=merged, queries=queries,
                         key_used=key_used, filtered_small=filtered)
