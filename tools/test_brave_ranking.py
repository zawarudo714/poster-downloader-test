"""
Behaviour tests for the search-result ranking — the four-tier table in
`brave_search.rank_tier`.

Run inside the container:

    docker compose exec web python tools/test_brave_ranking.py

════════════════════════════════════════════════════════════════════════════
WHY THESE TESTS
════════════════════════════════════════════════════════════════════════════
The ranking is a DECISION computed on the owner's behalf, and every way it
can be wrong is silent: a grid full of the wrong Newcastle looks exactly
like a grid full of the right one. The sheet holds sixteen Newcastles and
3,324 place names used by more than one row (measured 2026-09-10), so the
ambiguity is systemic, not one unlucky town.

Each case below is a way the ranking has actually been wrong, or a way the
conflict check could over-fire and hide a good result. The over-firing
cases matter MOST: hiding the right place's pictures is the one failure
worse than showing the wrong place's.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brave_search import (rank_tier, place_words, place_phrase,
                              region_phrase, foreign_regions_for)

# A region list like the real one — the frequent qualifiers of the sheet.
REGIONS = ("australia", "england", "new south wales", "northern ireland",
           "scotland", "south africa", "washington", "wyoming", "germany",
           "india", "california", "new york")

FAILURES: list[str] = []


def check(title: str, caption: str, expect: int, why: str) -> None:
    phrase = place_phrase(title)
    wanted = place_words(title)
    ours = region_phrase(title)
    foreign = foreign_regions_for(title, REGIONS)
    got = rank_tier(caption, phrase=phrase, wanted=wanted,
                    our_region=ours, foreign=foreign)
    if got != expect:
        FAILURES.append(f"{title!r} vs {caption!r}: expected tier {expect} "
                        f"({why}), got {got}")


T = "Newcastle, South Africa"
# The owner's own screenshot, 2026-09-10 — these were at the TOP of the grid.
check(T, "Newcastle Beach Australia at sunset. Newcastle is Australia's",
      3, "names the place and a different region — hide it")
check(T, "Newcastle ANZAC Memorial Walk NSW Australia Newcastle", 3,
      "same, via Australia written out")
# The right place must never be hidden.
check(T, "Newcastle South Africa town aerial view", 0,
      "names the whole place and our region")
# For a ONE-WORD place the phrase IS the word, so tiers 0 and 1 collapse —
# any caption saying "Newcastle" without a foreign region ranks top.
check(T, "Newcastle, KwaZulu-Natal", 0,
      "our province is not a known region, so no conflict — stays visible")
check(T, "Newcastle at dusk", 0, "no region named at all — neutral, visible")
check(T, "Sunset over the escarpment", 2, "names nothing — hidden as before")
check(T, "Newcastle near Durban", 0,
      "Durban is a city, never in the region list — must stay visible")
check(T, "Newcastle SA vs Newcastle NSW South Africa comparison", 0,
      "a caption naming OUR region can never conflict, whatever else it says")

# A title with no comma has no region, so nothing can contradict it — the
# caption ranks on its words alone ('york' is the one distinctive word).
check("New York City", "Little Italy, New York", 1,
      "no comma means the conflict check must stay out of it")

# Our own region must be struck from the foreign list, including by shared
# word — otherwise the title's own captions would hide themselves.
check("Newcastle, Washington", "Newcastle Washington lakefront", 0,
      "our region is ours, not foreign")
check("Newcastle, New South Wales", "Newcastle New York skyline", 0,
      "'new york' shares the word 'new' with our region, so it is skipped — "
      "over-keeping is the safe direction, and the caption stays visible")

# Tiers 0 and 1 match by SUBSTRING on purpose: a match can only promote a
# result into view, never hide one, and "Rothenburgstrasse" must still count
# as mentioning Rothenburg. So Perthshire promotes on "perth" — accepted.
# The CONFLICT tier is the opposite: hiding needs whole-word evidence, so a
# region inside a longer word never fires it.
check("Perth, Scotland", "Perthshire hills in autumn", 0,
      "substring promotion is deliberate — it can only ever SHOW a result")
check("Perth, Scotland", "Perth Australia city lights", 3,
      "the Australian Perth is hidden — whole-word region, ours absent")

if FAILURES:
    print("RANKING BEHAVIOUR — FAILED")
    for f in FAILURES:
        print("  ✗", f)
    sys.exit(1)
print("ranking behaviour: all cases pass")
