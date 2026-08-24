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
import sys
import types
import typing
from datetime import datetime, timedelta
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "app" / "earnings" / "store_health.py"
UNDER_TEST = ("_not_failed_this_run", "stage_should_stop")


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


def obj(**kw):
    return types.SimpleNamespace(**kw)


STARTED = datetime(2026, 8, 24, 9, 0)
RUN = obj(started_at=STARTED, status="deactivating", paused_at=None)


def cases(not_failed, should_stop) -> list[tuple[str, bool]]:
    """(what it means in plain words, did it hold)."""
    return [
        # ── Which designs a stage will act on ────────────────────────────
        ("a design with no failure is acted on",
         not_failed(obj(action_error=None, action_error_at=None), RUN) is True),
        ("a design that failed EARLIER IN THIS RUN is skipped, so the "
         "stage can finish instead of looping on it",
         not_failed(obj(action_error="no button", action_error_at=STARTED
                        + timedelta(minutes=5)), RUN) is False),
        ("a design that failed LAST WEEK is tried again",
         not_failed(obj(action_error="no button", action_error_at=STARTED
                        - timedelta(days=7)), RUN) is True),
        ("an old failure with no timestamp is skipped rather than looped",
         not_failed(obj(action_error="old", action_error_at=None), RUN) is False),

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
    ("old failures with no timestamp would loop for ever",
     "        return False\n    return row.action_error_at",
     "        return True\n    return row.action_error_at"),
]


def run_suite(src: str) -> list[tuple[str, bool]]:
    return cases(*load(src))


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
