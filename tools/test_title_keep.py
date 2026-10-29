"""
Behaviour test: the title-level KEEP key on the review screen sets every
picture of the title, and never flips a KEEP already made back off (v275).
With step 2 on, a picture that has only its painting is never marked KEEP
(v287) — the server refuses it too. Lifts keepRefused / decide /
toggleDecision / decideThisTitle out of
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
    "const t2 = { images: [{ poster_id: 3, s2: true }, { poster_id: 4 }] };",
    "const titles = [t, t2];",
    "let on = t;",
    "const current = () => on;",
    "let step2On = false;",
    "const hasStep2 = (img) => !!img.s2;",
    "const $ = () => null;",
    lift("keepRefused"), lift("decide"), lift("toggleDecision"), lift("decideThisTitle"),
    "const out = [];",
    "const snap = () => JSON.stringify([...decisions].map(([k, v]) => [k, v.action]));",
    "decisions.set(1, { action: 'approve', reason: '' });",
    "decideThisTitle('approve'); out.push(snap());",
    "decideThisTitle('approve'); out.push(snap());",
    "decideThisTitle('unusable'); out.push(snap());",
    "out.push(String(decisions.get(2) && decisions.get(2).reason));",
    "step2On = true; on = t2;",
    "decideThisTitle('approve'); out.push(snap());",
    "decide(4, 'approve'); out.push(snap());",
    "decide(4, 'step2'); out.push(snap());",
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
base = '[1,"unusable"],[2,"unusable"]'
ok(out[4] == f'[{base},[3,"approve"]]',
   f"step 2 on: title KEEP marks only the picture that has a step 2 (got {out[4]})")
ok(out[5] == out[4], f"step 2 on: KEEP on a bare painting is refused (got {out[5]})")
ok(out[6] == f'[{base},[3,"approve"],[4,"step2"]]',
   f"REDO STEP 2 on that painting still goes through (got {out[6]})")
print("ALL TITLE KEEP CHECKS PASSED")
