"""
Behaviour test: deleting the last picture on a title returns it to the
pool WHOLE (v278). The half reset kept the first worker's start time, save
date, folder and name, so the next worker could not return it unworked
and his save was filed under the old date. Builds a throw-away SQLite
database in a temp folder, so it never touches poster.db. Run from the
repo root:

    python tools/test_delete_last_picture.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_delete_last_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, date
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, diagnostics as D
from app.routes import worker as W

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
a = M.User(username="anne", password_hash="x", role="worker")
db.add(a); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
check = lambda: D.check_pending_titles_are_clean(db, D.Scope(db, None))


def worked(n, status):
    t = M.MasterTitle(title=f"Place {n}", external_id=n, status=status, project_id=proj.id,
                      claimed_by_id=a.id, claimed_by_name="anne", claimed_at=datetime(2026, 9, 1),
                      started_at=datetime(2026, 9, 1), original_save_date=date(2026, 9, 1),
                      title_folder_path=f"{n}. Place {n}")
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=a.id, username="anne", project_folder="travel",
                       original_save_date=date(2026, 9, 1), title_folder_path=t.title_folder_path,
                       filename=f"{n}.jpg", source_url=f"u{n}")
    db.add(sp); db.commit()
    a.locked_master_id = t.id; db.commit()
    return t, sp


ok(check().severity == "ok", f"Diagnostics quiet on an empty site: {check().title}")
for n, status in enumerate(("in_progress", "complete_pending"), 1):
    t, sp = worked(n, status)
    W.delete_poster(sp.id, note="wrong place", reason_source="", user=a, db=db)
    db.refresh(t)
    ok(t.status == "pending", f"{status}: the title is back in the pool")
    left = {k: getattr(t, k) for k in ("claimed_by_id", "claimed_by_name", "claimed_at",
                                         "started_at", "original_save_date", "title_folder_path")}
    ok(not any(left.values()), f"{status}: nothing of the first worker is left on it ({left})")
ok(check().severity == "ok", f"Diagnostics quiet: {check().title}")
t.started_at = datetime(2026, 9, 1); t.original_save_date = date(2026, 9, 1); db.commit()
ok(check().severity == "warn", f"Diagnostics warns on a half-reset title: {check().title}")
print("ALL DELETE-LAST-PICTURE CHECKS PASSED")
