"""
Undo images processed by the WRONG stage, into the WRONG project's folder.

════════════════════════════════════════════════════════════════════════════
WHAT WENT WRONG
════════════════════════════════════════════════════════════════════════════
The Windows node claimed greenlit work from every active project rather than
only from projects whose processor is 'photoshop'. So MUSIK images — which
are meant to go through image generation on the server — were opened in
Photoshop, run through the movie project's painterly effect, and written to

    S:/fineartamerica/GR(Movie&Series)/processed/<date>/<title>/

instead of MUSIK's own folder. The GPT worker was claiming the same rows from
the other side at the same time.

The dispatcher is fixed (pipeline.NODE_PROCESSORS). This cleans up what ran
before the fix.

════════════════════════════════════════════════════════════════════════════
WHAT IT DOES
════════════════════════════════════════════════════════════════════════════
Finds every ProcessedImage whose stored path does not start with the prefix
its own project would produce today, and for each one:

  · deletes the ProcessedImage row          (the derivative is wrong)
  · deletes any pending/failed UploadTracking rows that point at it
  · returns the poster to 'greenlit' with attempts reset

The poster, its source file, the worker's pay and the activity log are all
untouched — this only removes a derivative that should never have been made.

It will NOT touch a row whose upload already SUCCEEDED. That is on the
marketplace now, and quietly deleting the record of it would leave a live
listing nothing in the database knows about. Those are reported for you to
take down by hand.

The files on the Storage Box are NOT deleted — this has no access to them and
guessing would be worse than leaving them. It prints the exact paths to
remove; they are harmless orphans until then.

════════════════════════════════════════════════════════════════════════════
USAGE
════════════════════════════════════════════════════════════════════════════
    python scripts/fix_crossproject_processing.py             # report only
    python scripts/fix_crossproject_processing.py --apply     # make changes

Dry run is the default, deliberately. Read the report first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal                                    # noqa: E402
from app.models import (                                           # noqa: E402
    MasterTitle, ProcessedImage, Project, SavedPoster, UploadTracking,
)
from app import pipeline as P                                      # noqa: E402


def owning_segments(db, title, poster, project: Project) -> str:
    """
    The leading path components this image SHOULD have, as the pipeline
    itself would produce them.

    Asks storage_path_for() rather than rebuilding the path here, because the
    layout is an editable setting — a hardcoded "{site}/{project}/processed/"
    would report every image as misfiled the day that setting changes.

    Only the first two components are compared. Everything after them (date,
    title folder, filename) legitimately differs, and the question being
    asked is solely "is this under the right project".
    """
    rel, _filename = P.storage_path_for(db, title, poster, project=project)
    return "/".join(rel.replace("\\", "/").split("/")[:2])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually make the changes. Without it, report only.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        rows = (
            db.query(ProcessedImage, SavedPoster, MasterTitle)
              .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
              .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
              .all()
        )

        misfiled, protected = [], []
        wanted: dict[int, str] = {}
        for processed, poster, title in rows:
            owner = P.project_for_title(db, title)
            want = owning_segments(db, title, poster, owner)
            wanted[processed.id] = want
            path = (processed.storage_path or "").replace("\\", "/")
            if not want or "/".join(path.split("/")[:2]) == want:
                continue

            uploaded = (
                db.query(UploadTracking)
                  .filter(UploadTracking.saved_poster_id == poster.id,
                          UploadTracking.status == "uploaded")
                  .count()
            )
            (protected if uploaded else misfiled).append(
                (processed, poster, title, owner, path, want))

        if not misfiled and not protected:
            print("Nothing misfiled. Every derivative is under its own project.")
            return 0

        if protected:
            print(f"\n!! {len(protected)} image(s) were misfiled AND are already "
                  f"live on a marketplace.")
            print("   Left alone. Take these down by hand, then re-run:")
            for _pr, poster, title, owner, path, _want in protected:
                print(f"   · {owner.name}: {title.title} (image {poster.id}) -> {path}")

        print(f"\n{len(misfiled)} image(s) to undo:")
        for _pr, poster, title, owner, path, want in misfiled:
            print(f"  · {owner.name}: {title.title} (image {poster.id})")
            print(f"      wrong:  {path}")
            print(f"      wanted: {want}/…")

        print("\nStorage Box files to delete by hand (nothing here can reach them):")
        for _pr, _poster, _title, _owner, path, _want in misfiled:
            print(f"  S:/{path}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to make these changes.")
            return 0

        for processed, poster, title, _owner, _path, _want in misfiled:
            db.query(UploadTracking).filter(
                UploadTracking.processed_image_id == processed.id,
                UploadTracking.status.in_(("pending", "failed")),
            ).delete(synchronize_session=False)

            db.delete(processed)

            poster.pipeline_status = "greenlit"
            poster.process_attempts = 0
            poster.process_error = None
            poster.claimed_at = None
            poster.claimed_by = None
            P.recompute_title_status(db, title)

        db.commit()
        print(f"\nDone. {len(misfiled)} image(s) returned to the queue, and they "
              f"will now be processed by the correct stage.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
