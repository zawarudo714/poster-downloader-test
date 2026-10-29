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

HELPERS = ("versionsOf", "step2Versions", "paintingVersions", "hasStep2",
           "deciderVersions", "shownVersion", "shownPainting", "keepRefused")


def keep_on_a_bare_painting(page: str) -> dict:
    """KEEP pressed on a picture with only paintings, with the step 2 switch
    ON and then OFF — the switch is set here, not assumed from the fixture.
    Until v287 this test only had a picture with no step 2 VERSION, which
    says nothing about whether step 2 is switched on."""
    script = "\n".join([
        "const chosen = new Map(); const chosenPainting = new Map();",
        "const status = { textContent: '' }; const $ = () => status;",
        *[re.search(r"^  function " + n + r"\(.*?^  }\n", page, re.S | re.M).group(0)
          for n in HELPERS],
        "const p3 = { processed_id: 3, stage: 1 }, p4 = { processed_id: 4, stage: 1 };",
        "const s4 = { processed_id: 5, stage: 2, step2_from: 4 };",
        "const titles = [{ images: [{ poster_id: 1, processed_id: 5, versions: [p3, p4, s4] },",
        "                           { poster_id: 2, processed_id: 4, versions: [p3, p4] }] }];",
        "let step2On = true; const out = {};",
        "out.bareOn = keepRefused(2, 'approve');",
        "out.withStep2On = keepRefused(1, 'approve');",
        "out.rerunOn = keepRefused(2, 'rerun');",
        "out.said = status.textContent;",
        "step2On = false;",
        "out.bareOff = keepRefused(2, 'approve');",
        "console.log(JSON.stringify(out));",
    ])
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
    if res.returncode:
        print("FAIL node:", res.stderr)
        sys.exit(1)
    return json.loads(res.stdout)


def keep_checks(k: dict) -> list[str]:
    broke = []
    if k["bareOff"] is not False:
        broke.append("with step 2 OFF, KEEP on a painting is allowed")
    if k["bareOn"] is not True or "REDO STEP 2" not in k["said"]:
        broke.append("with step 2 ON, KEEP on a painting is refused, saying why")
    if k["withStep2On"] is not False or k["rerunOn"] is not False:
        broke.append("with step 2 ON, KEEP on a step 2 picture and REPAINT still work")
    return broke


script = "\n".join([
    "const chosen = new Map(); const chosenPainting = new Map();",
    *[lift(n) for n in HELPERS if n != "keepRefused"],
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
res = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8")
if res.returncode:
    print("FAIL node:", res.stderr)
    sys.exit(1)
out = json.loads(res.stdout)

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
# The positive control first: without step 2, a painting IS the decided picture.
# (Which picture is SHOWN only. Whether KEEP may release it depends on the
# step 2 switch, and is checked against keepRefused at the end.)
ok(out["twoRight"] == 4 and out["twoBar"] == [3, 4],
   f"with no step 2 version the paintings are shown as before (got {out['twoRight']}, {out['twoBar']})")
ok(out["twoMiddle"] is None, "with no step 2 version there is no middle picture")
ok(out["right"] == 5, "the right-hand picture is the step-2 one")
ok(out["middle"] == 4, "the middle shows the painting behind it")
ok(out["rightAfterOldPick"] == 5,
   f"a remembered painting pick cannot become what KEEP releases (got {out['rightAfterOldPick']})")
ok(out["middlePicked"] == 3, "a painting picked under the middle is shown there")
ok(out["rightAfterPaintingPick"] == 5, "picking a painting leaves KEEP on step 2")
ok(out["rightBar"] == [5], f"the right-hand bar lists only step-2 versions (got {out['rightBar']})")
ok(out["goneMiddle"] is None, "a discarded painting leaves the middle empty")

# ── KEEP ON A PICTURE THAT HAS ONLY PAINTINGS ─────────────────────────────
k = keep_on_a_bare_painting(src)
shipped = keep_checks(k)
for rule in ("with step 2 OFF, KEEP on a painting is allowed",
             "with step 2 ON, KEEP on a painting is refused, saying why",
             "with step 2 ON, KEEP on a step 2 picture and REPAINT still work"):
    ok(rule not in shipped, f"{rule} (got {k})")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
# keepRefused without its step 2 test: the SAME checks must go red.
print("\n  sabotage — KEEP on a painting no longer refused while step 2 is on")
line = "if (!img || hasStep2(img)) return false;"
ok(src.count(line) == 1, "the line is where the sabotage expects it")
ok("with step 2 ON, KEEP on a painting is refused, saying why"
   in keep_checks(keep_on_a_bare_painting(src.replace(line, "return false;"))),
   "the check catches it")
print("\nALL REVIEW SPLIT CHECKS PASSED")
