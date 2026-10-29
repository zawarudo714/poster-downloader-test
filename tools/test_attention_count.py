"""
Behaviour test: the NEEDS ATTENTION badges count everything the Needs
Attention page lists, and "waiting to upload" counts only pictures with an
upload queued (v287).

Until v287 the Pipeline tab's badge left out step 2 giving up and a batch
OpenAI refused, the home page's badge also left out stalled claims, and the
home page's "waiting to upload" added every picture still in review. So a
badge read 0 while the page beneath it listed work only the owner can
unstick.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Run from the repo root:

    python tools/test_attention_count.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_attention_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
from datetime import date, datetime, timedelta
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.flush()
acc = M.UploadAccount(name="one", email="a@x", password_enc="x", project_id=proj.id,
                      artist_name="artist-one", daily_limit=100)
db.add(acc); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
n = [0]
old = datetime.utcnow() - timedelta(hours=3)


def poster(status, claimed_at=None):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=date(2026, 10, 20),
                       title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"{n[0]}.jpg",
                       source_url=f"u{n[0]}", pipeline_status=status, claimed_at=claimed_at)
    db.add(sp); db.flush()
    return t, sp


def painting(sp, review_status):
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="n.jpg",
                          filename="n.jpg", is_current=1, review_status=review_status)
    db.add(pi); db.flush()
    return pi


def upload(sp, pi, status, claimed_at=None):
    tr = M.UploadTracking(saved_poster_id=sp.id, processed_image_id=pi.id,
                          account_id=acc.id, project_id=proj.id, status=status,
                          remote_title="x", claimed_at=claimed_at)
    db.add(tr); db.flush()
    return tr


# ── A POSITIVE CONTROL: a healthy project reads 0 ─────────────────────────
_t, healthy = poster("processing", claimed_at=datetime.utcnow())
db.commit()
ok(P.attention_count(db, proj) == 0, "a fresh claim is not counted as stalled")

# ── ONE OF EACH THING THE PAGE LISTS ──────────────────────────────────────
poster("failed_processing")                                   # 1 painting failed
poster("processing", claimed_at=old)                          # 2 painting stalled
_t, s3 = poster("processed"); upload(s3, painting(s3, "approved"), "failed")      # 3
_t, s4 = poster("uploading"); upload(s4, painting(s4, "approved"), "uploading",
                                      claimed_at=old)          # 4 upload stalled
_t, s5 = poster("processed"); painting(s5, "step2_failed")    # 5 step 2 gave up
db.add(M.AIBatch(project_id=proj.id, stage=2, status="send_failed",
                 error="[refused] no credit", created_at=datetime.utcnow()))  # 6
db.commit()
got = P.attention_count(db, proj)
ok(got == 6, f"every list on the page is counted once ({got} of 6)")

# Both badges ask this one question.
from app.routes import admin as A, pipeline_admin as PA
for name, mod in (("home page", A), ("Pipeline tab", PA)):
    src = inspect.getsource(mod)
    ok('"attention": P.attention_count(db, ' in src, f"the {name} badge uses attention_count")

# ── WAITING TO UPLOAD ─────────────────────────────────────────────────────
_t, rev = poster("processed"); painting(rev, "pending")       # in review
_t, q1 = poster("processed"); upload(q1, painting(q1, "approved"), "pending")
db.commit()
wait = P.waiting_to_upload_count(db, proj)
ok(wait == 2, f"waiting to upload: the queued and the uploading picture, not review ({wait})")
f = P.funnel_counts(db, project_id=proj.id)
ok(f["processed"] + f["uploading"] > wait,
   "the old sum WOULD count review pictures (so the check can fail)")

# ── UNUSABLE HAS A BOX, AND A TITLE CAN READ IT ───────────────────────────
tu, su = poster("unusable"); db.commit()
f = P.funnel_counts(db, project_id=proj.id)
ok(f.get("unusable") == 1, f"the funnel counts unusable pictures ({f.get('unusable')})")
ok(P.recompute_title_status(db, tu) == "unusable",
   f"a title whose only picture is unusable reads unusable ({tu.pipeline_status})")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
print("\n  sabotage — the count without the step-2 line")
src = inspect.getsource(P.attention_count)
line = "            + step2_gave_up + batch_refused)"
ok(line in src, "the line is where the sabotage expects it")
ns = dict(P.__dict__)
exec(src.replace(line, ")"), ns)
ok(ns["attention_count"](db, proj) == 4,
   "without it the badge misses step 2 and the refused batch (so the check can fail)")

print("\nALL ATTENTION COUNT CHECKS PASSED")
