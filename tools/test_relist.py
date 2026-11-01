"""
Behaviour test: RE-UPLOAD of listings deleted on FineArtAmerica, and USE MY
OWN PICTURE on a title waiting to go back up (v289).

The owner deletes the 417 live listings by hand, renames the shop, and
presses RE-UPLOAD. Each deleted listing goes back up on its SAME upload row
(one row per picture per account), with a plain title, at the front of the
queue. Checked here:

  * only rows the Listing check read as 410 are taken — never a 404;
  * an approved picture joins the queue now; one still on Approve Artwork
    waits as 'relist' and joins the queue when approved, with no second row;
  * the title goes out plain (times_listed back to 0);
  * relisted rows are claimed before everything else (with a sabotage);
  * USE MY OWN PICTURE moves the waiting row onto the new picture and names
    the pick for painting, so Step 1 OFF still paints it;
  * retire stands a waiting row down; the step-2 catch-up keeps it;
  * RE-UPLOAD refuses (409) when nothing reads 410;
  * the three new Diagnostics invariants, each with a positive control.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Never talks to a marketplace. Run from the repo root:

    python tools/test_relist.py
"""
import inspect
import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_relist_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import diagnostics as D
from app import gpt_worker as GW

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
ok(not P.step1_open(db, proj), "travel: step 1 is OFF, as on the live site")

w = M.User(username="humphrey", password_hash="x", role="worker")
boss = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, boss]); db.flush()
acct = M.UploadAccount(name="Illusination", target_site="fineartamerica", email="e",
                       password_enc=P.encrypt_secret("pw"), is_enabled=1, daily_limit=100,
                       artist_name="Illusination Travel Prints")
db.add(acct); db.flush()
db.add(M.AccountProject(account_id=acct.id, project_id=proj.id)); db.commit()

n = [0]


def listing(review="approved", *, listing_status="gone", saved=date(2026, 9, 20),
            status="uploaded", times_listed=1):
    """One title with one worker picture, painted, and an upload row."""
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id, title_folder_path=f"{n[0]}. Place {n[0]}",
                      original_save_date=saved)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=saved,
                       title_folder_path=t.title_folder_path, filename=f"{n[0]}.jpg",
                       source_url=f"u{n[0]}",
                       pipeline_status="uploaded" if status == "uploaded" else "processed",
                       times_listed=times_listed)
    db.add(sp); db.flush()
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id,
                          storage_path=f"s/{n[0]}.jpg", filename=f"{n[0]}.jpg",
                          is_current=1, review_status=review, stage=2)
    db.add(pi); db.flush()
    tr = M.UploadTracking(saved_poster_id=sp.id, processed_image_id=pi.id,
                          account_id=acct.id, project_id=proj.id,
                          target_site="fineartamerica", remote_title=t.title,
                          remote_id="r", status=status, letter_index=0,
                          uploaded_at=datetime(2026, 10, 1) if status == "uploaded" else None,
                          listing_status=listing_status if status == "uploaded" else None,
                          listing_http=410 if listing_status == "gone" else 200)
    db.add(tr); db.flush()
    return t, sp, pi, tr


def rows_of(sp):
    return db.query(M.UploadTracking).filter_by(saved_poster_id=sp.id).all()


# ── WHICH ROWS RE-UPLOAD MAY TAKE ─────────────────────────────────────────
t_a, sp_a, pi_a, tr_a = listing("approved")
t_w, sp_w, pi_w, tr_w = listing("pending")
_t, _sp, _pi, tr_404 = listing("approved", listing_status="no_page")
_t, _sp, _pi, tr_live = listing("approved", listing_status="live")
db.commit()
got = {r.id for r in P.relistable(db, acct.id).all()}
ok(got == {tr_a.id, tr_w.id},
   f"only the 410 rows are re-uploadable — never a 404, never a live one ({sorted(got)})")

# ── RE-UPLOAD ITSELF (the endpoint, so the log is checked too) ────────────
from app.routes import listing_admin as LA
res = LA.api_relist(payload={"account_id": acct.id}, admin=boss, db=db)
import json as _json
body = _json.loads(res.body)
ok(body["count"] == 2 and body["queued"] == 1 and body["waiting"] == 1,
   f"RE-UPLOAD: 1 queued now, 1 waits for approval ({body})")
for r in (tr_a, tr_w, tr_404, tr_live):
    db.refresh(r)
db.refresh(sp_a); db.refresh(sp_w)
ok(tr_a.status == "pending" and tr_a.relisted_at is not None,
   "an approved picture joins the upload queue, marked as relisted")
ok(tr_w.status == P.RELIST_WAITING, "a picture still on Approve Artwork waits as 'relist'")
ok(tr_a.uploaded_at is None and tr_a.remote_id is None and tr_a.listing_status is None,
   "the old listing's facts are cleared from the row (so it does not read live or gone)")
ok(tr_404.status == "uploaded" and tr_live.status == "uploaded",
   "the 404 row and the live row are untouched")
ok(sp_a.times_listed == 0 and sp_a.pipeline_status == "processed",
   "the picture is no longer counted as listed, so its title goes out plain")
ok(len(rows_of(sp_a)) == 1 and len(rows_of(sp_w)) == 1, "no second upload row was made")
logs = (db.query(M.ActivityLog).filter(M.ActivityLog.action == "listing_relisted").all())
ok(len(logs) == 2 and any("2026-10-01" in (l.details or "") for l in logs),
   "each re-upload is logged with when the old listing went live")
ok(P.render_remote_title(db, t_a, sp_a, 0, project=proj) == t_a.title,
   "the rendered title is the plain place name (no letter)")
sp_a.times_listed = 1
ok(P.render_remote_title(db, t_a, sp_a, 0, project=proj) != t_a.title,
   "control: with times_listed 1 the title WOULD carry a letter")
sp_a.times_listed = 0; db.commit()

try:
    LA.api_relist(payload={"account_id": acct.id}, admin=boss, db=db)
    ok(False, "a second RE-UPLOAD with nothing at 410 is refused")
except HTTPException as e:
    ok(e.status_code == 409, f"a second RE-UPLOAD with nothing at 410 is refused ({e.status_code})")

# ── APPROVAL RELEASES THE WAITING ROW ─────────────────────────────────────
pi_w.review_status = "approved"
P.ensure_upload_rows(db, poster=sp_w, title=t_w, processed=pi_w, project=proj)
db.commit(); db.refresh(tr_w)
ok(tr_w.status == "pending" and len(rows_of(sp_w)) == 1,
   "approving its picture puts the waiting row in the queue — still one row")

# ── RELISTED ROWS GO FIRST ────────────────────────────────────────────────
db.query(M.UploadTracking).filter(M.UploadTracking.id == tr_w.id).update(
    {M.UploadTracking.status: "skipped"})
_t, sp_old, _pi, tr_old = listing("approved", status="pending", saved=date(2026, 9, 1),
                                  times_listed=0)
db.commit()
claim = P.claim_upload_batch(db, node="n", account_id=acct.id, limit=1)
got = [i["tracking_id"] for i in claim.get("items") or []]
db.rollback()
ok(got == [tr_a.id], f"the relisted row is claimed before an older ordinary one ({got})")
claim = P.claim_upload_batch(db, node="n", account_id=acct.id, limit=2)
got = [i["tracking_id"] for i in claim.get("items") or []]
db.rollback()
ok(got == [tr_a.id, tr_old.id], f"control: the ordinary row is claimable too ({got})")

print("\n  sabotage — the claim without the relisted-first order")
csrc = inspect.getsource(P.claim_upload_batch)
rule = "              .order_by(case((UploadTracking.relisted_at.isnot(None), 0), else_=1),\n"
ok(rule in csrc, "the order line is where the sabotage expects it")
ns = dict(P.__dict__)
exec(csrc.replace(rule, "              .order_by(\n"), ns)
claim = ns["claim_upload_batch"](db, node="n", account_id=acct.id, limit=1)
got = [i["tracking_id"] for i in claim.get("items") or []]
db.rollback()
ok(got == [tr_old.id], f"without it the older ordinary row goes first (so the check can fail) ({got})")
db.query(M.UploadTracking).filter(M.UploadTracking.id == tr_old.id).delete()
db.commit()

# ── THE STEP-2 CATCH-UP KEEPS A RELISTED ROW ──────────────────────────────
t_s, sp_s, pi_s, tr_s = listing("approved")
pi_s.stage = 1
_t, sp_plain, pi_plain, tr_plain = listing("approved", status="pending", times_listed=0)
pi_plain.stage = 1
db.commit()
LA.api_relist(payload={"account_id": acct.id}, admin=boss, db=db)
db.refresh(tr_s)
ok(tr_s.status == "pending", "setup: the step-1 painting's row was re-uploaded")
P.send_painted_to_step2(db, proj, by="t"); db.commit()
ok([r.status for r in rows_of(sp_s)] == [P.RELIST_WAITING],
   "the catch-up keeps the relisted row, waiting for the step-2 picture")
ok(rows_of(sp_plain) == [], "control: an ordinary queued row is still deleted by the catch-up")

# ── RETIRE STANDS A WAITING ROW DOWN ──────────────────────────────────────
from app.routes import admin as A
t_r, sp_r, pi_r, tr_r = listing("pending")
db.commit()
LA.api_relist(payload={"account_id": acct.id}, admin=boss, db=db)
db.refresh(tr_r)
ok(tr_r.status == P.RELIST_WAITING, "setup: a row waits as 'relist'")
spared = A._withdraw_pictures(db, [sp_r], note="x", verdict="x", admin=boss,
                              now=datetime.utcnow())
db.commit(); db.refresh(tr_r)
ok(spared == [] and tr_r.status == "skipped",
   "retiring the picture stands its waiting row down")

# ── USE MY OWN PICTURE MOVES THE WAITING ROW ──────────────────────────────
t_o, sp_o, pi_o, tr_o = listing("pending")
db.commit()
LA.api_relist(payload={"account_id": acct.id}, admin=boss, db=db)
db.refresh(tr_o); db.refresh(sp_o)
ok(tr_o.status == P.RELIST_WAITING and sp_o.times_listed == 0,
   "setup: the listing waits to go back up, and its picture may leave")
ok(A._picture_cannot_leave(sp_o) is None, "a re-uploaded picture is no longer held as live")

A.current_project = lambda request, admin, db: proj
import app.place_check as PC
PC.launch_check = lambda poster_id: None
buf = io.BytesIO(); Image.new("RGB", (1200, 1600), (30, 120, 200)).save(buf, "JPEG")
buf.seek(0)
res = A.admin_pick_title(t_o.id, request=None, url="", file=UploadFile(buf, filename="mine.jpg"),
                         reason="better", confirm_same_picture=0, confirm_flagged=0,
                         admin=boss, db=db)
body = _json.loads(res.body)
ok(body.get("ok") is True, f"USE MY OWN PICTURE succeeds ({body})")
new = db.query(M.SavedPoster).filter_by(id=body["poster_id"]).one()
db.refresh(tr_o); db.refresh(sp_o)
ok(sp_o.deleted_at is not None, "the old picture is withdrawn")
ok(tr_o.saved_poster_id == new.id and tr_o.status == P.RELIST_WAITING
   and tr_o.relisted_at is not None,
   "the waiting row MOVED onto the new picture, keeping its place at the front")
ok(tr_o.letter_index == 0, "and its letter is the new picture's place on the title")
ok(len(rows_of(new)) == 1 and rows_of(sp_o) == [], "still exactly one row for the title")
ok(new.paint_requested_at is not None, "the pick is named for painting")
q = {sp.id for sp, _t in GW.paint_queue(db, proj).all()}
ok(new.id in q, "so it IS in the paint queue while Step 1 is off")
pi_n = M.ProcessedImage(saved_poster_id=new.id, project_id=proj.id, storage_path="s/n.jpg",
                        filename="n.jpg", is_current=1, review_status="approved", stage=2)
db.add(pi_n); db.flush()
P.ensure_upload_rows(db, poster=new, title=t_o, processed=pi_n, project=proj)
db.commit(); db.refresh(tr_o)
ok(tr_o.status == "pending" and tr_o.processed_image_id == pi_n.id and len(rows_of(new)) == 1,
   "approving the new painting puts the moved row in the queue — still one row")

# ── DIAGNOSTICS ───────────────────────────────────────────────────────────
scope = D.Scope(db, proj.id)
c1 = D.check_one_listing_per_title_slot(db, scope)
c2 = D.check_relisted_rows_are_sound(db, scope)
c3 = D.check_own_picks_painted_while_step1_off(db, scope)
ok(c1.count == 0 and c2.count == 0 and c3.count == 0,
   f"control: all three invariants are clear on what the doors wrote ({c1.count}, {c2.count}, {c3.count})")

# The replaced picture's row left live beside its successor's.
dup = M.UploadTracking(saved_poster_id=sp_o.id, processed_image_id=pi_o.id,
                       account_id=acct.id, project_id=proj.id, target_site="fineartamerica",
                       remote_title="x", status="pending", letter_index=0)
db.add(dup); db.commit()
c1 = D.check_one_listing_per_title_slot(db, scope)
ok(c1.count == 1 and c1.severity == "error", "a second row on one title slot goes red")
db.delete(dup); db.commit()

tr_o.status = P.RELIST_WAITING; tr_o.relisted_at = None; db.commit()
ok(D.check_relisted_rows_are_sound(db, scope).count == 1, "a 'relist' row never sent back goes red")
tr_o.relisted_at = datetime.utcnow(); tr_o.status = "uploaded"; tr_o.uploaded_at = None
db.commit()
ok(D.check_relisted_rows_are_sound(db, scope).count == 1,
   "a relisted row reading live with no new upload time goes red")
tr_o.uploaded_at = tr_o.relisted_at + timedelta(minutes=5); db.commit()
ok(D.check_relisted_rows_are_sound(db, scope).count == 0,
   "control: one that really went up again is clear")
tr_o.status = P.RELIST_WAITING
sp_o.deleted_at = sp_o.deleted_at or datetime.utcnow()
tr_o.saved_poster_id = sp_o.id; db.commit()
ok(D.check_relisted_rows_are_sound(db, scope).count == 1,
   "a 'relist' row left on a withdrawn picture goes red")
tr_o.saved_poster_id = new.id; tr_o.status = "pending"; db.commit()

new.pipeline_status = "greenlit"; new.paint_requested_at = None; db.commit()
c3 = D.check_own_picks_painted_while_step1_off(db, scope)
ok(c3.count == 1 and c3.severity == "warn", "an unnamed own pick with Step 1 off is reported")
P.set_setting(db, "step1_enabled", True, project=proj, by="t"); db.commit()
c3 = D.check_own_picks_painted_while_step1_off(db, scope)
ok(c3.skipped and c3.count == 0, "with Step 1 on the check says it does not apply")
P.set_setting(db, "step1_enabled", False, project=proj, by="t"); db.commit()

print("\nALL RELIST CHECKS PASSED")
