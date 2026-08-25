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
UNDER_TEST = ("_not_failed_this_run", "stage_should_stop")
LISTING_UNDER_TEST = ("slug", "verdict")


def _exec(path: Path, names: tuple, extra: dict):
    """Execute just the named functions from a file, with stand-ins."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    ns = {"Optional": typing.Optional, "Session": typing.Any,
          "datetime": datetime, "re": re, **extra}
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
    return ns["_not_failed_this_run"], ns["stage_should_stop"]


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
        ("404 is the ONLY thing that means gone", verdict(404) == "gone"),
        ("403 means we could not look — the Linux server gets this for "
         "every page, live or not",
         verdict(403) == "unknown"),
        ("429 means we were throttled, not that anything is missing",
         verdict(429) == "unknown"),
        ("500 means the site had a moment", verdict(500) == "unknown"),
        ("no status at all — the request never completed",
         verdict(None) == "unknown"),
    ]


def obj(**kw):
    return types.SimpleNamespace(**kw)


STARTED = datetime(2026, 8, 24, 9, 0)
RUN = obj(started_at=STARTED, status="deactivating", paused_at=None)


def cases(not_failed, should_stop) -> list[tuple[str, bool]]:
    """(what it means in plain words, did it hold)."""
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
]

# The listing rules live in a different file, so they get their own list.
LISTING_SABOTAGE = [
    ("every live listing would read as a copyright takedown",
     'return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")',
     'return re.sub(r"[^a-z0-9]+", "", (text or "").lower())'),
    ("the separator would survive and no address would ever match",
     'r"[^a-z0-9]+"', 'r"[^a-z0-9 ]+"'),
    ("being blocked would be recorded as the listing being gone",
     '    if http == 404:\n        return "gone"\n    return "unknown"',
     '    return "gone"'),
    ("a deleted listing would be recorded as healthy",
     '    if http == 404:\n        return "gone"',
     '    if http == 404:\n        return "live"'),
]


def run_suite(src: str) -> list[tuple[str, bool]]:
    return cases(*load(src)) + listing_cases()


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
        listing_src = LISTING.read_text(encoding="utf-8")
        for cost, find, repl in LISTING_SABOTAGE:
            if find not in listing_src:
                print(f"??      could not apply — {cost}")
                bad += 1
                continue
            LISTING.write_text(listing_src.replace(find, repl, 1),
                               encoding="utf-8")
            try:
                caught = not all(ok for _, ok in run_suite(src))
            except Exception:
                caught = True
            finally:
                LISTING.write_text(listing_src, encoding="utf-8")
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
