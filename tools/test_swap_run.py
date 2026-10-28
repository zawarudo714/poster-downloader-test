"""
Behaviour test: the SWAP button says what one run really swaps (v274).
One run takes one account and at most P.SWAP_JOB_CAP listings; the button,
the toast and the job's list must all agree, and another project's swap
job must not block this one. Builds a throw-away SQLite database in a temp
folder, so it never touches poster.db. Never talks to a marketplace.
Run from the repo root:

    python tools/test_swap_run.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_swap_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import date
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import pipeline_admin as PA, pipeline_api as PI

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
other = M.Project(slug="elsewhere", name="Elsewhere")
db.add(other); db.flush()
admin = M.User(username="boss", password_hash="x", role="admin")
db.add(admin); db.flush()
acc1 = M.UploadAccount(name="one", email="a@x", password_enc="x", project_id=proj.id,
                       artist_name="artist-one")
acc2 = M.UploadAccount(name="two", email="b@x", password_enc="x", project_id=proj.id,
                       artist_name="artist-two")
db.add_all([acc1, acc2]); db.flush()

P.SWAP_JOB_CAP = 3          # small, so "more than one run" is cheap to build


def live(n, acc):
    for i in range(n):
        t = M.MasterTitle(title=f"Place {acc.id}-{i}", external_id=acc.id * 100 + i,
                          status="complete", project_id=proj.id)
        db.add(t); db.flush()
        sp = M.SavedPoster(master_title_id=t.id, user_id=admin.id, username="boss",
                           project_folder="travel", original_save_date=date(2026, 10, 1),
                           title_folder_path="x", filename=f"{t.id}.jpg", source_url=f"u{t.id}")
        db.add(sp); db.flush()
        old = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="o.jpg",
                               filename="o.jpg", is_current=0, review_status="approved")
        new = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="n.jpg",
                               filename="n.jpg", is_current=1, review_status="approved")
        db.add_all([old, new]); db.flush()
        db.add(M.UploadTracking(saved_poster_id=sp.id, processed_image_id=old.id,
                                account_id=acc.id, project_id=proj.id, status="uploaded",
                                remote_title=t.title, remote_id=f"r{t.id}"))
    db.commit()


live(5, acc1)
live(2, acc2)
PA._project = lambda request, admin, db, explicit=None: proj

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

counts = PA._ai_counts(db, proj)
ok(counts["swap_waiting"] == 7, f"7 listings wait (got {counts['swap_waiting']})")
ok(counts["swap_this_run"] == 3, f"the button says 3, what one run takes (got {counts['swap_this_run']})")

P.create_job(db, kind="listing_swap", project_id=other.id, payload={}, requested_by="x")
db.commit()
r = json.loads(PA.api_ai_swap(request=None, admin=admin, db=db).body)
ok(not r.get("already"), "another project's swap job does not block this one")
ok(r["this_run"] == 3 and r["waiting"] == 7, f"the toast says 3 of 7 (got {r})")

job = db.query(M.PipelineJob).filter_by(id=r["job_id"]).one()
out = PI._resolve_job_payload(db, job, json.loads(job.payload_json or "{}"), proj)
ids = {it["tracking_id"] for it in out.get("items", [])}
ok(len(ids) == 3, f"the job's list is the same 3 (got {len(ids)}: {out.get('error')})")
accs = {db.get(M.UploadTracking, i).account_id for i in ids}
ok(accs == {acc1.id}, "all from one account")

r2 = json.loads(PA.api_ai_swap(request=None, admin=admin, db=db).body)
ok(r2.get("already") and r2["job_id"] == job.id, "pressing again while it waits does nothing")
print("ALL SWAP RUN CHECKS PASSED")
