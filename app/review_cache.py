"""
The Approve-Artwork review cache — a disposable local window on the archive.

The review screens display images this server wrote to the Storage Box but
cannot see directly, and re-fetching each one over SFTP on every arrow-key
press was the measured lag (2026-09-06). So each review image is kept as a
local file here, keyed by its archive path.

════════════════════════════════════════════════════════════════════════════
IT IS KEYED BY PATH, AND PATHS ARE REUSED — SO IT MUST BE CLEARED ON REWRITE
════════════════════════════════════════════════════════════════════════════
A poster's processed file has a DETERMINISTIC name, so a RERUN (or a colour
re-flatten) overwrites the same archive path with new bytes. The cache key
does not change, so without an explicit clear the screen keeps serving the
OLD picture — which is exactly the "rerun shows the same image" bug. Anything
that rewrites a processed file MUST call `clear()` for its paths in the same
breath. Lives in its own module so both the writer (gpt_worker) and the
reader (routes/pipeline_admin) use one definition and cannot drift.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path("review_cache")

# Every variant a review path is stored under. clear() wipes all of them for
# a path, so a rewrite cannot leave one stale copy behind under some suffix.
VARIANTS = ("raw", "disp", "dispw", "full")


def cache_file(rel_path: str, variant: str) -> Path:
    key = hashlib.sha1(f"{variant}:{rel_path}".encode("utf-8")).hexdigest()
    return ROOT / key[:2] / f"{key}.bin"


def clear(rel_paths) -> int:
    """Forget every cached variant of each given archive path. Returns how
    many files were removed. Never raises — a cache that cannot be cleared
    must not break the write it is attached to."""
    removed = 0
    for rel in rel_paths:
        if not rel:
            continue
        for variant in VARIANTS:
            f = cache_file(rel, variant)
            try:
                if f.is_file():
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    return removed
