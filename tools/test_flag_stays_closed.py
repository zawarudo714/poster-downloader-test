"""
Behaviour test: a worker cannot send a closed flag back to the admin (v273).
Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Run from the repo root:

    python tools/test_flag_stays_closed.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_flag_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime, date
from fastapi import HTTPException
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, diagnostics as D
from app.routes import worker as W

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.flush()
t = M.MasterTitle(title="Place 1", external_id=1, status="complete", project_id=proj.id)
db.add(t); db.flush()
sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username, project_folder="travel",
                   original_save_date=date(2026, 10, 20), title_folder_path="x", filename="1.jpg",
                   source_url="u")
db.add(sp); db.flush()
rev = M.Revision(saved_poster_id=sp.id, flagged_by="admin", status="open")
db.add(rev); db.commit()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
send = lambda: W.resolve_revision(rev.id, worker_note="", user=w, db=db)
check = lambda: D.check_closed_flags_stay_closed(db, D.Scope(db, None))

send(); db.refresh(rev)
ok(rev.status == "awaiting_approval", "an open flag is sent to the admin")
r = json.loads(send().body); db.refresh(rev)
ok(r.get("already") and rev.status == "awaiting_approval", "sending it twice changes nothing")
rev.status = "resolved"; rev.resolved_at = datetime.utcnow(); db.commit()
ok(check().severity == "ok", f"Diagnostics quiet: {check().title}")
try:
    send()
    ok(False, "a closed flag cannot be sent back")
except HTTPException as e:
    db.rollback(); db.refresh(rev)
    ok(e.status_code == 409 and rev.status == "resolved", "a closed flag cannot be sent back")
rev.status = "awaiting_approval"; db.commit()
ok(check().severity == "warn", f"Diagnostics warns on a closed flag that came back: {check().title}")
print("ALL FLAG CHECKS PASSED")
