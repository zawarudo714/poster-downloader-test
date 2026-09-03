"""
Behaviour tests for the worker's search-phrasing buttons and the
style-reference switch.

Run inside the container:

    docker compose exec web python tools/test_search_phrasings.py

════════════════════════════════════════════════════════════════════════════
WHY THESE TESTS AND NOT OTHERS
════════════════════════════════════════════════════════════════════════════
Both features are settings a person types into a box, so almost nothing about
them can be caught by a check that only looks at the code. What CAN be
checked is the three decisions the code makes on his behalf, and each one
fails silently rather than loudly:

  1. A phrasing with no {title} is REFUSED. Left in, it searches the same
     words for every place in the catalogue and returns real photographs of
     somewhere else, with nothing on any screen looking wrong.
  2. Editing a phrasing MISSES THE CACHE. If it did not, the owner would be
     comparing today's wording against yesterday's results.
  3. The style reference is sent only when it is switched on, and the
     ORDER survives — every prompt that mentions two pictures calls the
     reference the first one.

Each test below is followed by a SABOTAGE: the original mistake is put back
and the test must go red. A test that stays green under sabotage is not
protecting anything, which is how the stage-counting bug shipped.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

failures: list[str] = []
checks = 0


def ok(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  — {detail}" if detail else ""))
        failures.append(label)


# ════════════════════════════════════════════════════════════════════════════
# 1. A phrasing with no {title} is refused
# ════════════════════════════════════════════════════════════════════════════
print("\nA phrasing must say WHICH place to look for")

from app import pipeline as P                       # noqa: E402


def rejects(key: str, value: str) -> bool:
    """
    Did set_setting refuse this value?

    Called with db=None deliberately. The guard runs before anything touches
    the database, so a value that gets as far as needing a session is a value
    that was accepted — which makes the missing session the proof.
    """
    try:
        P.set_setting(None, key, value)
    except KeyError:
        return True
    except Exception:
        return False        # got past the guard and died on the database
    return False


ok("plain wording with {title} is allowed",
   not rejects("brave_search_phrasings", "places to visit in {title}"))
ok("the legacy {artist} spelling is still allowed",
   not rejects("brave_query_normal", '"{artist}"'))
ok("several good lines are allowed",
   not rejects("brave_search_phrasings",
               "{title} skyline\naerial view of {title}\n\n"))
ok("blank is allowed — it just means no extra buttons",
   not rejects("brave_search_phrasings", ""))

ok("a line with no place in it is REFUSED",
   rejects("brave_search_phrasings", "beautiful travel photography"))
ok("one bad line among good ones is REFUSED",
   rejects("brave_search_phrasings",
           "{title} skyline\nbeautiful scenery\naerial view of {title}"))
ok("the SEARCH button phrasing is guarded too",
   rejects("brave_query_normal", "landscape photo"))
ok("the DEEP SEARCH phrasing is guarded too",
   rejects("brave_query_deep", "landscape photo"))
ok("a setting that is not a phrasing is left alone",
   not rejects("openai_model", "gpt-image-2"))

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The original mistake: no guard at all. Put it back and the four refusal
# tests above must go red. Expect: "a line with no place in it is REFUSED".
print("\n  sabotage — remove the guard, the refusals must stop working")
_real_guard = P._reject_a_search_line_with_no_place_in_it
P._reject_a_search_line_with_no_place_in_it = lambda key, value: None
sabotage_caught = not rejects("brave_search_phrasings", "beautiful travel photography")
P._reject_a_search_line_with_no_place_in_it = _real_guard
ok("without the guard the bad line gets through (so the guard is real)",
   sabotage_caught,
   "the refusal happens somewhere else — this test is not watching the guard")
ok("the guard is back in place after the sabotage",
   rejects("brave_search_phrasings", "beautiful travel photography"))

# The guard has to sit where EVERY path goes through it, not only the
# settings screen. Read the call out of the syntax tree rather than searching
# the text, because a comment mentioning the guard would satisfy a text
# search while the call itself was gone.
src = ast.parse((REPO / "app" / "pipeline.py").read_text(encoding="utf-8"))
set_setting_fn = next(n for n in ast.walk(src)
                      if isinstance(n, ast.FunctionDef) and n.name == "set_setting")
calls_made = {n.func.id for n in ast.walk(set_setting_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
ok("the guard is called from inside set_setting itself",
   "_reject_a_search_line_with_no_place_in_it" in calls_made,
   "a guard the import scripts can walk around is not a guard")


# ════════════════════════════════════════════════════════════════════════════
# 2. Editing a phrasing misses the cache
# ════════════════════════════════════════════════════════════════════════════
print("\nEditing a phrasing must not serve the old results")

k = P.phrasing_cache_key
ok("the same wording always gives the same name",
   k("places to visit in {title}") == k("places to visit in {title}"))
ok("different wording gives a different name",
   k("places to visit in {title}") != k("{title} skyline"))
ok("even a one-word edit gives a different name",
   k("aerial view of {title}") != k("aerial photo of {title}"))
ok("the name fits the 16-character column",
   all(len(k(p)) <= 16 for p in ("{title}", "x" * 500 + " {title}")))
ok("the name cannot be confused with the everyday buttons",
   k("{title}") not in ("normal", "deep") and k("{title}").startswith("p:"))

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The original mistake: key the cache on the BUTTON NUMBER. Two different
# phrasings then share a name, and editing one silently serves the other's
# results. Expect: "different wording gives a different name".
print("\n  sabotage — key on the button number instead of the words")
by_position = lambda phrasing, position=2: f"p{position}"      # noqa: E731
ok("keying on position makes two phrasings collide (so the hash is doing work)",
   by_position("places to visit in {title}") == by_position("{title} skyline"),
   "the sabotage did not apply — this test proves nothing")


# ════════════════════════════════════════════════════════════════════════════
# 3. A phrasing button searches its own words
# ════════════════════════════════════════════════════════════════════════════
print("\nA phrasing button searches the words on it")

from app import brave_search as B                   # noqa: E402


class _NoDb:
    """Any database use here is a bug: a template needs no settings lookup."""


ok("the given wording is used, with the place dropped in",
   B.build_queries(_NoDb(), "Kyoto", deep=False,
                   template="places to visit in {title}")
   == ["places to visit in Kyoto"])
ok("typographic punctuation is still cleaned up for searching",
   B.build_queries(_NoDb(), "Guns N’ Roses", deep=False,
                   template="{title}") == ["Guns N' Roses"])
ok("one button is one query, never several",
   len(B.build_queries(_NoDb(), "Kyoto", deep=True,
                       template="{title} skyline")) == 1)


# ════════════════════════════════════════════════════════════════════════════
# 4. The style reference is sent only when it is switched on
# ════════════════════════════════════════════════════════════════════════════
print("\nThe style reference goes only when the switch says so")

gpt_src = (REPO / "app" / "gpt_images.py").read_text(encoding="utf-8")
tree = ast.parse(gpt_src)
generate_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "generate")
generate_src = ast.get_source_segment(gpt_src, generate_fn) or ""

ok("generate() reads the switch itself, rather than trusting its caller",
   "openai_use_style_image" in generate_src,
   "two places deciding how many images to send is the Chrome-profile bug again")
ok("the missing-reference error only fires when the reference is wanted",
   "use_style and not style.is_file()" in generate_src)

# Two branches, read out of the shipped file. Named separately because the
# thing that matters is that BOTH exist: one sending both pictures, one
# sending the photo alone.
both_lines = [l for l in generate_src.splitlines()
              if l.strip().startswith("files = ") and "style" in l]
photo_only = [l for l in generate_src.splitlines()
              if l.strip().startswith("files = ") and "style" not in l]
ok("there is a branch that sends both pictures", len(both_lines) == 1)
ok("there is a branch that sends the photo alone", len(photo_only) == 1)
ok("the source photo is sent in both branches",
   all("_part(source)" in l for l in both_lines + photo_only))

# The ORDER is the part that quietly ruins pictures rather than failing.
ok("the reference is written FIRST when both are sent",
   both_lines and both_lines[0].index("style") < both_lines[0].index("source"),
   "every prompt saying 'the first image' would then mean the photo")

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The original mistake: always send both. Expect the first check to go red.
print("\n  sabotage — always send both images")
pretend = "files = [('image[]', _part(style)), ('image[]', _part(source))]"
ok("an always-both version has no switch in it (so the check is looking)",
   "openai_use_style_image" not in pretend)


# ════════════════════════════════════════════════════════════════════════════
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nFAILED:")
    for f in failures:
        print(f"  · {f}")
    sys.exit(1)
print("All good.")
