"""
The owner's signature, painted onto the finished print file.

════════════════════════════════════════════════════════════════════════════
WHERE THE NUMBERS COME FROM — MEASURED, NOT CHOSEN
════════════════════════════════════════════════════════════════════════════
The owner placed it by hand in Photoshop on 2026-09-09 and sent the transform
box: on a 4000 × 6000 canvas the signature was 672 wide, its right edge 20
pixels from the right and its bottom edge 20 pixels from the bottom, at 35%
opacity.

Every one of those is stored as a PERCENTAGE of the canvas rather than as a
pixel count:

    672 / 4000 = 16.8%   the width
     20 / 4000 =  0.5%   the margin

That is the whole reason this file has no pixel constants in it. `upscale_
width_px` is a dashboard setting he can change, and a signature pinned at
"20 pixels from the edge" would silently drift to a different-looking margin
the day he changes the output size. A percentage means the poster looks the
same at any size, which is what he actually asked for.

════════════════════════════════════════════════════════════════════════════
IT IS PAINTED AFTER THE ENLARGEMENT, ON PURPOSE
════════════════════════════════════════════════════════════════════════════
The artwork is generated small and enlarged to print size with Lanczos. If
the signature went on BEFORE that, it would be enlarged too and its thin
strokes would come out soft — a 672-wide mark drawn at 1024 and blown up
four times is not the same thing as one drawn at 4000.

So `render_print_file()` enlarges first, paints the signature at full print
resolution, and encodes the JPEG ONCE. Encoding twice is what the quality
work of 2026-09-06 was about, and adding a signature must not undo it.

════════════════════════════════════════════════════════════════════════════
WHITE OR BLACK, DECIDED BY A PERSON
════════════════════════════════════════════════════════════════════════════
The owner's file is white strokes on transparency. White at 35% disappears
on a snowy corner, so the review screen carries a switch per poster and this
module simply does as it is told. It does NOT sample the corner and guess:
the owner chose the switch over the guess on 2026-09-09, because a machine
picking differently from him on one poster in twenty is worse than a button.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# The shape of a per-poster override, and the only place its key names live.
# Stored as JSON in ProcessedImage.signature_json rather than as four columns,
# for the same reason PipelineJob.payload_json is free-form: the second thing
# somebody wants to adjust — rotation, a second mark, a per-account signature
# — should not need a migration.
KEYS = ("x_pct", "w_pct", "opacity", "dark", "off")


def settings_for(db, project) -> dict:
    """The project's defaults, as a plain dictionary."""
    from .pipeline import get_setting

    def num(key, fallback):
        try:
            return float(get_setting(db, key, project=project))
        except (TypeError, ValueError):
            return fallback

    return {
        "image": str(get_setting(db, "signature_image", project=project) or ""),
        "enabled": str(get_setting(db, "signature_enabled",
                                   project=project)) not in ("", "0", "false"),
        "w_pct": num("signature_width_pct", 16.8),
        "margin_pct": num("signature_margin_pct", 0.5),
        "opacity": num("signature_opacity", 35.0),
        "x_pct": num("signature_x_pct", 91.1),
        "dark": False,
    }


def placement(db, project, processed) -> Optional[dict]:
    """
    Where this poster's signature goes, or None for "do not paint one".

    The project's defaults, with the poster's own adjustments laid over the
    top. Reading them in that order is what lets the owner change the default
    for everything to come without disturbing the ones he has already nudged.
    """
    base = settings_for(db, project)
    if not base["enabled"] or not base["image"]:
        return None

    own = {}
    raw = getattr(processed, "signature_json", None)
    if raw:
        try:
            own = json.loads(raw) or {}
        except (TypeError, ValueError):
            own = {}          # unreadable overrides fall back to the defaults

    if own.get("off"):
        return None           # this one poster, deliberately unsigned

    out = dict(base)
    for key in ("x_pct", "w_pct", "opacity"):
        if isinstance(own.get(key), (int, float)):
            out[key] = float(own[key])
    out["dark"] = bool(own.get("dark"))
    return out


def load_mark(workspace: Path, rel: str):
    """The signature file as an RGBA image, or None if it is not there."""
    if not rel:
        return None
    path = workspace / rel
    if not path.is_file():
        return None
    from PIL import Image

    mark = Image.open(path)
    mark.load()
    return mark.convert("RGBA")


def paint(img, mark, place: dict):
    """
    Paste the signature onto a finished print image. Returns the same image.

    `img` is RGB at full print size. `mark` is the RGBA signature. `place`
    is what `placement()` returned.

    THE COLOUR IS REBUILT FROM THE ALPHA rather than tinted. The owner's file
    is white strokes, so tinting it black would do nothing — the pixels are
    already white and multiplying white by black gives black only if you get
    the blend mode right. Building a solid block of the wanted colour and
    borrowing the file's transparency is exact, works for either colour, and
    would still work if he ever uploads a coloured mark.
    """
    from PIL import Image

    if img.mode != "RGB":
        img = img.convert("RGB")

    W, H = img.size
    want_w = max(1, int(round(W * place["w_pct"] / 100.0)))
    want_h = max(1, int(round(mark.height * (want_w / mark.width))))
    sized = mark.resize((want_w, want_h), Image.LANCZOS)

    colour = (0, 0, 0) if place.get("dark") else (255, 255, 255)
    alpha = sized.split()[3]
    if place["opacity"] < 100:
        alpha = alpha.point(
            lambda a: int(a * max(0.0, min(100.0, place["opacity"])) / 100.0))
    block = Image.new("RGBA", sized.size, colour + (255,))
    block.putalpha(alpha)

    margin = int(round(W * place["margin_pct"] / 100.0))
    # X is the CENTRE of the mark as a percentage of the width, so dragging
    # it feels the same whatever size the mark is. Clamped so it can never be
    # dragged off the edge — the margin is a promise, not a suggestion.
    centre = W * place["x_pct"] / 100.0
    left = int(round(centre - want_w / 2.0))
    left = max(margin, min(W - margin - want_w, left))
    top = H - margin - want_h

    img.paste(block, (left, top), block)
    return img


def default_x_pct(w_pct: float, margin_pct: float) -> float:
    """
    The centre that puts the mark's right edge on the margin.

    Derived rather than stored as a second number, so the default cannot
    drift out of step with the width and the margin it is made of.
    """
    return 100.0 - margin_pct - (w_pct / 2.0)
