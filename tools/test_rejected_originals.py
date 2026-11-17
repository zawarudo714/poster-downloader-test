"""
Behaviour test: THE OWNER'S TURNED-DOWN PICTURES ARE KEPT, NOT DELETED (v302).

The owner (2026-11-17) agreed to keep flagged originals so an AI picture
check can be re-tested on his real decisions. Checked here:

  * a flagged worker picture is moved to REJECTED_DIR, with an index line
    holding its title, source address and the flag's words;
  * a picture with no flag is still deleted; so is the admin's own pick;
  * the owner's own removal (_withdraw_pictures, the real path) keeps a
    worker picture even with no flag;
  * if keeping fails, the file is still removed (the old delete);
  * the folder sits outside the workspace (workspace folders are read as
    workers);
  * no removal site in worker.py or admin.py deletes a picture file directly;
  * sabotage: a helper that never keeps goes red.

Run from the repo root:  python tools/test_rejected_originals.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_rejected_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
os.environ.pop("REJECTED_DIR", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, config
from app import rejected_originals as RO
from app.utils import saved_poster_path
from app.routes import admin as A

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
admin = M.User(username="owner", password_hash="x", role="admin")
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add_all([admin, w]); db.commit()
n = [0]


def picture(flag=None, **extra):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete", project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder=proj.slug, original_save_date=date(2026, 11, 1),
                       title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"Place {n[0]} 1.jpg",
                       source_url=f"https://example.test/{n[0]}.jpg", **extra)
    db.add(sp); db.flush()
    if flag:
        db.add(M.Revision(saved_poster_id=sp.id, comment=flag, flagged_by=admin.username, status="open"))
        db.flush()
    p = saved_poster_path(sp)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8 picture " + str(n[0]).encode())
    return sp, p


def kept():
    return sorted(x.name for x in config.REJECTED_DIR.glob("*.jpg")) if config.REJECTED_DIR.is_dir() else []


def index():
    f = config.REJECTED_DIR / "index.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


# 0. Where the folder is
ws = config.WORKSPACE_DIR.resolve()
ok(ws not in config.REJECTED_DIR.resolve().parents and config.REJECTED_DIR.resolve() != ws,
   "the kept-pictures folder is outside the workspace")

# 1. Positive control first: the plain delete still deletes
sp, p = picture()
RO.remove_picture_file(db, sp, p, reason="worker deleted it")
ok(not p.exists() and kept() == [], "a picture with no flag is deleted, nothing kept")

# 2. A flagged worker picture is kept, with its story
sp, p = picture(flag="generic")
RO.remove_picture_file(db, sp, p, reason="worker replaced it")
ok(not p.exists() and len(kept()) == 1, "a flagged picture leaves the worker's folder and is kept")
line = index()[-1]
ok(line["poster_id"] == sp.id and line["title"] == f"Place {n[0]}" and line["flags"][0]["comment"] == "generic"
   and line["source_url"].endswith(f"/{n[0]}.jpg"), "the index line holds the title, address and the flag's words")
ok((config.REJECTED_DIR / line["file"]).read_bytes().endswith(str(n[0]).encode()), "the kept file is the same picture")

# 3. The admin's own pick is not kept, flag or not
sp, p = picture(flag="angle", added_by=admin.id)
RO.remove_picture_file(db, sp, p, reason="x", by_owner=True)
ok(not p.exists() and len(kept()) == 1, "the admin's own picture is deleted, not kept")

# 4. The owner's own removal, through the real path, keeps an unflagged worker picture
sp, p = picture()
db.commit()
A._withdraw_pictures(db, [sp], note="Deleted by admin", verdict="deleted", admin=admin,
                     now=datetime(2026, 11, 17), pay=False)
ok(not p.exists() and len(kept()) == 2 and index()[-1]["by_owner"], "an admin DELETE keeps the worker's picture")

# 5. Keeping fails: the file still goes
real_move = RO.shutil.move
RO.shutil.move = lambda a, b: (_ for _ in ()).throw(OSError("disk full"))
sp, p = picture(flag="generic")
RO.remove_picture_file(db, sp, p, reason="x")
ok(not p.exists() and len(kept()) == 2, "when keeping fails the picture is still removed")
RO.shutil.move = real_move

# 6. No removal site deletes a picture file directly any more
for f in ("app/routes/worker.py", "app/routes/admin.py"):
    src = (ROOT / f).read_text(encoding="utf-8")
    direct = re.findall(r"\b(old_fs|fs_path|fs|old_path)\.unlink\(", src)
    ok(not direct, f"{f}: every picture removal goes through remove_picture_file ({direct})")
ok("for old_sp, old_path in stood_down_paths" in (ROOT / "app/routes/worker.py").read_text(encoding="utf-8"),
   "both swap paths hand the poster, not only the path")

# 7. Sabotage: a helper that never keeps must be seen
real = RO.should_keep
RO.should_keep = lambda db, sp, by_owner=False: False
sp, p = picture(flag="generic")
RO.remove_picture_file(db, sp, p, reason="x")
ok(len(kept()) == 2, "sabotage seen: with keeping switched off the flagged picture is lost (count stays 2)")
RO.should_keep = real
print("ALL PASS")
