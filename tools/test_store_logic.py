"""
Behaviour tests for the listing-health decisions. Run: python tools/test_store_logic.py

════════════════════════════════════════════════════════════════════════════
WHY THESE TWO FUNCTIONS AND NOT OTHERS
════════════════════════════════════════════════════════════════════════════
Both decide something EXPENSIVE and neither says anything when it is wrong:

  · `stage_should_stop` is the only way a stop button reaches the worker
    machine. Get it wrong in one direction and PAUSE stops nothing while
    live listings keep switching off for an hour. Wrong in the other and
    every stage quits at its first design, silently doing almost nothing.

  · `_not_failed_this_run` is what lets a stage END. The stage is over when
    no account has work left — so a design that can never be switched would
    be handed out, fail, and be handed straight back, for ever, holding
    Photoshop and the uploads the whole time.

════════════════════════════════════════════════════════════════════════════
IT RUNS THE SHIPPED SOURCE, NOT A COPY
════════════════════════════════════════════════════════════════════════════
The functions are lifted out of `app/earnings/store_health.py` by parsing
it and executing just those definitions. That matters: a test containing its
own copy of the logic proves the copy works. Both are pure — they touch only
their arguments — which is what makes this possible and is a good reason to
keep them that way.

It also means NO DEPENDENCIES. No FastAPI, no SQLAlchemy, no database. It
runs anywhere Python does, which is why preflight can call it on every
deploy.

════════════════════════════════════════════════════════════════════════════
--SABOTAGE PROVES THE TESTS CAN GO RED
════════════════════════════════════════════════════════════════════════════
`python tools/test_store_logic.py --sabotage` breaks each rule on purpose
and checks the suite notices. A green light from a test that cannot fail is
worse than no test: the stage-counting bug was shipped with a test that
made ONE run and asserted ONE stage, because that is how its author pictured
it — the test shared the bug's assumption exactly.

That run found a real thing: an explicit `status in FINISHED` guard whose
removal changed no answer, because the stale-stage line already covered it.
It was deleted rather than kept as a guard that guarded nothing.
"""

from __future__ import annotations

import ast
import re
import sys
import types
import typing
from datetime import datetime, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
SOURCE = APP / "earnings" / "store_health.py"
LISTING = APP / "listing_check.py"
ARCHIVE = APP / "archive_index.py"
ARCHIVE_UNDER_TEST = ("strip_suffix", "parse_path")
UNDER_TEST = ("_not_failed_this_run", "stage_should_stop", "judge_counts")
LISTING_UNDER_TEST = ("slug", "verdict")


def _exec(path: Path, names: tuple, extra: dict):
    """
    Execute just the named functions from a file, with stand-ins.

    ════════════════════════════════════════════════════════════════════════
    MODULE-LEVEL VALUES ARE LIFTED TOO, AND THAT IS NOT A CONVENIENCE
    ════════════════════════════════════════════════════════════════════════
    The first version took only the functions and let the caller pass
    anything else in. So the archive test handed in its own copy of
    `FOLDER_ID` — and when that pattern was broken on purpose in the real
    file, changing which title number a path resolves to, every check
    stayed green. The test was reading itself.

    Same failure as the preflight hook check that searched the JS and found
    its own query, and as the guard check that matched a parameter name.
    Anything a rule depends on has to come from the shipped file.

    An assignment that cannot run out of context is skipped rather than
    fatal — some modules open with things that need a database — but the
    ones that matter here are plain constants and compiled patterns.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ns = {"Optional": typing.Optional, "Session": typing.Any,
          "datetime": datetime, "re": re, **extra}

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        try:
            snippet = ast.Module([node], [])
            ast.fix_missing_locations(snippet)
            exec(compile(snippet, str(path), "exec"), ns)
        except Exception:
            continue

    picked = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in names]
    if len(picked) != len(names):
        raise SystemExit(
            f"Could not find {set(names) - {n.name for n in picked}} in "
            f"{path.name} — renamed? This test is now blind.")
    mod = ast.Module(
        [ast.ImportFrom(module="__future__",
                        names=[ast.alias(name="annotations")], level=0)] + picked,
        [])
    ast.fix_missing_locations(mod)
    exec(compile(mod, str(path), "exec"), ns)
    return [ns[n] for n in names]


def load(src: str):
    """Execute just the functions under test, with stand-ins for the rest."""
    tree = ast.parse(src)
    ns = {
        "FINISHED": ("done", "failed", "abandoned"),
        "Optional": typing.Optional, "Session": typing.Any,
        "StoreScanRun": typing.Any, "StoreListing": typing.Any,
        "datetime": datetime,
    }
    picked = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in UNDER_TEST]
    if len(picked) != len(UNDER_TEST):
        raise SystemExit(
            f"Could not find {set(UNDER_TEST) - {n.name for n in picked}} in "
            f"{SOURCE.name} — renamed? This test is now blind.")
    mod = ast.Module(
        [ast.ImportFrom(module="__future__",
                        names=[ast.alias(name="annotations")], level=0)] + picked,
        [])
    ast.fix_missing_locations(mod)
    exec(compile(mod, str(SOURCE), "exec"), ns)
    return tuple(ns[n] for n in UNDER_TEST)


def listing_cases() -> list[tuple[str, bool]]:
    """
    The FineArtAmerica address rule, and what a status code means.

    Both are cheap to get wrong and silent when wrong. A bad slug makes a
    live listing read as a copyright takedown; treating a 403 as GONE does
    the same thing to thousands at once.

    The six addresses below are REAL listings from two live shops, checked
    by hand on 2026-08-24. They are the only reason to believe the rule.
    """
    slug, verdict = _exec(LISTING, LISTING_UNDER_TEST, {})
    url = lambda t, a: f"{slug(t)}-{slug(a)}.html"
    return [
        ("real listing: Alicia Keys - #B",
         url("Alicia Keys - #B", "White And Black")
         == "alicia-keys-b-white-and-black.html"),
        ("real listing: Nickelback - #A",
         url("Nickelback - #A", "White And Black")
         == "nickelback-a-white-and-black.html"),
        ("real listing: Snoop Dogg - #A",
         url("Snoop Dogg - #A", "White And Black")
         == "snoop-dogg-a-white-and-black.html"),
        ("real listing: Brother Bear - 2003 A",
         url("Brother Bear - 2003 A", "Golden Reel")
         == "brother-bear-2003-a-golden-reel.html"),
        ("real listing: The Killing - 2011 C",
         url("The Killing - 2011 C", "Golden Reel")
         == "the-killing-2011-c-golden-reel.html"),
        ("real listing: Patriot Games - 1992 A",
         url("Patriot Games - 1992 A", "Golden Reel")
         == "patriot-games-1992-a-golden-reel.html"),
        ("a title that is only punctuation gives an empty slug, not a hyphen",
         slug("- # -") == ""),

        ("200 means the listing is there", verdict(200) == "live"),
        # ── THE TWO CODES FAA ACTUALLY USES, MEASURED 25 Aug ─────────────
        # A listing the owner deleted returned 410; the same address with
        # two extra letters returned 404. Telling them apart is what
        # separates a copyright takedown from a mistyped artist name, and
        # the first version had them effectively backwards — 410 fell
        # through to "we could not look", so REAL removals were ignored.
        ("410 means it was REMOVED — the takedown case, the whole point",
         verdict(410) == "gone"),
        ("404 means no page ever existed there — our ADDRESS is wrong, "
         "which is not the same as a takedown",
         verdict(404) == "no_page"),
        ("410 and 404 must never give the same answer",
         verdict(410) != verdict(404)),
        ("403 means we could not look — the Linux server gets this for "
         "every page, live or not",
         verdict(403) == "unknown"),
        ("429 means we were throttled, not that anything is missing",
         verdict(429) == "unknown"),
        ("500 means the site had a moment", verdict(500) == "unknown"),
        ("no status at all — the request never completed",
         verdict(None) == "unknown"),
    ]


def archive_cases() -> list[tuple[str, bool]]:
    """
    Reading a path on the storage box back into "which poster is this".

    Cheap to get wrong and silent when wrong in BOTH directions. Match too
    loosely and a file gets attached to the wrong poster, which sends the
    wrong image to a marketplace. Match too tightly and 4,865 finished
    images read as missing and the archive stays unindexed for ever.

    The paths below are the real shape from the storage box, including the
    dotted titles the old Photoshop script mangled.
    """
    import os as _os
    # `os` is a stand-in for a library. FOLDER_ID is deliberately NOT passed
    # in — it is a RULE, and a rule handed to the test by the test is a rule
    # nobody is checking. It comes out of the shipped file.
    strip, parse = _exec(ARCHIVE, ARCHIVE_UNDER_TEST, {"os": _os})
    p = lambda path: parse(path, "_Painted")
    return [
        ("an ordinary archive path gives its title number and poster",
         p("2026-05-11/1. The Shawshank Redemption (1994)/"
           "The Shawshank Redemption 1_Painted.jpg")
         == (1, "The Shawshank Redemption 1",
             "The Shawshank Redemption 1_Painted.jpg")),
        ("a title whose own name contains a dot still resolves — the folder "
         "number is what we match on, never the text",
         p("2026-05-12/432. E.T. (1982)/E_Painted.jpg")[0] == 432),
        ("a three-digit title number is not truncated",
         p("2026-05-12/1057. Se7en (1995)/Se7en 2_Painted.jpg")[0] == 1057),
        ("Windows backslashes read the same as forward ones",
         p("2026-05-11\\1. Title (1994)\\Title 1_Painted.jpg")[0] == 1),
        ("a folder with no leading number is not ours",
         p("2026-05-11/Some Folder I Made/thing_Painted.jpg") is None),
        ("a stray non-image on the drive is ignored rather than reported "
         "as a poster",
         p("2026-05-11/1. Title (1994)/notes.txt") is None),
        ("a loose file at the top of the tree is not ours",
         p("stray.jpg") is None),

        ("the suffix comes off the stem", strip("Title 1_Painted.jpg", "_Painted")
         == "Title 1"),
        ("the extension comes off too, so a .png source and a .jpg output "
         "still match each other",
         strip("Title 1_Painted.jpg", "_Painted")
         == strip("Title 1_Painted.png", "_Painted")),
        ("a filename that never had the suffix is left alone",
         strip("Title 1.jpg", "_Painted") == "Title 1"),
        ("a title with a dot in it keeps the dot — only the extension goes",
         strip("E.T. 1_Painted.jpg", "_Painted") == "E.T. 1"),
    ]


def obj(**kw):
    return types.SimpleNamespace(**kw)


STARTED = datetime(2026, 8, 24, 9, 0)
RUN = obj(started_at=STARTED, status="deactivating", paused_at=None)


def cases(not_failed_raw, should_stop, judge) -> list[tuple[str, bool]]:
    """(what it means in plain words, did it hold)."""
    # Most cases care about the older rules, so the flag limit defaults to
    # the shipped 3 and the flag cases pass their own.
    def not_failed(row, run, stage, give_up_after=3):
        if not hasattr(row, "action_fail_count"):
            row.action_fail_count = 0
        return not_failed_raw(row, run, stage, give_up_after)

    return [
        # ── Which designs a stage will act on ────────────────────────────
        ("a design with no failure is acted on",
         not_failed(obj(action_error=None, action_error_at=None,
                        action_error_kind=None), RUN, "deactivate") is True),
        ("a design that failed EARLIER IN THIS RUN is skipped, so the "
         "stage can finish instead of looping on it",
         not_failed(obj(action_error="no button", action_error_kind="deactivate",
                        action_error_at=STARTED + timedelta(minutes=5)),
                    RUN, "deactivate") is False),
        ("a design that failed LAST WEEK is tried again",
         not_failed(obj(action_error="no button", action_error_kind="deactivate",
                        action_error_at=STARTED - timedelta(days=7)),
                    RUN, "deactivate") is True),
        ("an old failure with no timestamp is skipped rather than looped",
         not_failed(obj(action_error="old", action_error_at=None,
                        action_error_kind="deactivate"),
                    RUN, "deactivate") is False),

        # ── A FAILED SWITCH-OFF MUST NOT BLOCK A SWITCH-ON ───────────────
        # The real case, 24 Aug: a design was switched OFF successfully, a
        # duplicate job tried again and failed "already inactive", and the
        # reactivate stage then SKIPPED it — leaving a live listing hidden
        # while the run reported success.
        ("a design that failed to switch OFF is still switched back ON",
         not_failed(obj(action_error="No Deactivate button",
                        action_error_kind="deactivate",
                        action_error_at=STARTED + timedelta(minutes=5)),
                    RUN, "reactivate") is True),
        ("a design that failed to switch ON this run is not retried "
         "immediately, so the stage can end",
         not_failed(obj(action_error="no publish button",
                        action_error_kind="reactivate",
                        action_error_at=STARTED + timedelta(minutes=5)),
                    RUN, "reactivate") is False),
        ("an error from before we recorded the action never blocks a "
         "switch-on — a hidden listing is the expensive outcome",
         not_failed(obj(action_error="old", action_error_kind=None,
                        action_error_at=STARTED + timedelta(minutes=5)),
                    RUN, "reactivate") is True),

        # ── GIVING UP IS ONLY SAFE IN ONE DIRECTION ──────────────────────
        # A design that will not switch OFF may be left alone: it is live and
        # earning. A design that will not switch back ON may not, because it
        # is a live listing WE hid and every day it stays hidden costs money.
        ("a design that has failed to switch OFF three times is flagged "
         "instead of being retried at the front of every future sweep",
         not_failed(obj(action_error="No Deactivate button",
                        action_error_kind="deactivate",
                        action_error_at=STARTED - timedelta(days=30),
                        action_fail_count=3), RUN, "deactivate") is False),
        ("two failures is not yet three — still tried",
         not_failed(obj(action_error="No Deactivate button",
                        action_error_kind="deactivate",
                        action_error_at=STARTED - timedelta(days=30),
                        action_fail_count=2), RUN, "deactivate") is True),
        ("a design that has failed to switch back ON many times is STILL "
         "tried — abandoning it would leave a live listing hidden for ever",
         not_failed(obj(action_error="no publish button",
                        action_error_kind="reactivate",
                        action_error_at=STARTED - timedelta(days=30),
                        action_fail_count=99), RUN, "reactivate") is True),

        # ── DOES THE MARKETPLACE'S OWN COUNT AGREE WITH US ───────────────
        # The only check here that is not us marking our own homework. It is
        # what would have caught TALKING HEADS · LITTLE CREATURES, recorded
        # as republished on 25 Aug while sitting on the inactive tab.
        ("switching 80 back on and their count falling by 80 — agreed",
         judge("reactivate", 100, 20, 80, False)[0] == "agreed"),
        ("switching 80 back on and their count falling by only 77 — three "
         "did not go back on, and this is the whole point",
         judge("reactivate", 100, 23, 80, False)[0] == "mismatch"),
        ("switching 12 off and their count rising by 12 — agreed",
         judge("deactivate", 30, 42, 12, False)[0] == "agreed"),
        ("switching 12 off and their count not moving at all — mismatch",
         judge("deactivate", 30, 30, 12, False)[0] == "mismatch"),
        ("the 379 designs the owner switched off himself do not matter — "
         "only the CHANGE across our turn is ours",
         judge("reactivate", 459, 379, 80, False)[0] == "agreed"),
        ("a count we could not read is NOT a disagreement",
         judge("reactivate", None, 20, 80, False)[0] == "unreadable"),
        ("a count we could not read is not treated as zero either",
         judge("deactivate", 30, None, 12, False)[0] == "unreadable"),
        ("an account stopped part-way has no expectation to test",
         judge("reactivate", 100, 90, 80, True)[0] == "skipped"),
        ("switching nothing and nothing moving is agreement, not a "
         "mismatch — a stage with no work must stay quiet",
         judge("deactivate", 30, 30, 0, False)[0] == "agreed"),
        ("the sentence says what happened in things, not in arithmetic",
         "still switched off" in judge("reactivate", 100, 23, 80, False)[1]),

        # ── Whether the worker machine keeps switching designs ───────────
        ("no run at all — stop",
         should_stop(None, None, "deactivate") is True),
        ("a healthy run — keep going",
         should_stop(None, obj(status="deactivating", paused_at=None),
                     "deactivate") is False),
        ("PAUSED — stop, and this is the line that makes PAUSE reach the "
         "worker machine at all",
         should_stop(None, obj(status="deactivating", paused_at=STARTED),
                     "deactivate") is True),
        ("the run was stopped by hand — stop, rather than switching off "
         "designs for a run the screen says is over",
         should_stop(None, obj(status="abandoned", paused_at=None),
                     "deactivate") is True),
        ("the run finished — stop",
         should_stop(None, obj(status="done", paused_at=None),
                     "reactivate") is True),
        ("a leftover switching-off job while the run has moved on to "
         "switching back on — stop",
         should_stop(None, obj(status="reactivating", paused_at=None),
                     "deactivate") is True),
        ("the switching-back-on job during that same stage — keep going",
         should_stop(None, obj(status="reactivating", paused_at=None),
                     "reactivate") is False),
    ]


# Each is (what breaking it would cost, find, replace).
SABOTAGE = [
    ("PAUSE would never reach the worker machine",
     "    if run.paused_at is not None:\n        return True\n", ""),
    ("a stopped run would keep switching live listings off",
     "    return run.status != expected", "    return False"),
    ("every stage would quit at its first design",
     "    return run.status != expected", "    return True"),
    ("a design that cannot be switched would loop for ever",
     "    return row.action_error_at < run.started_at",
     "    return row.action_error_at >= run.started_at"),
    ("failures would be ignored, so the same design loops for ever",
     "    if not row.action_error:\n        return True\n", "    return True\n"),
    ("a failed switch-OFF would again block switching back ON",
     "    if row.action_error_kind and row.action_error_kind != stage:\n        return True\n",
     ""),
    ("old failures with no timestamp would loop for ever",
     "        return False\n    return row.action_error_at",
     "        return True\n    return row.action_error_at"),
    ("a listing WE hid would be abandoned for ever after three failures",
     '    if stage == "deactivate" and give_up_after and \\',
     "    if give_up_after and \\"),
    ("a design that will never switch off would be retried in every sweep "
     "for ever",
     "            (row.action_fail_count or 0) >= give_up_after:\n        return False\n",
     ""),
    # ── The marketplace's own count ──────────────────────────────────────
    ("switching designs back on would be checked as if we were switching "
     "them off, so every clean run would read as a disaster",
     '    expected = int(switched) if stage == "deactivate" else -int(switched)',
     "    expected = int(switched)"),
    ("a number we could not read would be treated as zero, inventing "
     "findings out of a failed page load",
     "    if before is None or after is None:\n        return (\"unreadable\",",
     "    if False:\n        return (\"unreadable\","),
    ("a disagreement would be reported as agreement, which is the exact "
     "silence this check exists to break",
     "    if change == expected:", "    if True:"),
    ("an account stopped part-way would be reported as a disagreement "
     "every single time you press pause",
     "    if cut_short:\n        return (\"skipped\",",
     "    if False:\n        return (\"skipped\","),
]

# The listing rules live in a different file, so they get their own list.
LISTING_SABOTAGE = [
    ("every live listing would read as a copyright takedown",
     'return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")',
     'return re.sub(r"[^a-z0-9]+", "", (text or "").lower())'),
    ("the separator would survive and no address would ever match",
     'r"[^a-z0-9]+"', 'r"[^a-z0-9 ]+"'),
    ("being blocked would be recorded as the listing being gone",
     '        return "no_page"\n    return "unknown"',
     '        return "no_page"\n    return "gone"'),
    ("a deleted listing would be recorded as healthy",
     '    if http == 410:\n        return "gone"',
     '    if http == 410:\n        return "live"'),
    ("a real takedown would be filed as 'we could not look' and ignored",
     '    if http == 410:\n        return "gone"          # it was there; it is not any more\n',
     ''),
    ("a mistyped artist name would be reported as thousands of takedowns",
     '        return "no_page"', '        return "gone"'),
]

# The archive rules live in their own file too.
ARCHIVE_SABOTAGE = [
    ("every title number past 9 would be read wrong, so thousands of "
     "finished images would attach to the wrong poster",
     r'FOLDER_ID = re.compile(r"^(\d+)[.\s]")',
     r'FOLDER_ID = re.compile(r"^(\d)[.\s]")'),
    ("the output suffix would stay on the name and nothing would ever "
     "match a poster — the whole archive would read as missing",
     "    if suffix and stem.endswith(suffix):\n"
     "        stem = stem[: -len(suffix)]\n", ""),
    ("a .png source could never match its .jpg output",
     'stem = os.path.splitext(filename or "")[0]',
     'stem = filename or ""'),
    ("junk on the drive would be reported as posters we cannot place",
     '    if os.path.splitext(filename)[1].lower() not in (\n'
     '            ".jpg", ".jpeg", ".png", ".webp"):\n'
     '        return None\n', ''),
    ("a Windows path would never match, so a job run on the worker "
     "machine would find nothing at all",
     'parts = [p for p in (rel or "").replace("\\\\", "/").split("/") if p]',
     'parts = [p for p in (rel or "").split("/") if p]'),
]


def run_suite(src: str) -> list[tuple[str, bool]]:
    return cases(*load(src)) + listing_cases() + archive_cases()


def main() -> int:
    src = SOURCE.read_text(encoding="utf-8")

    if "--sabotage" in sys.argv:
        print("SABOTAGE — breaking each rule on purpose. Every line must say "
              "CAUGHT.\n")
        if not all(ok for _, ok in run_suite(src)):
            print("BASELINE ALREADY FAILING — fix that first.")
            return 1
        bad = 0
        for cost, find, repl in SABOTAGE:
            if find not in src:
                print(f"??      could not apply — {cost}")
                bad += 1
                continue
            try:
                caught = not all(ok for _, ok in
                                 run_suite(src.replace(find, repl, 1)))
            except Exception:
                caught = True
            print(f"{'CAUGHT ' if caught else 'MISSED '} {cost}")
            bad += 0 if caught else 1

        # The listing rules live in their own file, so they are broken on
        # disk and put back. Uglier than the in-memory swap above, and
        # unavoidable: `run_suite` reads that file itself.
        for path, table in ((LISTING, LISTING_SABOTAGE),
                            (ARCHIVE, ARCHIVE_SABOTAGE)):
            original = path.read_text(encoding="utf-8")
            for cost, find, repl in table:
                if find not in original:
                    print(f"??      could not apply — {cost}")
                    bad += 1
                    continue
                path.write_text(original.replace(find, repl, 1),
                                encoding="utf-8")
                try:
                    caught = not all(ok for _, ok in run_suite(src))
                except Exception:
                    caught = True
                finally:
                    path.write_text(original, encoding="utf-8")
                print(f"{'CAUGHT ' if caught else 'MISSED '} {cost}")
                bad += 0 if caught else 1
        print()
        print("Every sabotage was caught." if not bad else
              f"{bad} sabotage(s) went unnoticed — those tests cannot fail, "
              f"which is worse than not having them.")
        return 1 if bad else 0

    results = run_suite(src)
    for label, ok in results:
        print(f"  [{'ok  ' if ok else 'FAIL'}] {label}")
    failed = [l for l, ok in results if not ok]
    print()
    print(f"{len(results)} checks, all passing." if not failed
          else f"{len(failed)} FAILING.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
