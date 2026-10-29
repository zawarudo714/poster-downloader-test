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

m = re.search(r"^  function updateTally\(.*?^  }\n", page, re.S | re.M)
ok(m is not None, "found updateTally in admin_review_images.js")

script = "\n".join([
    "const el = { innerHTML: '', textContent: '' };",
    "const $ = () => el;",
    "let decisions = new Map([[1, { action: 'approve' }], [2, { action: 'rerun' }]]);",
    "let bareCount = 2; const step2On = true; const mode = 'waiting';",
    "const releasedCount = () => 1; const unmarkedCount = () => bareCount; const leftCount = () => 0;",
    m.group(0),
    "const out = {};",
    "updateTally(); out.missing = el.innerHTML;",
    "bareCount = 0; updateTally(); out.done = el.innerHTML;",
    "console.log(JSON.stringify(out));",
])
res = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
if res.returncode:
    print("FAIL node:", res.stderr)
    sys.exit(1)
out = json.loads(res.stdout)

# The positive control first: the counts themselves are still there.
ok(out["missing"].startswith("1 will be released · 1 repaint"),
   f"the tally still counts what SAVE will do (got {out['missing']!r})")
ok('<span class="review-tally-bare">2 NOT MARKED YET</span>' in out["missing"],
   f"unmarked pictures are counted in red (got {out['missing']!r})")
ok('review-tally-done' in out["done"] and "NOT MARKED" not in out["done"],
   f"with nothing missed it says so (got {out['done']!r})")
print("ALL REVIEW TALLY CHECKS PASSED")
