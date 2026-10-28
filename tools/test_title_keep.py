"""
Behaviour test: the title-level KEEP key on the review screen sets every
picture of the title, and never flips a KEEP already made back off (v275).
Lifts decide / toggleDecision / decideThisTitle out of
admin_review_images.js and runs them in node with a stub screen.
Run from the repo root:

    python tools/test_title_keep.py
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "app" / "static" / "js" / "admin_review_images.js").read_text(encoding="utf-8")


def lift(name):
    m = re.search(r"^  function " + name + r"\(.*?^  }\n", src, re.S | re.M)
    if not m:
        print(f"FAIL cannot find function {name} in admin_review_images.js")
        sys.exit(1)
    return m.group(0)


if not shutil.which("node"):
    print("SKIP node is not installed")
    sys.exit(0)

script = "\n".join([
    "const decisions = new Map();",
    "let answer = 'blurry';",
    "const prompt = () => answer;",
    "const render = () => {};",
    "const t = { images: [{ poster_id: 1 }, { poster_id: 2 }] };",
    "const current = () => t;",
    lift("decide"), lift("toggleDecision"), lift("decideThisTitle"),
    "const out = [];",
    "const snap = () => JSON.stringify([...decisions].map(([k, v]) => [k, v.action]));",
    "decisions.set(1, { action: 'approve', reason: '' });",
    "decideThisTitle('approve'); out.push(snap());",
    "decideThisTitle('approve'); out.push(snap());",
    "decideThisTitle('unusable'); out.push(snap());",
    "out.push(String(decisions.get(2) && decisions.get(2).reason));",
    "console.log(JSON.stringify(out));",
])
res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
if res.returncode:
    print("FAIL node:", res.stderr)
    sys.exit(1)
import json
out = json.loads(res.stdout)

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
ok(out[0] == '[[1,"approve"],[2,"approve"]]',
   f"KEEP on a pair with one already KEEP marks both (got {out[0]})")
ok(out[1] == "[]", f"KEEP again when both are KEEP clears both (got {out[1]})")
ok(out[2] == '[[1,"unusable"],[2,"unusable"]]', f"UNUSABLE marks both after one question (got {out[2]})")
ok(out[3] == "blurry", "both carry the reason")
print("ALL TITLE KEEP CHECKS PASSED")
