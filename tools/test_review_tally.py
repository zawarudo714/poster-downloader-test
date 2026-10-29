"""
Behaviour test: on Approve Artwork every mark has its OWN outline colour
(REDO STEP 2 had none until v285, so it looked unmarked), and the tally ("12 will be released · …
37 NOT MARKED YET") is the FIRST thing in the review panel, above the title,
and the unmarked count is red — the owner reads it to see whether a picture
was missed (owner, 2026-10-29, v285). It used to be small print at the
bottom. Lifts updateTally from the shipped file and runs it in node.
Run from the repo root:

    python tools/test_review_tally.py
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
page = (ROOT / "app" / "static" / "js" / "admin_review_images.js").read_text(encoding="utf-8")
tpl = (ROOT / "app" / "templates" / "admin_review_images.html").read_text(encoding="utf-8")

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

stage = tpl[tpl.index("data-review-stage"):]
ok(stage.count("data-review-tally") == 1, "the review panel has exactly one tally")
ok(stage.index("data-review-tally") < stage.index("data-review-title"),
   "the tally sits above the title, at the top")
actions = stage[stage.index('class="review-actions"'):stage.index("review-keys")]
ok("data-review-tally" not in actions, "the tally is no longer down by UNMARK THIS TITLE")

css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
words = re.search(r"const MARK_WORD = \{(.*?)\};", page, re.S)
ok(words is not None, "found MARK_WORD, the list of marks")
marks = re.findall(r"(\w+):", words.group(1))
ok({"approve", "rerun", "unusable", "photoshop"} <= set(marks), f"the usual marks are listed (got {marks})")
colour = {}
for mk in marks:
    rule = re.search(r"\.review-img\.is-" + mk + r"\s*\{\s*border-color:\s*([^;]+);", css)
    ok(rule is not None, f"the {mk} mark has an outline colour")
    colour[mk] = rule.group(1).strip()
ok(len(set(colour.values())) == len(colour), f"no two marks share an outline colour ({colour})")
ok('class="btn btn-step2 btn-tiny" data-img-action="step2"' in page and ".btn-step2 {" in css,
   "the REDO STEP 2 button wears its own colour too")

if not shutil.which("node"):
    print("SKIP node is not installed")
    sys.exit(0)

# Every counting function the tally calls is LIFTED from the shipped file,
# not stubbed. Until v287 releasedCount, unmarkedCount and leftCount were
# stand-ins returning fixed numbers, so the counts the owner reads could
# have been anything and these checks would still pass.
LIFT = ("imageFate", "unmarkedCount", "leftCount", "releasedCount", "updateTally")


def lift(src: str, name: str) -> str:
    m = re.search(r"^  function " + name + r"\(.*?^  }\n", src, re.S | re.M)
    ok(m is not None, f"found {name} in admin_review_images.js")
    return m.group(0)


def tally(src: str) -> dict:
    """Run the lifted functions on one batch: picture 1 approved, 2 marked
    RERUN, 3 left for Photoshop, 4 and 5 not marked yet. Then mark 4 and 5."""
    script = "\n".join([
        "const el = { innerHTML: '', textContent: '' };",
        "const $ = () => el;",
        "const titles = [{ images: [{ poster_id: 1 }, { poster_id: 2 }, { poster_id: 3 }] },",
        "                { images: [{ poster_id: 4 }, { poster_id: 5 }] }];",
        "const decisions = new Map([[1, { action: 'approve' }], [2, { action: 'rerun' }],",
        "                           [3, { action: 'photoshop' }]]);",
        "const step2On = true; const mode = 'waiting';",
        *[lift(src, n) for n in LIFT],
        "const out = {};",
        "updateTally(); out.missing = el.innerHTML;",
        "decisions.set(4, { action: 'approve' }); decisions.set(5, { action: 'unusable' });",
        "updateTally(); out.done = el.innerHTML;",
        "console.log(JSON.stringify(out));",
    ])
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        print("FAIL node:", res.stderr)
        sys.exit(1)
    return json.loads(res.stdout)


def tally_checks(out: dict) -> list[str]:
    broke = []
    if not out["missing"].startswith("1 will be released · 1 repaint"):
        broke.append("the tally counts what SAVE will do")
    if '<span class="review-tally-bare">2 NOT MARKED YET</span>' not in out["missing"]:
        broke.append("unmarked pictures are counted in red")
    if not out["done"].startswith("2 will be released"):
        broke.append("a retired picture is not released")
    if 'review-tally-done' not in out["done"] or "NOT MARKED" in out["done"]:
        broke.append("with nothing missed it says so")
    return broke


out = tally(page)
shipped = tally_checks(out)
for rule in ("the tally counts what SAVE will do", "unmarked pictures are counted in red",
             "a retired picture is not released", "with nothing missed it says so"):
    ok(rule not in shipped, f"{rule} (got {out!r})")

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The shipped file with one counting rule broken at a time. The SAME checks
# must go red on each, which the stubs could never do.
for label, old, bad, rule in (
        ("release RERUN pictures too",
         "if (d && (d.action === 'rerun' || d.action === 'unusable'",
         "if (d && (d.action === 'unusable'",
         "the tally counts what SAVE will do"),
        ("count Photoshop pictures as unmarked",
         "if (d && d.action === 'photoshop') return 'left';",
         "if (d && d.action === 'photoshop') return 'unmarked';",
         "unmarked pictures are counted in red")):
    print(f"\n  sabotage — {label}")
    ok(page.count(old) == 1, "the line is where the sabotage expects it")
    ok(rule in tally_checks(tally(page.replace(old, bad))), f"the check catches it ({rule})")
print("\nALL REVIEW TALLY CHECKS PASSED")
