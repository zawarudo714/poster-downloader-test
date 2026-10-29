"""
Behaviour test: on Approve Artwork, number pad 6 and 4 MOVE right and left,
with Num Lock on or off, and REDO STEP 2 is on 3 (owner, 2026-10-29, v284).
REDO STEP 2 sat on 6 before, so with Num Lock on the owner's "move right"
marked a redo instead. Lifts the page's keydown handler and keys.js and
runs them in node against stubs. Run from the repo root:

    python tools/test_review_keys.py
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
page = (ROOT / "app" / "static" / "js" / "admin_review_images.js").read_text(encoding="utf-8")
keys = (ROOT / "app" / "static" / "js" / "keys.js").read_text(encoding="utf-8")
defaults = (ROOT / "app" / "pipeline.py").read_text(encoding="utf-8")
route = (ROOT / "app" / "routes" / "pipeline_admin.py").read_text(encoding="utf-8")

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

# One default, in all four places that state it.
m = re.search(r'"review_key_step2":\s*"([^"]*)"', defaults)
ok(m and m.group(1) == "3", f"the server default for REDO STEP 2 is 3 (got {m and m.group(1)})")
ok('"review_key_step2",\n                                 project=project) or "3")' in route,
   "the queue's fallback for REDO STEP 2 is 3")
ok("step2: '3' };" in page and "d.review_keys.step2 || '3'" in page,
   "the page's own defaults for REDO STEP 2 are 3")

if not shutil.which("node"):
    print("SKIP node is not installed")
    sys.exit(0)

h = re.search(r"^  document\.addEventListener\('keydown', \(e\) => \{\n.*?^  \}\);\n", page, re.S | re.M)
ok(h is not None, "found the page's keydown handler")

script = "\n".join([
    "const window = {};",
    keys,
    "const log = [];",
    "const stage = { hidden: false }; let zoomOpen = false; let eyedropFor = null;",
    "const reviewKeys = { rerun: '7', photoshop: '8', keep: '9', step2: '3' };",
    "const sigKeys = { left: ',', right: '.' }; let step2On = true;",
    "const move = (d) => log.push('move ' + d);",
    "const decideThisTitle = (a) => log.push('decide ' + a);",
    "const pickVersionByNumber = (n) => log.push('version ' + n);",
    "const current = () => null; const clearTitleMarks = () => {}; const stepVersion = () => {};",
    "let handler = null;",
    "const document = { addEventListener: (_t, fn) => { handler = fn; } };",
    h.group(0),
    "const press = (key, code) => { log.length = 0;",
    "  handler({ key, code, target: { matches: () => false }, preventDefault() {} });",
    "  return log.join(', '); };",
    "console.log(JSON.stringify({",
    "  pad6On: press('6', 'Numpad6'), pad6Off: press('ArrowRight', 'Numpad6'),",
    "  pad4On: press('4', 'Numpad4'), pad4Off: press('ArrowLeft', 'Numpad4'),",
    "  pad3On: press('3', 'Numpad3'), pad3Off: press('PageDown', 'Numpad3'),",
    "  top6: press('6', 'Digit6'), top3: press('3', 'Digit3'), pad9: press('9', 'Numpad9'),",
    "}));",
])
res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
if res.returncode:
    print("FAIL node:", res.stderr)
    sys.exit(1)
out = json.loads(res.stdout)

# The positive control first: an untouched decision key still decides.
ok(out["pad9"] == "decide approve", f"number pad 9 still keeps (got {out['pad9']!r})")
ok(out["pad6On"] == "move 1", f"number pad 6, Num Lock ON, moves right (got {out['pad6On']!r})")
ok(out["pad6Off"] == "move 1", f"number pad 6, Num Lock off, moves right (got {out['pad6Off']!r})")
ok(out["pad4On"] == "move -1", f"number pad 4, Num Lock ON, moves left (got {out['pad4On']!r})")
ok(out["pad4Off"] == "move -1", f"number pad 4, Num Lock off, moves left (got {out['pad4Off']!r})")
ok(out["pad3On"] == "decide step2", f"number pad 3, Num Lock ON, is REDO STEP 2 (got {out['pad3On']!r})")
ok(out["pad3Off"] == "decide step2", f"number pad 3, Num Lock off, is REDO STEP 2 (got {out['pad3Off']!r})")
# Since v287 the top-row 3 picks version 3 again: REDO STEP 2 answers on
# the number pad only, like 4 and 6.
ok(out["top3"] == "version 3", f"the top-row 3 picks version 3 (got {out['top3']!r})")
ok(out["top6"] == "version 6", f"the top-row 6 still picks version 6 (got {out['top6']!r})")
print("ALL REVIEW KEY CHECKS PASSED")
