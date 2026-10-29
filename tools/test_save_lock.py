"""
Behaviour test: saving a picture does not hold the database while the
picture downloads (v287).

SQLite has ONE write lock. Both save doors (the search grid and the paste
box) used to write the title's first-save fields, and a swap's stand-down,
BEFORE downloading — and a download can take up to a minute. For that
minute the painting thread, the upload machine and every other save waited,
and after five seconds failed with "database is locked".

A second connection with no patience tries to write while each download is
running. Builds a throw-away SQLite database in a temp folder, so it never
touches poster.db, and never downloads anything. Run from the repo root:

    python tools/test_save_lock.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_save_lock_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import sqlite3
from PIL import Image
from sqlalchemy.orm import object_session
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import worker as W
import app.imagefetch as IF

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
dbfile = os.environ["DATABASE_URL"][len("sqlite:///"):]
W._validate_image_url = lambda url, db_, project: (True, "")
free = []


def lock_is_free() -> bool:
    other = sqlite3.connect(dbfile, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE"); other.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def picture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 2000), (90, 140, 200)).save(path, "JPEG")


def fake_fetch(url, target_path, **_):
    free.append(lock_is_free())
    picture(Path(target_path))
    return Path(target_path).stat().st_size, 1600, 2000


def fake_download(url, target_path):
    free.append(lock_is_free())
    picture(Path(target_path))
    return Path(target_path).stat().st_size


IF.fetch_as_jpeg = fake_fetch
W._download_to = fake_download
n = [0]
import itertools
seq = itertools.count(1)


def title():
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="pending",
                      project_id=proj.id, claimed_by_id=w.id, claimed_by_name=w.username)
    db.add(t); db.commit()
    return t


def grid(t, replace=0):
    r = W.api_search_save(t.id, url=f"https://example.com/g{next(seq)}.jpg",
                          replace=replace, source="", confirm_same_picture=1,
                          user=w, db=db)
    return json.loads(r.body), r.status_code


def paste(t, replace=0):
    w.locked_master_id = t.id; db.commit()
    r = W.save_image(url=f"https://example.com/p{next(seq)}.jpg",
                     confirm_duplicate=0, confirm_cross_title=0, replace=replace,
                     confirm_low_quality=1, confirm_same_picture=1, user=w, db=db)
    return json.loads(r.body), r.status_code


# ── THE GRID DOOR ─────────────────────────────────────────────────────────
t1 = title()
body, code = grid(t1)
ok(code == 200 and body.get("ok"), f"the grid saves a first picture ({code} {body.get('message', '')})")
ok(free == [True], f"the database is free while it downloads ({free})")
db.refresh(t1)
ok(t1.original_save_date is not None and t1.title_folder_path,
   "the first-save fields are still recorded")

free.clear()
body, code = grid(t1, replace=1)
ok(code == 200 and body.get("ok"), f"the grid swaps for a better pick ({code} {body.get('message', '')})")
ok(free == [True], f"the database is free while a SWAP downloads ({free})")
live = W.count_live_posters_for_master(db, t1.id)
ok(live == 1, f"after the swap the title still holds one picture ({live})")

# ── THE PASTE DOOR ────────────────────────────────────────────────────────
free.clear()
t2 = title()
body, code = paste(t2)
ok(code == 200 and body.get("ok"), f"the paste box saves a first picture ({code} {body.get('message', '')})")
ok(free == [True], f"the database is free while it downloads ({free})")
free.clear()
body, code = paste(t2, replace=1)
ok(code == 200 and body.get("ok"), f"the paste box swaps ({code} {body.get('message', '')})")
ok(free == [True], f"the database is free while a SWAP downloads ({free})")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
# The old order: the first-save fields written (flushed) before the
# download. The probe must see the lock held.
print("\n  sabotage — the first-save fields flushed before the download")
_real = W._ensure_first_save_metadata


def flushed(t, today):
    _real(t, today)
    object_session(t).flush()


W._ensure_first_save_metadata = flushed
free.clear()
try:
    t3 = title()
    grid(t3)
finally:
    W._ensure_first_save_metadata = _real
    db.rollback()
ok(free == [False], f"with the old order the lock IS held during the download ({free})")

print("\nALL SAVE LOCK CHECKS PASSED")
