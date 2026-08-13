"""
Repair posters left reading 'uploading' after their uploads actually finished.

════════════════════════════════════════════════════════════════════════════
WHY THEY ARE WRONG
════════════════════════════════════════════════════════════════════════════
report_uploaded() decides whether a poster is fully uploaded by counting how
many of its upload rows are still outstanding. That count ran BEFORE the
session was flushed, so it read the database as it was on disk — where the
row it had just marked 'uploaded' still said 'uploading'.

Every poster therefore counted itself as outstanding, never reached zero, and
stayed at 'uploading' forever. The uploads were real, the marketplace quota
went up, the listings are live; only the poster's own stage was left behind.

The flush is fixed. This corrects the rows that were written before it.

════════════════════════════════════════════════════════════════════════════
WHAT IT CHANGES
════════════════════════════════════════════════════════════════════════════
Nothing is inferred and nothing is uploaded. For each live poster it simply
re-runs the rule that report_uploaded() should have applied:

    every upload row for this poster is 'uploaded' (and there is at least
    one)                                    ->  poster is 'uploaded'

A poster with any row still pending, uploading or failed is left exactly as
it is — that one really is still in flight. Title rollups are recomputed
afterwards so the funnel and the title list agree.

    docker compose exec web python scripts/backfill_upload_status.py
    docker compose exec web python scripts/backfill_upload_status.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func                                       # noqa: E402

from app.db import SessionLocal                                   # noqa: E402
from app.models import MasterTitle, SavedPoster, UploadTracking   # noqa: E402
from app import pipeline as P                                     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Make the changes. Without it, report only.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # Only posters that think they are mid-upload. A poster already at
        # 'uploaded' needs nothing, and one at 'processed' has not been
        # dispatched yet.
        candidates = (
            db.query(SavedPoster)
              .filter(SavedPoster.pipeline_status == "uploading",
                      SavedPoster.deleted_at.is_(None))
              .all()
        )

        fixed = []
        for poster in candidates:
            total = (
                db.query(func.count(UploadTracking.id))
                  .filter(UploadTracking.saved_poster_id == poster.id)
                  .scalar() or 0
            )
            outstanding = (
                db.query(func.count(UploadTracking.id))
                  .filter(UploadTracking.saved_poster_id == poster.id,
                          UploadTracking.status.in_(("pending", "uploading", "failed")))
                  .scalar() or 0
            )
            if total and outstanding == 0:
                fixed.append(poster)

        if not fixed:
            print(f"{len(candidates)} poster(s) are 'uploading' and all of them "
                  f"genuinely have work outstanding. Nothing to repair.")
            return 0

        print(f"{len(fixed)} of {len(candidates)} poster(s) are finished but still "
              f"marked 'uploading':")
        for poster in fixed:
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            print(f"  · {title.title if title else '?'} — {poster.filename}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to correct them.")
            return 0

        for poster in fixed:
            poster.pipeline_status = "uploaded"
            title = db.query(MasterTitle).filter_by(id=poster.master_title_id).first()
            if title:
                P.recompute_title_status(db, title)
        db.commit()
        print(f"\nCorrected {len(fixed)}. The funnel should now show them as uploaded.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
