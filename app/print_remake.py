"""
Rebuild print files made with old enlargement settings (v295).

WHY: Junique refused 4 of the first 10 posters as "upscaled" because the
4000-pixel print file was a plain Lanczos enlargement. v294 added sharpening
80 and grain 4 for new files; the 318 approved before that still had the old
ones (MEASURED 2026-11-05). The owner asked for all of them to be remade and
for FineArtAmerica to use the same files (DECIDED 2026-11-05).

It reuses `_build_print_file`, the one place a print file is made, so a
remade file is exactly what an approval today would make. That function
compares `print_treatment` on the row with the settings, so a row is never
remade twice and a remake that stops half way simply carries on next time.

The old file is overwritten, which is safe to undo: the see-through original
(`master_path`) is kept, and a rebuild with sharpening 0 and grain 0 makes
the old look again.

    docker compose exec -T -w /app web python -m app.print_remake --count
    docker compose exec -T -w /app web python -m app.print_remake [--limit N]
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy.orm import Session

from .models import (MasterTitle, ProcessedImage, Project, SavedPoster,
                     UploadTracking)

STOP_AFTER_FAILURES_IN_A_ROW = 5


def outdated(db: Session) -> list[ProcessedImage]:
    """Approved, current print files whose enlargement differs from the
    settings now. Those still waiting to go to FineArtAmerica come first, so
    they go up with the new file rather than the old one."""
    from . import pipeline as P
    from .routes.pipeline_admin import print_treatment

    rows = (db.query(ProcessedImage)
              .filter(ProcessedImage.is_current == 1,
                      ProcessedImage.review_status == "approved",
                      ProcessedImage.master_path.isnot(None),
                      ProcessedImage.master_path != "",
                      ProcessedImage.storage_path != "")
              .order_by(ProcessedImage.id).all())
    waiting = {pid for (pid,) in db.query(UploadTracking.processed_image_id)
               .filter(UploadTracking.status.in_(("pending", "relist")))}
    wanted_by_project: dict = {}
    out = []
    for row in rows:
        key = row.project_id
        if key not in wanted_by_project:
            project = db.query(Project).filter_by(id=key).first()
            g = lambda k, d: int(P.get_setting(db, k, project=project) or d)  # noqa: E731
            wanted_by_project[key] = print_treatment(
                g("upscale_width_px", 4000), g("upscale_sharpen", 0),
                g("upscale_grain", 0), g("upscale_jpeg_quality", 92))
        if (row.print_treatment or "") != wanted_by_project[key]:
            out.append(row)
    out.sort(key=lambda r: (r.id not in waiting, r.id))
    return out


def remake(db: Session, *, limit: int | None = None, emit=print) -> dict:
    """Rebuild each outdated print file, one commit per poster."""
    from . import pipeline as P
    from .imagefetch import normalise_background
    from .routes.pipeline_admin import _build_print_file

    todo = outdated(db)
    if limit is not None:
        todo = todo[:limit]
    counts = {"remade": 0, "failed": 0, "left_before": len(todo)}
    in_a_row = 0
    for n, row in enumerate(todo, 1):
        started = time.monotonic()
        try:
            poster = db.query(SavedPoster).filter_by(id=row.saved_poster_id).first()
            title = (db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
                     if poster else None)
            project = P.project_for_title(db, title) if title else None
            # The colour already baked in, so only the enlargement changes.
            color = (normalise_background(row.background_color)
                     or P.default_background(db, project))
            note = _build_print_file(db, row, color, project, title=title)
            db.commit()
            counts["remade"] += 1
            in_a_row = 0
            emit(f"{n}/{len(todo)} OK  {row.storage_path}  "
                 f"({note or 'nothing to do'}, {time.monotonic() - started:.1f} s)")
        except Exception as e:                 # noqa: BLE001 — reported, then counted
            db.rollback()
            counts["failed"] += 1
            in_a_row += 1
            emit(f"{n}/{len(todo)} FAILED  processed {row.id}: {e}")
            if in_a_row >= STOP_AFTER_FAILURES_IN_A_ROW:
                emit(f"Stopped: {in_a_row} failures in a row.")
                break
    counts["left_after"] = len(outdated(db))
    emit(f"Done: {counts}")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--count", action="store_true", help="only count, change nothing")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    from .db import SessionLocal
    db = SessionLocal()
    try:
        if args.count:
            print(f"{len(outdated(db))} print file(s) made with old enlargement settings")
            return 0
        counts = remake(db, limit=args.limit, emit=lambda s: print(s, flush=True))
        return 1 if counts["failed"] else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
