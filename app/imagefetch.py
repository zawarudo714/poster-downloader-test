"""
Fetching and normalising an image the worker picked out of a search grid.

════════════════════════════════════════════════════════════════════════════
WHY THE SERVER FETCHES, NOT THE BROWSER
════════════════════════════════════════════════════════════════════════════
The worker clicks SAVE and the server downloads immediately. Three reasons,
all of which came out of things that actually go wrong:

  1. Brave's thumbnail URLs are not guaranteed to live forever. A worker who
     searches, wanders off for twenty minutes and then saves would otherwise
     hand us a dead URL. Fetching on the click closes that window to seconds.
  2. A URL that returns an HTML error page will happily be saved as `.jpg`
     by anything that trusts the extension. We check the magic bytes instead,
     so a page of HTML is rejected rather than stored as a broken image.
  3. Brave serves a lot of WebP. Photoshop, GPT and FineArtAmerica all prefer
     JPEG, and converting once at the door beats discovering it three stages
     later.

════════════════════════════════════════════════════════════════════════════
PILLOW
════════════════════════════════════════════════════════════════════════════
The Photoshop pipeline deliberately avoided Pillow — the worker node reads
JPEG dimensions by parsing the SOF header rather than taking a dependency.
That made sense for a Windows box doing one job.

This runs on the Linux server and needs real image work: WebP decoding,
format conversion, and later the Lanczos upscale to print size. Reimplementing
that by hand would be worse than the dependency.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import requests

log = logging.getLogger("uvicorn.error")

TIMEOUT_S = 20
MAX_BYTES = 25 * 1024 * 1024

# First bytes of the formats we accept. Checked instead of trusting the URL's
# extension or the server's Content-Type, both of which lie routinely.
_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"BM": "bmp",
}


class FetchError(Exception):
    """Download or decode failed, with text safe to show a worker."""


def sniff_format(head: bytes) -> str | None:
    for magic, name in _MAGIC.items():
        if head.startswith(magic):
            return name
    # WebP is RIFF....WEBP — the size field sits between the two markers.
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def download_bytes(url: str) -> bytes:
    """Stream a URL into memory with a hard size cap."""
    try:
        with requests.get(
            url, stream=True, timeout=TIMEOUT_S,
            headers={"User-Agent": "Mozilla/5.0 PosterDownloader/1.0"},
        ) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            for chunk in resp.iter_content(64 * 1024):
                if not chunk:
                    continue
                buf.write(chunk)
                if buf.tell() > MAX_BYTES:
                    raise FetchError("That image is larger than 25 MB.")
            return buf.getvalue()
    except FetchError:
        raise
    except requests.RequestException as e:
        raise FetchError(f"Could not download that image: {e}")


def fetch_as_jpeg(url: str, target_path: Path, *, quality: int = 92) -> tuple[int, int, int]:
    """
    Download `url` and write it to `target_path` as JPEG.

    Returns (bytes_written, width, height).

    Raises FetchError with worker-readable text — this runs in response to a
    click, so the message goes straight back to the person who clicked.
    """
    data = download_bytes(url)
    fmt = sniff_format(data[:16])
    if fmt is None:
        # Almost always an HTML error page or a hotlink block.
        raise FetchError(
            "That link didn't return an image. It may have expired — "
            "search again and pick another."
        )

    try:
        from PIL import Image
    except ImportError:
        raise FetchError("Image processing isn't available on the server. Tell the admin.")

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise FetchError(f"That image could not be read ({e}).")

    # Flatten transparency onto white rather than letting it become black,
    # which is what a naive RGBA->RGB conversion does and looks like a
    # printing fault rather than a source problem.
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        from PIL import Image as _Image
        flat = _Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(target_path, "JPEG", quality=quality, optimize=True, progressive=True)
    return target_path.stat().st_size, img.width, img.height


DEFAULT_BACKGROUND = "#000000"


def parse_color(text: str | None) -> tuple[int, int, int]:
    """
    A hex colour to RGB, falling back to black rather than raising.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS NEVER RAISES
    ════════════════════════════════════════════════════════════════════════
    The value arrives from a text box on a dashboard, so it can be anything
    a person can type. The cost of getting it wrong is not symmetrical: a
    crash here stops a finished picture — already paid for — from reaching
    storage, while a wrong colour merely produces a poster the admin is
    about to look at anyway and can fix in one click.

    So a bad value quietly becomes black, which is the default the owner
    wanted in the first place.

    Accepts #rgb, #rrggbb, with or without the hash.
    """
    raw = (text or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6:
        return (0, 0, 0)
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def flatten_onto(src: Path, dest: Path, color: str | None) -> tuple[int, int]:
    """
    Put a solid colour behind a picture that may be see-through, and save it
    as a JPEG. Returns the size.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS AT ALL
    ════════════════════════════════════════════════════════════════════════
    gpt-image-2 asked for a transparent background renders differently, and
    better for this niche — MEASURED by the owner 2026-09-05. The see-through
    result is a side effect, not the goal, and it cannot go to a marketplace
    as it is.

    ════════════════════════════════════════════════════════════════════════
    IT MUST HAPPEN BEFORE THE UPSCALE
    ════════════════════════════════════════════════════════════════════════
    Enlarging a picture that still has an alpha channel means resampling that
    alpha, and the colour hiding UNDER the clear pixels gets averaged in
    along every soft edge. Flattening first removes the possibility outright:
    there is no alpha left to resample.

    MEASURED 2026-09-05 — and the measurement is weaker than the reasoning.
    On a hard-edged test shape the two orders came out within two levels of
    each other, so this is not a fault anybody has SEEN here. It is the safe
    order, it costs nothing, and that is the whole case for it. Do not repeat
    the fringing claim as though it had been observed.

    A picture with no transparency at all passes through this unchanged
    apart from the format, so it is safe to call on anything.
    """
    from PIL import Image

    with Image.open(src) as img:
        img.load()
        if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
            plate = Image.new("RGB", img.size, parse_color(color))
            # The alpha channel is the mask, so a HALF-transparent pixel comes
            # out half its own colour and half the plate — which is exactly
            # why a semi-transparent sky reads muddy on black and correct on
            # its own blue.
            plate.paste(img, mask=img.split()[-1])
            img = plate
        else:
            img = img.convert("RGB")

        dest.parent.mkdir(parents=True, exist_ok=True)
        # A .png destination means "keep this lossless" — it is an
        # intermediate on the way to the upscale, not the delivered file.
        if dest.suffix.lower() == ".png":
            img.save(dest, "PNG")
        else:
            img.save(dest, "JPEG", quality=95, subsampling=0)
        return img.size


def has_transparency(path: Path) -> bool:
    """
    Is any part of this picture ACTUALLY see-through?

    ════════════════════════════════════════════════════════════════════════
    PIXELS, NOT FILE MODE (the owner's find, 2026-09-06)
    ════════════════════════════════════════════════════════════════════════
    The first version answered "is the file RGBA", and gpt-image-2 returns
    RGBA even when it painted a fully opaque poster. So the review screen
    offered a background colour on images the colour could never change —
    the owner set the plate red for dramatic effect and nothing happened,
    which is the exact dead-knob shape these notes keep warning about.
    A picture is transparent when at least one pixel's alpha is below 255.
    """
    from PIL import Image
    try:
        with Image.open(path) as img:
            if img.mode == "P" and "transparency" in img.info:
                return True
            if img.mode not in ("RGBA", "LA"):
                return False
            alpha_min, _ = img.split()[-1].getextrema()
            return alpha_min < 255
    except OSError:
        return False


def upscale_to_width(path: Path, *, width: int, sharpen: int = 0,
                     quality: int = 92, dest: "Path | None" = None,
                     overlay=None) -> tuple[int, int]:
    """
    Resize an image to `width`, height following in proportion, in place.

    Lanczos: the best of Pillow's resampling filters for upscaling, and the
    reason we generate small and enlarge here rather than paying OpenAI for a
    larger canvas — output size drives the token bill directly.

    `sharpen` is 0-100 and applied AFTER the resize. It defaults to off
    because sharpening artefacts are permanent and the review gate is the
    only place they would ever be caught.

    `overlay` is a callable given the finished full-size image, returning the
    image to save. It exists so the SIGNATURE can be painted at print
    resolution and still be encoded ONCE — putting it on before the
    enlargement would blow its thin strokes up four times and soften them,
    and encoding a second time to add it afterwards is exactly the
    double-encode the 2026-09-06 quality work removed.

    Returns the final (width, height). The enlargement is skipped when the
    image is already at least that wide — we never downscale a print file —
    but the overlay and the save still happen, because "already big enough"
    must not silently mean "no signature".
    """
    from PIL import Image, ImageFilter

    img = Image.open(path)
    img.load()
    if img.width >= width:
        if overlay is None and dest is None:
            return img.width, img.height
        img = img.convert("RGB")
        if overlay is not None:
            img = overlay(img)
        img.save(dest or path, "JPEG", quality=quality, optimize=True,
                 progressive=True, subsampling=0)
        return img.width, img.height

    height = max(1, round(img.height * (width / img.width)))
    img = img.convert("RGB").resize((width, height), Image.LANCZOS)

    if sharpen and sharpen > 0:
        # UnsharpMask is the controllable one; percent maps directly to the
        # dashboard's 0-100 so the number means something to the operator.
        img = img.filter(ImageFilter.UnsharpMask(
            radius=2, percent=int(sharpen), threshold=3))

    # ── QUALITY, TWO WAYS (2026-09-06) ───────────────────────────────────
    # subsampling=0 keeps full colour resolution (4:4:4). The default 4:2:0
    # throws away three quarters of the colour detail, and this artwork is
    # flat blocks meeting at hard edges — exactly where that shows, as
    # coloured fringing along every boundary.
    #
    # `dest` lets the caller enlarge FROM a lossless intermediate and write
    # the JPEG once, instead of encoding, re-reading and re-encoding: JPEG
    # ringing introduced at the small size would otherwise be magnified 4x
    # into the print file.
    # LAST, at full print resolution, and immediately before the ONE encode.
    if overlay is not None:
        img = overlay(img)

    target = dest or path
    img.save(target, "JPEG", quality=quality, optimize=True,
             progressive=True, subsampling=0)
    return img.width, img.height


def make_preview(src: Path, dest: Path, *, width: int = 1200, quality: int = 82) -> None:
    """
    Write a web-sized copy for the review screens.

    The review gate shows two images side by side and is arrow-keyed through
    hundreds of titles. Serving the 4000px print files would be ~6 MB per
    screen; a 1200px preview is ~120 KB, so a 250-artist session moves 59 MB
    instead of 3 GB. Generated once here rather than resized on every request.
    """
    from PIL import Image

    img = Image.open(src)
    img.load()
    if img.width > width:
        height = max(1, round(img.height * (width / img.width)))
        img = img.convert("RGB").resize((width, height), Image.LANCZOS)
    elif img.mode != "RGB":
        img = img.convert("RGB")

    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=quality, optimize=True, progressive=True)
