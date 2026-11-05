"""
The designs ready for Junique, as JSON, for the laptop's fetch tool (v296).

READ ONLY. The owner's laptop runs it over SSH (`tools/junique/fetch.py`),
because the Junique record stays on the laptop for now (DECIDED 2026-11-05,
docs/DECISIONS.md) and the laptop already holds the server's SSH key, so no
token has to be set up for a tool that is still being tried out.

A design is listed when its picture is approved, current, and its print file
was made with today's enlargement settings. The last part matters: Junique
refuses an old plain enlargement as "upscaled", so a file still waiting for
`app/print_remake.py` is left out rather than fetched and refused.

Order is approval order, then id, so "the next 20" means the same 20 on
every run. The key is `saved_poster_id`, which a re-approved generation
keeps, and which `tools/junique/MANUAL_UPLOADS.csv` also uses.

    docker compose exec -T -w /app web python -m app.junique_list
"""
from __future__ import annotations

import json
import sys

from .models import MasterTitle, ProcessedImage, SavedPoster

TITLE_LIMIT = 60          # Junique's title box counts to 60 (recording, 2026-11-05)


def junique_title(name: str) -> str:
    """The place name, cut at a word if it is over Junique's 60."""
    name = " ".join((name or "").split())
    if len(name) <= TITLE_LIMIT:
        return name
    cut = name[:TITLE_LIMIT + 1].rsplit(" ", 1)[0]   # +1: a word ending at 60 stays
    return cut.rstrip(" ,-")


def ready(db) -> list[dict]:
    from .print_remake import outdated

    old = {r.id for r in outdated(db)}
    rows = (db.query(ProcessedImage, SavedPoster, MasterTitle)
              .join(SavedPoster, SavedPoster.id == ProcessedImage.saved_poster_id)
              .join(MasterTitle, MasterTitle.id == SavedPoster.master_title_id)
              .filter(ProcessedImage.is_current == 1,
                      ProcessedImage.review_status == "approved",
                      ProcessedImage.storage_path != "",
                      SavedPoster.deleted_at.is_(None))
              .order_by(ProcessedImage.reviewed_at, ProcessedImage.id).all())
    return [{"saved_poster_id": sp.id,
             "processed_image_id": pi.id,
             "title": junique_title(mt.title),
             "storage_path": pi.storage_path,
             "approved_at": pi.reviewed_at.isoformat() if pi.reviewed_at else None}
            for pi, sp, mt in rows if pi.id not in old]


def main() -> int:
    from .db import SessionLocal
    db = SessionLocal()
    try:
        out = {"designs": ready(db)}
    finally:
        db.rollback()          # read only
        db.close()
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
