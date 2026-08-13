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


def upscale_to_width(path: Path, *, width: int, sharpen: int = 0,
                     quality: int = 92) -> tuple[int, int]:
    """
    Resize an image to `width`, height following in proportion, in place.

    Lanczos: the best of Pillow's resampling filters for upscaling, and the
    reason we generate small and enlarge here rather than paying OpenAI for a
    larger canvas — output size drives the token bill directly.

    `sharpen` is 0-100 and applied AFTER the resize. It defaults to off
    because sharpening artefacts are permanent and the review gate is the
    only place they would ever be caught.

    Returns the final (width, height). A no-op if the image is already at
    least that wide — we never downscale a print file.
    """
    from PIL import Image, ImageFilter

    img = Image.open(path)
    img.load()
    if img.width >= width:
        return img.width, img.height

    height = max(1, round(img.height * (width / img.width)))
    img = img.convert("RGB").resize((width, height), Image.LANCZOS)

    if sharpen and sharpen > 0:
        # UnsharpMask is the controllable one; percent maps directly to the
        # dashboard's 0-100 so the number means something to the operator.
        img = img.filter(ImageFilter.UnsharpMask(
            radius=2, percent=int(sharpen), threshold=3))

    img.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    return width, height


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
