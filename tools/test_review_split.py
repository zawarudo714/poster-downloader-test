"""
Behaviour test: once a picture has a step-2 version, the review screen
splits its versions (v283, owner 2026-10-29). The right-hand picture —
the one KEEP releases — is always a step-2 one, whatever an old memory or
a painting pick says, because a painting never goes live while step 2 is
on. The middle shows the painting behind it, or the one picked under it.
Lifts the helpers out of admin_review_images.js and runs them in node.
Run from the repo root:

    python tools/test_review_split.py
"""
import json
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
    "const chosen = new Map(); const chosenPainting = new Map();",
    *[lift(n) for n in ("versionsOf", "step2Versions", "paintingVersions",
                        "hasStep2", "deciderVersions", "shownVersion",
                        "shownPainting")],
    # v3 and v4 paintings, and the step 2 made from v4 (the queue's pick).
    "const p3 = { processed_id: 3, stage: 1 }, p4 = { processed_id: 4, stage: 1 };",
    "const s4 = { processed_id: 5, stage: 2, step2_from: 4 };",
    "const img = { poster_id: 1, processed_id: 5, versions: [p3, p4, s4] };",
    "const out = {};",
    "out.right = shownVersion(img).processed_id;",
    "out.middle = shownPainting(img).processed_id;",
    "chosen.set(1, 4);",                 # a remembered pick of a painting
    "out.rightAfterOldPick = shownVersion(img).processed_id;",
    "chosen.delete(1); chosenPainting.set(1, 3);",
    "out.middlePicked = shownPainting(img).processed_id;",
    "out.rightAfterPaintingPick = shownVersion(img).processed_id;",
    "out.rightBar = deciderVersions(img).map((v) => v.processed_id);",
    # No step 2 yet: the screen works exactly as before.
    "const two = { poster_id: 2, processed_id: 4, versions: [p3, p4] };",
    "out.twoMiddle = shownPainting(two);",
    "out.twoBar = deciderVersions(two).map((v) => v.processed_id);",
    "out.twoRight = shownVersion(two).processed_id;",
    # The painting behind it was discarded: an empty middle, not a stand-in.
    "const gone = { poster_id: 3, processed_id: 5, versions: [p3, s4] };",
    "out.goneMiddle = shownPainting(gone);",
    "console.log(JSON.stringify(out));",
])
res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
if res.returncode:
    print("FAIL node:", res.stderr)
    sys.exit(1)
out = json.loads(res.stdout)

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
# The positive control first: without step 2, a painting IS the decided picture.
ok(out["twoRight"] == 4 and out["twoBar"] == [3, 4],
   f"without step 2 the paintings are decided as before (got {out['twoRight']}, {out['twoBar']})")
ok(out["twoMiddle"] is None, "without step 2 there is no middle picture")
ok(out["right"] == 5, "the right-hand picture is the step-2 one")
ok(out["middle"] == 4, "the middle shows the painting behind it")
ok(out["rightAfterOldPick"] == 5,
   f"a remembered painting pick cannot become what KEEP releases (got {out['rightAfterOldPick']})")
ok(out["middlePicked"] == 3, "a painting picked under the middle is shown there")
ok(out["rightAfterPaintingPick"] == 5, "picking a painting leaves KEEP on step 2")
ok(out["rightBar"] == [5], f"the right-hand bar lists only step-2 versions (got {out['rightBar']})")
ok(out["goneMiddle"] is None, "a discarded painting leaves the middle empty")
print("ALL REVIEW SPLIT CHECKS PASSED")
