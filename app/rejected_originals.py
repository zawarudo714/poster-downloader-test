"""
KEEP THE PICTURES THE OWNER TURNED DOWN (v302, owner 2026-11-17)

Why: a trial of an AI picture check (2026-11-17) could only be scored
against 200 past flags by re-downloading each original from the web,
because every path that removes a flagged picture deleted the file. Some
addresses had already died. From now on the file is moved to REJECTED_DIR
with one line in `index.jsonl` saying which poster, title and flag it was,
so a later test reads the owner's real decisions from disk.

Which pictures are kept: a WORKER's picture (never the admin's own pick)
that either carries a flag (any Revision row, open or closed) or that the
owner himself took off (`by_owner`). Everything else is deleted as before.

What it costs: disk, about 175 KB a picture, roughly 35 MB a year at
today's ~200 flags. Nothing reads the folder; deleting it loses only the
test data.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from . import config
from .models import Revision, SavedPoster


def should_keep(db: Session, sp: SavedPoster, *, by_owner: bool = False) -> bool:
    if sp is None or sp.added_by:
        return False
    if by_owner:
        return True
    return db.query(Revision.id).filter(Revision.saved_poster_id == sp.id).first() is not None


def remove_picture_file(db: Session, sp: SavedPoster, path: Path, *,
                        reason: str, by_owner: bool = False) -> None:
    """Take this picture's file off the worker's folder: kept in
    REJECTED_DIR when it carries the owner's judgment, else deleted.
    Keeping must never stop the removal, so any failure falls back to the
    old delete."""
    path = Path(path)
    if path.is_file() and should_keep(db, sp, by_owner=by_owner):
        try:
            now = datetime.utcnow()
            config.REJECTED_DIR.mkdir(parents=True, exist_ok=True)
            dest = config.REJECTED_DIR / f"{sp.id}-{now:%Y%m%d%H%M%S}-{path.name}"
            shutil.move(str(path), str(dest))
            flags = (db.query(Revision).filter(Revision.saved_poster_id == sp.id)
                       .order_by(Revision.id).all())
            line = {
                "file": dest.name, "poster_id": sp.id, "at": now.isoformat(timespec="seconds"),
                "reason": reason, "by_owner": by_owner, "worker": sp.username,
                "title": (sp.title_folder_path or "").split(". ", 1)[-1],
                "source_url": sp.source_url,
                "flags": [{"id": r.id, "comment": r.comment, "type": r.revision_type,
                           "verdict": r.admin_verdict} for r in flags],
            }
            with open(config.REJECTED_DIR / "index.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
            return
        except Exception:
            pass
    path.unlink(missing_ok=True)
