"""
Behaviour test: old print files are remade with today's sharpen and grain,
once (v295). Builds a throw-away SQLite database and keeps the "Storage Box"
in memory, so it never touches poster.db or S:. Run from the repo root:

    python tools/test_print_remake.py

Junique refused plain enlargements as "upscaled" (docs/FAILURES.md). v294
fixed new files; this checks the 318 made before it are found and remade by
the one builder (`_build_print_file`), and that a remade file is not remade
again until the settings change.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_remake_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date  # noqa: E402
from PIL import Image  # noqa: E402

from app.db import init_db, SessionLocal  # noqa: E402
from app import models as M, pipeline as P, storage_remote, review_cache  # noqa: E402
from app.print_remake import outdated, remake  # noqa: E402

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()  # noqa: E402,E702

# The Storage Box, in memory.
BOX: dict[str, bytes] = {}
WRITES: list[str] = []
storage_remote.read_bytes = lambda db, rel, *, project: BOX[rel]
def _write(db, rel, data, *, project):
    BOX[rel] = data; WRITES.append(rel); return rel
storage_remote.write_bytes = _write
review_cache.clear = lambda paths: 0

db = SessionLocal()
for _ in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
P.set_setting(db, "upscale_width_px", 600, project=proj)   # small, so the test is quick
P.set_setting(db, "signature_enabled", 0, project=proj)
db.commit()

u = M.User(username="w", password_hash="x", role="worker")
db.add(u); db.flush()
buf = io.BytesIO()
Image.new("RGBA", (300, 450), (40, 90, 160, 255)).save(buf, "PNG")

n = [0]
def row(status="approved", waiting=False):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=u.id, username="w",
                       project_folder="travel", original_save_date=date(2026, 11, 1), title_folder_path="x",
                       filename=f"{n[0]}.jpg", source_url="u")
    db.add(sp); db.flush()
    BOX[f"m/{n[0]}.png"] = buf.getvalue()
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id,
                          storage_path=f"p/{n[0]}.jpg" if status == "approved" else "",
                          filename=f"{n[0]}.jpg", master_path=f"m/{n[0]}.png",
                          background_color="#ffffff", review_status=status, is_current=1)
    db.add(pi); db.flush()
    if waiting:
        acct = db.query(M.UploadAccount).first()
        if acct is None:
            acct = M.UploadAccount(name="faa", email="t@example.com", password_enc="x", project_id=proj.id)
            db.add(acct); db.flush()
        db.add(M.UploadTracking(saved_poster_id=sp.id, processed_image_id=pi.id,
                                account_id=acct.id, project_id=proj.id, status="pending"))
    db.commit()
    return pi

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

live = row()
queued = row(waiting=True)
unreviewed = row(status="pending")

found = outdated(db)
ok([r.id for r in found] == [queued.id, live.id],
   "both approved files are outdated, the one waiting for upload first; the unreviewed one is not")

counts = remake(db, emit=lambda s: None)
ok(counts["remade"] == 2 and counts["failed"] == 0 and counts["left_after"] == 0,
   f"remake rebuilt 2, none left ({counts})")
with Image.open(io.BytesIO(BOX["p/1.jpg"])) as im:
    ok(im.format == "JPEG" and im.size == (600, 900), f"the print file was rewritten ({im.size})")
db.refresh(live)
ok(json.loads(live.print_treatment) == {"width": 600, "sharpen": 80, "grain": 4,
                                         "quality": int(P.DEFAULTS["upscale_jpeg_quality"])},
   f"the row records how it was made ({live.print_treatment})")

before = len(WRITES)
counts = remake(db, emit=lambda s: None)
ok(counts["remade"] == 0 and len(WRITES) == before, "a second run remakes nothing")

P.set_setting(db, "upscale_grain", 5, project=proj)
db.commit()
ok(len(outdated(db)) == 2, "changing the grain makes both outdated again")

print("ALL PASS")
