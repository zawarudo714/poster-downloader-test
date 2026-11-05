"""
Behaviour test: the print file gets sharpen 80 + grain 4 by default (v294).

Junique refused plain Lanczos enlargements as "upscaled"; sharpen 80 + a
fine grey grain of strength 4 passed for every picture it had refused
(MEASURED 2026-11-05, docs/FAILURES.md). This checks:

  1. the defaults are 80 and 4, and every call that enlarges reads the grain
     setting (a call that forgot it would quietly make refusable files);
  2. grain 4 on a flat grey picture gives grey noise of about that strength,
     the same in all three colour channels (no coloured speckle);
  3. grain 0 leaves a flat picture flat, so the setting really turns it off;
  4. the signature overlay goes on after the grain, so it stays clean.

No database, no network. Run from the repo root:

    python tools/test_upscale_grain.py
"""
import re
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import pipeline  # noqa: E402
from app.imagefetch import upscale_to_width  # noqa: E402

fails = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        fails.append(what)


check(pipeline.DEFAULTS.get("upscale_sharpen") == 80, "default sharpen is 80")
check(pipeline.DEFAULTS.get("upscale_grain") == 4, "default grain is 4")

# Every place that enlarges must read the grain setting and pass it on.
for rel in ("app/gpt_worker.py", "app/routes/pipeline_admin.py"):
    src = (ROOT / rel).read_text(encoding="utf-8")
    calls = re.findall(r"upscale_to_width\(([^)]*)\)", src)
    calls = [c for c in calls if "width=" in c]
    check(calls and all("grain=grain" in c for c in calls),
          f"{rel}: all {len(calls)} enlarge calls pass grain")

tmp = Path(tempfile.mkdtemp(prefix="pd_grain_"))
flat = tmp / "flat.png"
Image.new("RGB", (400, 600), (128, 128, 128)).save(flat)


def enlarge(grain, overlay=None):
    out = tmp / f"out_{grain}.jpg"
    upscale_to_width(flat, width=800, sharpen=80, grain=grain, quality=100,
                     dest=out, overlay=overlay)
    return Image.open(out).convert("RGB")


im = enlarge(4)
r, g, b = (list(c.getdata()) for c in im.split())
sd = statistics.pstdev(r)
check(3.0 <= sd <= 5.0, f"grain 4 gives noise of strength ~4 (measured {sd:.2f})")
same = sum(1 for x, y, z in zip(r, g, b) if abs(x - y) <= 2 and abs(y - z) <= 2)
check(same / len(r) > 0.99, f"grain is grey, not coloured ({same / len(r):.1%} of pixels)")

im0 = enlarge(0)
lo, hi = im0.convert("L").getextrema()
check(hi - lo <= 2, f"grain 0 leaves a flat picture flat (range {lo}-{hi})")

# The overlay paints a white box; after the grain it must stay pure white.
def box(img):
    img.paste((255, 255, 255), (0, 0, 100, 100))
    return img


ov = enlarge(4, overlay=box).crop((10, 10, 90, 90)).convert("L")
lo, hi = ov.getextrema()
check(lo >= 250, f"the signature goes on after the grain (box min {lo})")

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
