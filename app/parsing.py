"""
Parsing / sanitization helpers ported verbatim from the original PyQt desktop
app (main.py). Behaviour MUST match exactly — the spec calls these out as
"port exactly":

    - sanitize()
    - _extract_num()
    - _extract_year()
    - parse_clipboard_data()

Plus a small image-extension helper.
"""

import re


# Mirrors IMAGE_EXTS in main.py
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMAGE_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif)(\?.*)?$", re.IGNORECASE)


def sanitize(name: str) -> str:
    """Strip filesystem-illegal chars. Same regex as main.py."""
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def _extract_num(s: str) -> str:
    m = re.search(r'\d+', s.strip())
    return m.group() if m else ''


def _extract_year(s: str) -> str:
    s = s.strip()
    m = re.search(r'\d{4}', s)
    if m:
        return m.group()
    # -1, 0, blank, or any non-4-digit value → treat as unknown
    return "N/A"


def parse_clipboard_data(raw: str):
    """
    Handles all paste formats from Excel.

    Pass 1: delimited lines (tab / comma / semicolon / pipe). Uses re.search so
            stray \\r, BOM, decimals like 134.0, extra trailing tabs, etc.
            cannot cause false negatives. Entries with missing/invalid year
            (e.g. -1, blank) are accepted with year='N/A' rather than dropped.

    Pass 2: fully concatenated blob (no delimiters at all). The 19xx/20xx
            anchor prevents entry numbers from being read as years.

    Returns: list of (num, title, year) tuples.
    """
    rows = []

    # Pass 1 – delimited
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = None
        for sep in ('\t', ',', ';', '|'):
            p = line.split(sep)
            if len(p) >= 3:
                parts = p
                break
        if not parts:
            continue
        num   = _extract_num(parts[0])
        title = parts[1].strip()
        year  = _extract_year(parts[2])
        if num and title:
            rows.append((num, title, year))

    if rows:
        return rows

    # Pass 2 – concatenated blob
    blob = re.sub(r'[\r\n\s]+', ' ', raw).strip()
    pat = re.compile(
        r'(\d{1,5})'
        r'([A-Za-z][^0-9]*)'
        r'((19|20)\d{2})'
        r'(?=\d|$)'
    )
    for m in pat.finditer(blob):
        num   = m.group(1).strip()
        title = m.group(2).strip()
        year  = m.group(3).strip()
        if title:
            rows.append((num, title, year))

    return rows


def folder_name_for(num: str, title: str, year: str) -> str:
    """
    Build the per-title folder name exactly like the original:
        "{num}. {Title} ({Year})"
    sanitized for filesystem.
    """
    return sanitize(f"{num}. {title} ({year})")


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
