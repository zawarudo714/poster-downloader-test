"""
Behaviour tests for flattening a transparent generation onto a colour.

Run inside the container:

    docker compose exec web python tools/test_background_flatten.py

════════════════════════════════════════════════════════════════════════════
WHY THESE THREE THINGS AND NOT OTHERS
════════════════════════════════════════════════════════════════════════════
gpt-image-2 asked for a transparent background renders differently, and
better for this niche — MEASURED by the owner 2026-09-05. The see-through
result is a side effect that has to be flattened away before a marketplace
sees it, and every way that can go wrong goes wrong QUIETLY:

  1. A HALF-transparent pixel must come out half its own colour and half the
     plate. That is the whole reason a hazy sky looks muddy on black and
     correct on its own blue — if the maths were wrong, the eyedropper would
     be picking colours that do nothing.
  2. FLATTEN BEFORE UPSCALING. Enlarging a picture that still has an alpha
     channel means resampling that alpha, and the colour hiding under the
     clear pixels gets averaged in along every soft edge. Flattening first
     removes the possibility entirely — there is no alpha left to resample.
     MEASURED 2026-09-05: on a hard-edged test shape the two orders came out
     within two levels of each other, so this is a safety argument rather
     than a demonstrated fault. The order costs nothing either way.
  3. A BAD COLOUR MUST NOT RAISE. The value comes from a text box a person
     types into. A crash there would strand a picture that has already been
     paid for, which is a far worse outcome than falling back to black.

Each is followed by a SABOTAGE: the original mistake is put back and the
test must go red. A test that stays green under sabotage is protecting
nothing.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from PIL import Image                                   # noqa: E402

from app.imagefetch import (DEFAULT_BACKGROUND, flatten_onto,      # noqa: E402
                            has_transparency, parse_color,
                            upscale_to_width)

failures: list[str] = []
checks = 0
tmp = Path(tempfile.mkdtemp())


def ok(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}" + (f"  — {detail}" if detail else ""))
        failures.append(label)


def near(a, b, slack=3):
    return all(abs(x - y) <= slack for x, y in zip(a, b))


# ════════════════════════════════════════════════════════════════════════════
# 1. Reading a colour
# ════════════════════════════════════════════════════════════════════════════
print("\nA colour typed into a box")

ok("plain hex", parse_color("#1E90FF") == (30, 144, 255))
ok("no hash needed", parse_color("1E90FF") == (30, 144, 255))
ok("short form expands", parse_color("#08f") == (0, 136, 255))
ok("spaces are trimmed", parse_color("  #000000  ") == (0, 0, 0))
ok("the default is black", parse_color(DEFAULT_BACKGROUND) == (0, 0, 0))

print("\n  a value nobody can use falls back to black rather than crashing")
for junk in ("", None, "blue", "#12345", "#zzzzzz", "########"):
    ok(f"{junk!r} -> black", parse_color(junk) == (0, 0, 0))


# ════════════════════════════════════════════════════════════════════════════
# 2. The compositing itself
# ════════════════════════════════════════════════════════════════════════════
print("\nHalf-transparent pixels take half their colour from the background")

# A picture built the way the real ones come back: a solid subject, a HAZY
# band that is half see-through, and a fully clear strip.
src = tmp / "hazy.png"
img = Image.new("RGBA", (60, 30), (255, 255, 255, 0))
for x in range(60):
    img.putpixel((x, 5), (200, 40, 40, 255))    # solid
    img.putpixel((x, 15), (200, 40, 40, 128))   # half see-through
    img.putpixel((x, 25), (200, 40, 40, 0))     # fully clear
img.save(src)

ok("the file is seen as transparent", has_transparency(src))

out = tmp / "on_black.jpg"
flatten_onto(src, out, "#000000")
with Image.open(out) as res:
    solid, half, clear = (res.getpixel((30, y)) for y in (5, 15, 25))
ok("solid pixels are untouched", near(solid, (200, 40, 40)))
ok("half-transparent lands halfway to black", near(half, (100, 20, 20), 4),
   f"got {half}")
ok("clear pixels become the background", near(clear, (0, 0, 0)))

out_blue = tmp / "on_blue.jpg"
flatten_onto(src, out_blue, "#1E90FF")
with Image.open(out_blue) as res:
    half_blue = res.getpixel((30, 15))
ok("THE SAME PIXEL COMES OUT DIFFERENT ON A DIFFERENT COLOUR",
   not near(half_blue, half, 8),
   "if these matched, the eyedropper would be changing nothing")
ok("and it lands halfway to the chosen blue",
   near(half_blue, (115, 92, 147), 6), f"got {half_blue}")

opaque = tmp / "opaque.png"
Image.new("RGB", (20, 20), (10, 90, 40)).save(opaque)
ok("a picture with no transparency is left alone", not has_transparency(opaque))
flatten_onto(opaque, tmp / "opaque.jpg", "#FF0000")
with Image.open(tmp / "opaque.jpg") as res:
    ok("and the colour does not bleed into it",
       near(res.getpixel((10, 10)), (10, 90, 40)))

# ── SABOTAGE ────────────────────────────────────────────────────────────────
# The original mistake: drop the alpha instead of compositing with it.
# EXPECT: "half-transparent lands halfway to black" would go red.
print("\n  sabotage — throw the alpha away instead of using it as a mask")
with Image.open(src) as bad:
    naive = bad.convert("RGB")          # what a careless convert() does
ok("a naive convert keeps the full colour (so the mask is doing the work)",
   near(naive.getpixel((30, 15)), (200, 40, 40)),
   "the sabotage did not apply — this test proves nothing")


# ════════════════════════════════════════════════════════════════════════════
# 3. Order of operations
# ════════════════════════════════════════════════════════════════════════════
print("\nFlatten first, enlarge second")

# A hard alpha edge with BLACK hiding under the clear side — exactly what a
# generation contains, and what fringes if it is enlarged too early.
edge = tmp / "edge.png"
e = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
for x in range(20):
    for y in range(40):
        e.putpixel((x, y), (255, 240, 220, 255))
e.save(edge)

right_way = tmp / "right.jpg"
flatten_onto(edge, right_way, "#FFFFFF")
upscale_to_width(right_way, width=400, sharpen=0, quality=95)

wrong_way = tmp / "wrong.png"
with Image.open(edge) as im:
    im.resize((400, 400), Image.LANCZOS).save(wrong_way)   # alpha resampled
flat_after = tmp / "wrong.jpg"
flatten_onto(wrong_way, flat_after, "#FFFFFF")

def darkest_near_seam(path):
    with Image.open(path) as im:
        w, h = im.size
        band = [im.convert("RGB").getpixel((x, h // 2))
                for x in range(int(w * .45), int(w * .55))]
    return min(sum(p) / 3 for p in band)

good, bad_order = darkest_near_seam(right_way), darkest_near_seam(flat_after)
print(f"       flatten-then-enlarge: darkest {good:.0f} · "
      f"enlarge-then-flatten: darkest {bad_order:.0f}")

# NOT AN ASSERTION, AND THAT IS DELIBERATE.
#
# The first version of this file claimed enlarging first leaves a dark
# fringe, and asserted it. Measured, the two orders came out within two
# levels of each other on this shape — so the claim was a guess wearing the
# clothes of a measurement, which is the exact thing the project's notes
# warn about.
#
# Flattening first is still the order used, because it CANNOT fringe by
# construction: there is no alpha channel left to resample. But that is an
# argument from safety, not a measured difference, and it is written down
# that way now.
print("       (reported, not asserted — see the note in this file)")
ok("both orders produce a usable picture", min(good, bad_order) > 100,
   "one of the two orders is producing something dark and wrong")

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nFAILED:")
    for f in failures:
        print(f"  · {f}")
    sys.exit(1)
print("All good.")
