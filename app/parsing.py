"""
Parsing / sanitization helpers ported verbatim from the original PyQt desktop
app (main.py). Behaviour MUST match exactly — the spec calls these out as
"port exactly":

    - sanitize()
    - _extract_num()

Plus a small image-extension helper.
"""

import re


# Mirrors IMAGE_EXTS in main.py
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif)(\?.*)?$", re.IGNORECASE)


def sanitize(name: str) -> str:
    """
    Strip filesystem-illegal characters, and anything Windows would strip
    for us WITHOUT SAYING SO.

    ════════════════════════════════════════════════════════════════════════
    A TRAILING DOT IS THE ONE THAT CROSSES MACHINES
    ════════════════════════════════════════════════════════════════════════
    The nine characters in the regex are refused outright, so they are easy
    to remember. A trailing dot or space is different: Windows ACCEPTS the
    name and silently hands back a folder with the dot removed, while Linux
    keeps it exactly as written.

    That difference sits across the middle of this system. The Linux server
    writes the archive over SFTP, where `8. Washington, D.C.` keeps its dot.
    The Windows node then looks for `S:\...\8. Washington, D.C.` and Windows
    resolves that to the name WITHOUT the dot — a folder that is not there.
    A file the server had just written would be unfindable from the machine
    that has to upload it (found by the new title check, 2026-09-10).

    So the dot is removed HERE, once, at the only place a folder name is
    built. Both machines then agree, because neither of them is guessing.
    """
    cleaned = re.sub(r'[<>:"/\\|?*]', "", name)
    # REMOVING A CHARACTER LEAVES A HOLE. "Aoraki / Mount Cook" loses the
    # slash and becomes "Aoraki  Mount Cook" with two spaces — a folder name
    # nobody typed, impossible to match by eye against the title, and one
    # keystroke away from colliding with the single-spaced version. Collapse
    # the runs so the name is the one a person would have written.
    cleaned = " ".join(cleaned.split())
    # rstrip, not [:-1] — "Trailing..." ends in three of them.
    return cleaned.rstrip(". ")


def _extract_num(s: str) -> str:
    m = re.search(r'\d+', s.strip())
    return m.group() if m else ''


def folder_name_for(num: str, title: str, year: str = "") -> str:
    """
    Build the per-title folder name:
        "{num}. {Title} ({Year})"  — when the project has years
        "{num}. {Title}"           — when it does not

    ════════════════════════════════════════════════════════════════════════
    WHY THE PARENTHESES ARE CONDITIONAL
    ════════════════════════════════════════════════════════════════════════
    This used to be given `t.year or "N/A"`, so a travel folder was written
    to disk as `1. Santorini (N/A)`. That is the same poisoned "N/A" as the
    database column, except baked into a FOLDER PATH — and folder paths are
    immutable here by design, so it would have been permanent for every
    title created.

    A missing year now means no brackets at all, which is what a project
    with `has_year = 0` should have had from the start.

    **This changes the name of folders created from now on.** Existing
    folders are untouched and still resolve, because a poster's path is
    stored at first save and never recomputed. The change lands with the
    reset to zero, so in practice nothing is straddling two conventions.
    """
    stem = f"{num}. {title}"
    if str(year or "").strip():
        stem = f"{stem} ({year})"
    return sanitize(stem)


def filename_for(title: str, count: int, src_url: str) -> str:
    """
    Build the saved image filename exactly like main.py's _on_download():
        "{Title} {count}{ext}"   (sanitized title)
    Extension is taken from the source URL, defaulting to .webp like the original.
    """
    safe_title = sanitize(title)
    m = re.search(r'\.(jpg|jpeg|png|webp|gif)', src_url, re.IGNORECASE)
    ext = m.group(0).lower() if m else '.webp'
    return f"{safe_title} {count}{ext}"
