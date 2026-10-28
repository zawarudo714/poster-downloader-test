"""
Behaviour test: the search grid cannot save onto a closed title (v277).
Skip and complete keep the worker's claim, so the claim check alone let a
stale grid tab save a live, payable picture onto a skipped title. Builds a
throw-away SQLite database in a temp folder, so it never touches poster.db,
and never downloads anything (the refusal comes first). Run from the repo
root:

    python tools/test_search_save_closed.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_search_closed_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import worker as W

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
fetched = []
import app.imagefetch as IF
IF.fetch_as_jpeg = lambda *a, **k: fetched.append(1) or (_ for _ in ()).throw(RuntimeError("no fetch in tests"))

for n, status in enumerate(("skipped", "complete", "unusable"), 1):
    t = M.MasterTitle(title=f"Place {n}", external_id=n, status=status, project_id=proj.id,
                      claimed_by_id=w.id, claimed_by_name=w.username)
    db.add(t); db.commit()
    try:
        W.api_search_save(t.id, url="https://example.com/a.jpg", replace=0, source="",
                          confirm_same_picture=0, user=w, db=db)
        ok(False, f"a {status} title refuses a save from the grid")
    except HTTPException as e:
        db.rollback()
        ok(e.status_code in (409, 410), f"a {status} title refuses a save from the grid ({e.detail})")
    ok(db.query(M.SavedPoster).filter_by(master_title_id=t.id).count() == 0,
       f"nothing was saved on the {status} title")
ok(not fetched, "nothing was downloaded")
print("ALL SEARCH SAVE CHECKS PASSED")
