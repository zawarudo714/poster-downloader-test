"""
Behaviour test: painting follows the K mark (v265), and the pay-ahead money
fix. Builds a throw-away SQLite database in a temp folder, so it never
touches poster.db. Run from the repo root:

    python tools/test_painting_follows_k.py

Needs the app's own requirements (SQLAlchemy). Every line prints PASS or
FAIL; the first FAIL stops it with exit code 1.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_keep_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime, timedelta, date
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, payments as PAY

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
print("project", proj.slug, "images_per_title", proj.images_per_title)

w = M.User(username="humphrey", password_hash="x", role="worker"); a = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, a]); db.flush()
NOW = datetime.utcnow()
n = [0]
def title(status="complete"):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status=status, project_id=proj.id)
    db.add(t); db.flush(); return t
def pic(t, *, kept_min_ago=None, admin=False, day=date(2026,10,20), status=None):
    sp = M.SavedPoster(master_title_id=t.id, user_id=(a.id if admin else w.id), username=w.username,
                       project_folder="travel", original_save_date=day, title_folder_path="x",
                       filename=f"{t.id}.jpg", source_url="u", added_by=("admin" if admin else None),
                       reviewed_at=(NOW - timedelta(minutes=kept_min_ago) if kept_min_ago is not None else None),
                       pipeline_status=status)
    db.add(sp); db.flush(); return sp
def pay(ids, advance=1, settled=None):
    r = M.PaymentRun(worker_id=w.id, worker_username="humphrey", period_start=date(2026,10,1), period_end=date(2026,10,25),
                     poster_count=len(ids), rate_kes="7", amount_kes="0", poster_ids_json=json.dumps(ids), is_advance=advance,
                     advance_settled_json=json.dumps(settled) if settled else None, created_by="admin")
    db.add(r); db.flush(); return r

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

t1 = title(); unpaid_kept = pic(t1, kept_min_ago=60)
t2 = title(); paid_kept_old = pic(t2, kept_min_ago=60)
t3 = title(); paid_kept_new = pic(t3, kept_min_ago=1)
t4 = title(); paid_unkept = pic(t4)
t5 = title(); paid_kept_flagged = pic(t5, kept_min_ago=60)
db.add(M.Revision(saved_poster_id=paid_kept_flagged.id, flagged_by="admin", status="open"))
t6 = title(); own = pic(t6, admin=True, kept_min_ago=0)
t7 = title("in_progress"); paid_kept_unfinished = pic(t7, kept_min_ago=60)
pay([paid_kept_old.id, paid_kept_new.id, paid_unkept.id, paid_kept_flagged.id, paid_kept_unfinished.id])
db.commit()

ready = set(P.pictures_ready_to_paint(db))
ok(paid_kept_old.id in ready, "paid + kept an hour ago -> ready")
ok(unpaid_kept.id not in ready, "kept but NOT paid -> waits for Monday")
ok(paid_kept_new.id not in ready, "paid + kept 1 minute ago -> still inside the 5-minute wait")
ok(paid_unkept.id not in ready, "paid, no K -> waits for your look")
ok(paid_kept_flagged.id not in ready, "paid + kept but flagged -> waits")
ok(own.id in ready, "own pick -> no pay, no wait")
ok(paid_kept_unfinished.id not in ready, "title not finished -> waits")
later = set(P.pictures_ready_to_paint(db, as_of=NOW + timedelta(minutes=6)))
ok(paid_kept_new.id in later, "the 1-minute K is ready once 5 minutes have passed")

res = P.release_kept_pictures(db); db.commit()
db.refresh(paid_kept_old); db.refresh(own); db.refresh(unpaid_kept)
ok(paid_kept_old.pipeline_status == "greenlit" and own.pipeline_status == "greenlit", f"release sends them: {res}")
ok(unpaid_kept.pipeline_status is None, "unpaid stays out")
ok(db.get(M.MasterTitle, t2.id).greenlit_source == "kept", "title stamped greenlit_source=kept")
ok(not P.pictures_ready_to_paint(db) or set(P.pictures_ready_to_paint(db)) <= set(), "nothing left ready after release")

# manual mode turns it off (saved per project, as the Greenlight tab does)
P.set_setting(db, "greenlight_mode", "manual", project=proj, by="t"); db.commit()
ok(P.pictures_ready_to_paint(db, as_of=NOW + timedelta(minutes=6)) == [], "manual mode -> nothing released")
P.set_setting(db, "greenlight_mode", "both", project=proj, by="t"); db.commit()

# un-K / flag pull back
ok(P.pull_back_pictures(db, [paid_kept_old]) == 1 and paid_kept_old.pipeline_status is None, "pull back a QUEUED picture")
db.commit()
sp = paid_kept_old; sp.pipeline_status = "processing"; db.commit()
ok(P.pull_back_pictures(db, [sp]) == 0 and sp.pipeline_status == "processing", "a picture being painted is left alone")
ok(not P.picture_can_change(db, sp), "being painted -> worker cannot change it")

# painted, not approved -> can change; take out supersedes its painting
sp.pipeline_status = "processed"
pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="", filename="p.jpg", is_current=1, review_status="pending", attempt=1)
db.add(pi); db.commit()
ok(P.picture_can_change(db, sp), "painted but not approved -> can change")
ok(P.take_out_of_pipeline(db, sp), "take out painted-unapproved")
db.commit(); db.refresh(sp); db.refresh(pi)
ok(sp.pipeline_status is None and pi.is_current == 0 and pi.review_status == "superseded", "painting left Approve Artwork, picture back to not-greenlit")
sp.pipeline_status = "processed"; pi.review_status = "approved"; pi.is_current = 1; db.commit()
ok(not P.picture_can_change(db, sp), "approved painting -> cannot change")
sp.pipeline_status = "uploaded"; db.commit()
ok(not P.picture_can_change(db, sp), "uploaded -> cannot change")
sp.pipeline_status = None; sp.times_listed = 1; db.commit()
ok(P.picture_can_change(db, sp), "recalled (state reset, listed before) -> can change")

# race: state changed under us -> take_out refuses
t9 = title(); racer = pic(t9, status="greenlit"); db.commit()
db.query(M.SavedPoster).filter_by(id=racer.id).update({M.SavedPoster.pipeline_status: "processing"}, synchronize_session=False)
ok(P.take_out_of_pipeline(db, racer) is False, "painter claimed it this instant -> take_out refuses")
db.rollback()

# money fix
t10 = title(); old = pic(t10, day=date(2026,10,10)); db.flush()
run = pay([old.id]); old.deleted_at = NOW; db.commit()
redo = pic(t10, day=date(2026,10,24)); db.commit()
ok(PAY.unpayable_reasons(db, w.id, [redo.id]).get(redo.id) == "title_paid", "before deduction: redo is 'title already paid'")
sf = PAY.advance_shortfall(db, w.id)
ok(sf["count"] == 0, f"redo by the same worker -> nothing to take back ({sf['count']})")
redo.deleted_at = NOW; db.commit()
sf = PAY.advance_shortfall(db, w.id)
ok(sf["count"] == 1 and sf["items"][0]["poster_id"] == old.id, "no redo -> 7 KES listed to take back")
pay([], settled={"deducted": [old.id], "kes": "7"}); db.commit()
redo2 = pic(t10, day=date(2026,10,30)); db.commit()
why = PAY.unpayable_reasons(db, w.id, [redo2.id]).get(redo2.id)
ok(why is None, f"after the 7 KES was taken back, a later redo IS paid (reason={why})")
t11 = title(); old2 = pic(t11); pay([old2.id]); old2.deleted_at = NOW; db.commit()
pay([], settled={"waived": [old2.id], "kes": "7"}); db.commit()
redo3 = pic(t11); db.commit()
ok(PAY.unpayable_reasons(db, w.id, [redo3.id]).get(redo3.id) == "title_paid", "waived (he kept the money) -> redo is not paid again")

# replacement on a paid title, kept -> ready
t12 = title(); orig = pic(t12, kept_min_ago=60); pay([orig.id]); orig.deleted_at = NOW
rep = pic(t12, kept_min_ago=120); db.commit()
ok(rep.id in set(P.pictures_ready_to_paint(db)), "kept replacement on a paid title -> ready")

# diagnostics
from app import diagnostics as D
sc = D.Scope(db)
r1 = D.check_kept_pictures_reach_painting(db, sc); r2 = D.check_nothing_queued_without_a_look(db, sc); r3 = D.check_titles_are_paid_once(db, sc)
print("diag:", r1.severity, r1.count, "|", r2.severity, r2.count, "|", r3.severity, r3.count)
ok(r1.count >= 1, "watcher sees kept+paid pictures not released for an hour (loop not running in this test)")
ok(r2.count >= 1, "watcher sees a queued picture with no K (racer/greenlit seeds)")
print("ALL TESTS RAN")
