"""
Link the processed images already sitting on the storage box to their posters.

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════
4,865 images were painted by hand, before any of this existed, and copied to
the storage box with rclone. The database has never known they are there.

That is not dangerous on its own — what stops the pipeline uploading them a
second time is the UPLOAD HISTORY, which comes from the old tool's JSON and
needs no files at all. This is the other, smaller half: an index of where
each finished image actually lives, so the archive can be looked up, checked
and re-uploaded after an account ban without anyone hunting through folders.

════════════════════════════════════════════════════════════════════════════
WHY THE WORKER MACHINE HAS TO DO IT
════════════════════════════════════════════════════════════════════════════
The files are on the storage box, mounted as `S:` on the Windows machine.
The database is on the Linux server. Neither can see both.

So the machine that already has the drive lists what is on it and posts the
paths home, and the server — which is the only thing that knows what a
poster is — does the matching. Exactly the split the listing checker uses,
and for the same reason: a capability belongs to whatever already has it.

════════════════════════════════════════════════════════════════════════════
THE MATCHING RULE LIVES HERE AND NOWHERE ELSE
════════════════════════════════════════════════════════════════════════════
    S:/{site}/{project}/processed/{date}/{N. Title (Year)}/{stem}{suffix}.jpg
                                          └── external_id     └── poster

  · the folder's leading number is `MasterTitle.external_id` — the `0`
    column from the original CSV, the key everything in this system joins on
  · the filename with the output suffix stripped is the SOURCE poster's
    filename stem, so `E.T. 1_Painted.jpg` came from `E.T. 1.jpg`

`scripts/migrate_pipeline.py --processed-root` once held a second copy of
this rule for a local folder. That path is dead — the images are not on the
laptop any more — and two copies of one fact are two chances to drift, with
the newer one always winning silently.

════════════════════════════════════════════════════════════════════════════
IT NEVER OVERWRITES, AND IT NEVER GUESSES
════════════════════════════════════════════════════════════════════════════
A poster that already has a current processed image is left alone, even when
the file on disk is at a different path. The pipeline's own output is the
authoritative one; a stale hand-made file quietly replacing it would send
the wrong image to a marketplace. Those are REPORTED as conflicts instead.

A file that matches nothing is reported, never attached to a nearby poster.
The 44 titles the old Photoshop script truncated at the first dot land here,
and inventing a home for them would corrupt the audit trail — the honest
answer is that they are unprocessed work the pipeline should redo.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import MasterTitle, ProcessedImage, Project, SavedPoster

# "50. Pulp Fiction (1994)"  ->  50
FOLDER_ID = re.compile(r"^(\d+)[.\s]")

VERSION = "legacy-archive"
BY = "archive-index"


def strip_suffix(filename: str, suffix: str) -> str:
    """
    `E.T. 1_Painted.jpg` -> `E.T. 1`, given suffix `_Painted`.

    The extension goes too, because the source is a `.jpg` or a `.png` and
    the output is always a `.jpg` — matching on the stem is what makes that
    difference stop mattering.
    """
    stem = os.path.splitext(filename or "")[0]
    if suffix and stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    return stem


def parse_path(rel: str, suffix: str) -> Optional[tuple[int, str, str]]:
    """
    (external_id, poster stem, filename) out of a relative archive path.

    None when the path is not shaped like one of ours — which is a real
    case, not a defensive flourish: the storage box also holds whatever the
    owner has put there by hand.
    """
    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return None
    filename = parts[-1]
    if os.path.splitext(filename)[1].lower() not in (
            ".jpg", ".jpeg", ".png", ".webp"):
        return None
    found = FOLDER_ID.match(parts[-2])
    if not found:
        return None
    return int(found.group(1)), strip_suffix(filename, suffix), filename


def index_files(db: Session, project: Project, files: list[dict],
                suffix: str) -> dict:
    """
    Attach a batch of archive paths to their posters.

    `files` is [{"path": "2026-05-11/1. Title (1994)/Title 1_Painted.jpg",
                 "size": 812345}, …] — relative to
    `{storage_root}/{site}/{project}/processed`, which is the same layout
    the pipeline writes going forward, so nothing here is legacy-only.

    Returns what happened, in words the screen can use directly.
    """
    linked = already = conflict = unmatched = 0
    examples: list[dict] = []

    def note(kind: str, path: str, why: str) -> None:
        if len(examples) < 40:
            examples.append({"kind": kind, "path": path, "why": why})

    for item in files:
        rel = (item or {}).get("path") or ""
        parsed = parse_path(rel, suffix)
        if parsed is None:
            unmatched += 1
            note("not ours", rel, "not shaped like a processed image folder")
            continue
        ext_id, stem, filename = parsed

        title = (db.query(MasterTitle)
                   .filter(MasterTitle.external_id == ext_id).first())
        if title is None:
            unmatched += 1
            note("no title", rel, f"no title carries the number {ext_id}")
            continue

        poster = None
        for candidate in (db.query(SavedPoster)
                            .filter(SavedPoster.master_title_id == title.id,
                                    SavedPoster.deleted_at.is_(None)).all()):
            if os.path.splitext(candidate.filename or "")[0] == stem:
                poster = candidate
                break

        if poster is None:
            unmatched += 1
            # The dot-name bug is the overwhelmingly common cause and it has
            # a reliable tell, so the report says which rather than leaving
            # 44 mysteries for somebody to work out again.
            why = ("the old Photoshop script truncated this name at the "
                   "first dot, so the other posters for this title were "
                   "overwritten — the pipeline will redo them"
                   if "." in (title.title or "") else
                   "no live poster of this title has that filename")
            note("no poster", rel, why)
            continue

        current = (db.query(ProcessedImage)
                     .filter(ProcessedImage.saved_poster_id == poster.id,
                             ProcessedImage.is_current == 1).first())
        if current is not None:
            if (current.storage_path or "") == rel:
                already += 1
            else:
                # NEVER replaced. The pipeline's own output wins; a stale
                # hand-made file taking its place would put the wrong image
                # on a marketplace.
                conflict += 1
                note("conflict", rel,
                     f"this poster already points at {current.storage_path}")
            continue

        db.add(ProcessedImage(
            saved_poster_id=poster.id,
            project_id=project.id,
            storage_path=rel,
            filename=filename,
            file_size=item.get("size") or None,
            script_version=VERSION,
            processed_by=BY,
            is_current=1,
        ))
        linked += 1

    return {"linked": linked, "already": already, "conflict": conflict,
            "unmatched": unmatched, "examples": examples}


def counts(db: Session, project: Project) -> dict:
    """
    How much of the archive is indexed — DERIVED, never stored.

    There is no "total" column and no "done" column on purpose. A counter
    has to be kept correct by every path that touches it, and this is simply
    true or not: how many live posters have a current processed image, out
    of how many exist.
    """
    live = (db.query(SavedPoster.id)
              .join(MasterTitle, MasterTitle.id == SavedPoster.master_title_id)
              .filter(SavedPoster.deleted_at.is_(None),
                      MasterTitle.project_id == project.id).count())
    indexed = (db.query(ProcessedImage.id)
                 .join(SavedPoster,
                       SavedPoster.id == ProcessedImage.saved_poster_id)
                 .filter(ProcessedImage.is_current == 1,
                         ProcessedImage.project_id == project.id,
                         SavedPoster.deleted_at.is_(None)).count())
    from_archive = (db.query(ProcessedImage.id)
                      .filter(ProcessedImage.is_current == 1,
                              ProcessedImage.project_id == project.id,
                              ProcessedImage.script_version == VERSION).count())
    return {"posters": live, "indexed": indexed, "from_archive": from_archive,
            "missing": max(0, live - indexed)}


def archive_root(db: Session, project: Project) -> str:
    """
    Where on the storage box this project's finished images live.

    ════════════════════════════════════════════════════════════════════════
    RENDERED FROM THE WRITER'S OWN TEMPLATE, NOT REBUILT BY HAND
    ════════════════════════════════════════════════════════════════════════
    `storage_layout` is a dashboard setting — the owner can change where
    images are filed. A second, hardcoded copy of that path here would be
    correct exactly until the day he changed it, and then the reader would
    look in one place while the writer put things in another, with no error
    anywhere.

    So the layout is rendered with markers where the per-image parts go, and
    everything up to the first marker is the root. Change the setting and
    this follows it for free.
    """
    from . import pipeline as P

    mark = "\x00"
    rendered = P._render(
        P.get_setting(db, "storage_layout", project=project),
        {"project":      P._path_token(project.name or project.slug),
         "project_slug": project.slug,
         "site":         P._path_token(project.target_site or "unknown"),
         # Everything below varies per image, so the root ends at whichever
         # of them the template mentions first.
         "date": mark, "title_folder": mark, "filename": mark,
         "username": mark, "external_id": mark},
    ).replace("\\", "/")

    root = str(P.get_setting(db, "storage_root", project=project)).rstrip("/\\")
    head = rendered.split(mark)[0].strip("/")
    return f"{root}/{head}" if head else root
