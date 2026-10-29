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

    The refusal is recognised by its OWN words. Any KeyError used to count,
    so a key that is no longer a setting at all ("Unknown pipeline setting")
    read as "the guard refused it" and the check could not fail (v287).
    """
    try:
        P.set_setting(None, key, value)
    except KeyError as e:
        return "does not say which place" in str(e)
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
ok("the GOOGLE button phrasing is guarded too",
   rejects("google_query", "landscape photo"))
ok("a key that is not a setting is not mistaken for a refusal",
   not rejects("brave_query_deep", "landscape photo"))
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

def cache_checks(k) -> list[str]:
    """The cache-name rules, run against whichever function is passed —
    the shipped one, or the sabotage below. Returns what broke."""
    broke = []
    if k("places to visit in {title}") != k("places to visit in {title}"):
        broke.append("the same wording always gives the same name")
    if k("places to visit in {title}") == k("{title} skyline"):
        broke.append("different wording gives a different name")
    if k("aerial view of {title}") == k("aerial photo of {title}"):
        broke.append("even a one-word edit gives a different name")
    if not all(len(k(p)) <= 16 for p in ("{title}", "x" * 500 + " {title}")):
        broke.append("the name fits the 16-character column")
    if k("{title}") in ("normal", "deep") or not k("{title}").startswith("p:"):
        broke.append("the name cannot be confused with the everyday buttons")
    return broke


shipped = cache_checks(P.phrasing_cache_key)
for rule in ("the same wording always gives the same name",
             "different wording gives a different name",
             "even a one-word edit gives a different name",
             "the name fits the 16-character column",
             "the name cannot be confused with the everyday buttons"):
    ok(rule, rule not in shipped)

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The original mistake: key the cache on the BUTTON NUMBER. The SAME rules
# above are run against it, so this proves those rules can fail — until
# v287 the sabotage compared a lambda with itself and could not.
print("\n  sabotage — key on the button number instead of the words")
by_position = lambda phrasing, position=2: f"p:{position}"      # noqa: E731
ok("keying on position breaks 'different wording gives a different name'",
   "different wording gives a different name" in cache_checks(by_position),
   "the rules above would pass a cache keyed on the button number")


# ════════════════════════════════════════════════════════════════════════════
# 3. A phrasing button searches its own words
# ════════════════════════════════════════════════════════════════════════════
print("\nA phrasing button searches the words on it")

from app import brave_search as B                   # noqa: E402


class _NoDb:
    """Any database use here is a bug: a template needs no settings lookup."""


# The ONE setting a template may read: since v221 the kinds the owner lists
# in `search_kind_omit` render {kind} as nothing. Answered here so any OTHER
# setting read still fails loudly (the test went red on _NoDb from v221 until
# 2026-10-29 because nobody gave it this answer).
omit_list = "island"
_real_setting = B._setting


def _only_kind_omit(db, key, project=None):
    if key != "search_kind_omit":
        raise AssertionError(f"build_queries read the setting {key!r}")
    return omit_list


B._setting = _only_kind_omit

ok("the given wording is used, with the place dropped in",
   B.build_queries(_NoDb(), "Kyoto",
                   template="places to visit in {title}")
   == ["places to visit in Kyoto"])

# ── {kind} ─────────────────────────────────────────────────────────────────
#
# The sheet's description column — city, island, mountain. A PLACEHOLDER
# rather than something glued on automatically, so the owner can put it in,
# take it out and compare. A rule baked into the code would take that away.
print("\n  the {kind} placeholder")
ok("the kind is dropped in where it is asked for",
   B.build_queries(_NoDb(), "Chicago Illinois USA", kind="city",
                   template="{title} {kind}")
   == ["Chicago Illinois USA city"])
ok("and his own words go on the end",
   B.build_queries(_NoDb(), "Chicago Illinois USA", kind="city",
                   template="{title} {kind} scenic")
   == ["Chicago Illinois USA city scenic"])
ok("a template that does not ask for it is left alone",
   B.build_queries(_NoDb(), "Kyoto", kind="city", template="{title} skyline")
   == ["Kyoto skyline"])
ok("a MISSING kind leaves no double space behind it",
   B.build_queries(_NoDb(), "Kyoto", kind="", template="{title} {kind} scenic")
   == ["Kyoto scenic"],
   "a gap in the middle of the query looks broken on screen")
ok("a kind the owner listed to leave out renders as nothing",
   B.build_queries(_NoDb(), "Mallorca Spain", kind="Island",
                   template="{title} {kind}") == ["Mallorca Spain"],
   "'Mallorca Spain island' finds anonymous landmasses from the air")

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# An empty leave-out list must put the word back, or the check above is not
# watching the setting. Expect "Mallorca Spain Island".
print("\n  sabotage — an empty leave-out list")
omit_list = ""
ok("with nothing listed, the kind is searched (so the setting is doing work)",
   B.build_queries(_NoDb(), "Mallorca Spain", kind="Island",
                   template="{title} {kind}") == ["Mallorca Spain Island"])
omit_list = "island"

ok("typographic punctuation is still cleaned up for searching",
   B.build_queries(_NoDb(), "Guns N’ Roses",
                   template="{title}") == ["Guns N' Roses"])
ok("one button is one query, never several",
   len(B.build_queries(_NoDb(), "Kyoto",
                       template="{title} skyline")) == 1)
B._setting = _real_setting

# ── DEEP SEARCH IS GONE — REMOVED 2026-09-06 ──────────────────────────────
#
# It fired two queries and merged them. The owner's phrasing buttons do the
# same job better, because each one is wording he chose.
#
# THE PAID KEY STAYED, and that is the part worth checking. It was never only
# for deep search: it is the fallback when the free key is inside its
# one-per-second window or has spent its monthly allowance. Removing both
# together would leave a worker looking at a rate-limit error.
print("\nDeep search is gone; the paid key is not")
import inspect                                          # noqa: E402
src_search = inspect.getsource(B.search)
ok("nothing takes a `deep` argument any more",
   "deep" not in inspect.signature(B.build_queries).parameters
   and "deep" not in inspect.signature(B.search).parameters)
ok("the paid key is still the fallback",
   "paid_key" in src_search and "_RateLimited" in src_search,
   "removing this would show a worker an error instead of pictures")
ok("brave_query_deep is no longer a setting",
   "brave_query_deep" not in P.DEFAULTS)


# ════════════════════════════════════════════════════════════════════════════
# 4. The style reference is sent only when it is switched on
# ════════════════════════════════════════════════════════════════════════════
print("\nThe style reference goes only when the switch says so")

# Since v266 the decision lives in step1_images(), shared by the live call
# (generate) and the batch file, so "which images, in which order" is made in
# one place. Until 2026-10-29 these checks still read generate() and found
# nothing to check.
gpt_src = (REPO / "app" / "gpt_images.py").read_text(encoding="utf-8")
tree = ast.parse(gpt_src)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
images_src = ast.get_source_segment(gpt_src, fns["step1_images"]) or ""
called_by_generate = {n.func.id for n in ast.walk(fns["generate"])
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

ok("the live call takes its pictures from step1_images()",
   "step1_images" in called_by_generate,
   "two places deciding how many images to send is the Chrome-profile bug again")
ok("step1_images() reads the switch itself, rather than trusting its caller",
   'get_setting(db, "openai_use_style_image"' in images_src)
ok("the missing-reference error only fires when the reference is wanted",
   "use_style and not style.is_file()" in images_src)

# What step1_images() actually RETURNS, with the switch on and off. Until
# v287 these checks read the return line as text, and the sabotage below
# checked a string the test wrote itself, so neither could fail.
import tempfile                                          # noqa: E402
from app import gpt_images as GI                         # noqa: E402

_pics = Path(tempfile.mkdtemp(prefix="pd_style_"))
(_pics / "style.png").write_bytes(b"S")
(_pics / "photo.jpg").write_bytes(b"P")


def sent(fn, switch_on: bool) -> list[str]:
    real = P.get_setting
    P.get_setting = (lambda db, key, project=None:
                     switch_on if key == "openai_use_style_image"
                     else real(db, key, project=project))
    try:
        return [name for name, _b, _m in
                fn(None, None, source=_pics / "photo.jpg", style=_pics / "style.png")]
    finally:
        P.get_setting = real


def style_checks(fn) -> list[str]:
    broke = []
    if sent(fn, True) != ["style.png", "photo.jpg"]:
        broke.append("switched on, both are sent, the reference FIRST")
    if sent(fn, False) != ["photo.jpg"]:
        broke.append("switched off, the photo goes alone")
    return broke


shipped = style_checks(GI.step1_images)
ok("switched on, both pictures are sent, the reference FIRST",
   "switched on, both are sent, the reference FIRST" not in shipped,
   "every prompt saying 'the first image' would then mean the photo")
ok("switched off, the photo goes alone", "switched off, the photo goes alone" not in shipped)

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The shipped function, run with its return line changed: always both, then
# the wrong order. The SAME checks must go red on each.
_line = "return [_part(style), _part(source)] if use_style else [_part(source)]"
_src = inspect.getsource(GI.step1_images)
ok("the return line is where the sabotage expects it", _line in _src)
for label, bad, rule in (
        ("always send both images", "return [_part(style), _part(source)]",
         "switched off, the photo goes alone"),
        ("send the photo first", "return [_part(source), _part(style)] if use_style else [_part(source)]",
         "switched on, both are sent, the reference FIRST")):
    print(f"\n  sabotage — {label}")
    _ns = dict(GI.__dict__)
    exec(_src.replace(_line, bad), _ns)
    ok(f"the check catches it ({rule})", rule in style_checks(_ns["step1_images"]))


# ════════════════════════════════════════════════════════════════════════════
print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nFAILED:")
    for f in failures:
        print(f"  · {f}")
    sys.exit(1)
print("All good.")
