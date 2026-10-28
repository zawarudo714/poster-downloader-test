"""
Behaviour test: step 2 (the flatten), the Step 1 switch, OpenAI batches and
the live-listing swap list (v266). Builds a throw-away SQLite database and a
throw-away storage folder, and replaces the four OpenAI calls with fakes that
record what was sent — so it never touches poster.db and never spends money.

    python tools/test_step2_batch.py

Needs the app's own requirements (SQLAlchemy, Pillow, requests). Every line
prints PASS or FAIL; the first FAIL stops it with exit code 1.

WHAT THE FAKES SIMPLIFY (rule 5d — list them before trusting a pass):
  · OpenAI's output file is written by the test, in the documented shape
    ({"custom_id", "response": {"status_code", "body"}}); a real file may
    carry more fields, which the collector ignores.
  · No network: the upload/create/get/download/cancel calls are replaced.
  · Storage is the local fallback folder, not the Storage Box over SFTP.
"""
import base64
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_step2_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import gpt_images as G, gpt_worker as GW, ai_batches as AB
from app import diagnostics as D

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))


def png(size=(1024, 1536), rgba=True) -> bytes:
    im = Image.new("RGBA" if rgba else "RGB", size, (40, 90, 160, 120) if rgba else (40, 90, 160))
    out = io.BytesIO(); im.save(out, "PNG"); return out.getvalue()


init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
P.set_setting(db, "storage_local_root", os.path.join(_TMP, "box"), project=proj, by="t")
P.set_setting(db, "openai_api_key", P.encrypt_secret("sk-test"), project=proj, by="t")
P.set_setting(db, "openai_use_style_image", False, project=proj, by="t")
db.commit()

# ── The registry put step 2 on, batch on, and step 1 OFF for the first deploy.
ok(P.step2_active(db, proj), "travel: step 2 is on (registry)")
ok(P.batch_enabled(db, proj), "travel: batches are on (registry)")
ok(not P.step1_open(db, proj), "travel: step 1 starts OFF (the catch-up start)")
ok("Maintain somewhat the same composition" in P.get_setting(db, "step2_prompt", project=proj),
   "travel: the owner's step 2 prompt is in place")

w = M.User(username="humphrey", password_hash="x", role="worker")
a = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, a]); db.flush()
acct = M.UploadAccount(name="Illusination", target_site="fineartamerica", email="e",
                       password_enc=P.encrypt_secret("pw"), is_enabled=1, daily_limit=100,
                       artist_name="Illusination Travel Posterz")
db.add(acct); db.flush()
db.add(M.AccountProject(account_id=acct.id, project_id=proj.id)); db.flush()

n = [0]
def picture(status=None, *, photo=True):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=date(2026, 10, 20),
                       title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"{n[0]}.jpg",
                       source_url="u", pipeline_status=status)
    db.add(sp); db.flush()
    if photo:
        from app.utils import saved_poster_path
        f = saved_poster_path(sp); f.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 1200), (200, 120, 60)).save(f, "JPEG")
    return t, sp

def painting(t, sp, status, *, attempt=1):
    rel, name = P.storage_path_for(db, t, sp, project=proj, attempt=attempt)
    master = rel.rsplit(".", 1)[0] + "_master.png"
    from app.storage_remote import write_bytes
    write_bytes(db, master, png(), project=proj)
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="",
                          filename=name, is_current=1, review_status=status,
                          attempt=attempt, master_path=master, preview_path="p.jpg")
    db.add(pi); db.flush(); return pi

# ── STEP 1 SWITCH ──────────────────────────────────────────────────────────
t_g, waiting = picture("greenlit")
t_r, retried = picture("greenlit"); retried.paint_requested_at = datetime.utcnow()
db.commit()
q = {sp.id for sp, _t in GW.paint_queue(db, proj).all()}
ok(retried.id in q and waiting.id not in q, "step 1 OFF: only the picture named by hand is painted")
P.set_setting(db, "step1_enabled", True, project=proj, by="t"); db.commit()
q = {sp.id for sp, _t in GW.paint_queue(db, proj).all()}
ok(retried.id in q and waiting.id in q, "step 1 ON: everything waiting is painted")
P.set_setting(db, "step1_enabled", False, project=proj, by="t"); db.commit()

# ── CATCH-UP: everything painted before step 2 goes on to it ───────────────
t1, p_pending = picture("processed"); pi_pending = painting(t1, p_pending, "pending")
t2, p_held = picture("processed");    pi_held = painting(t2, p_held, "held")
t3, p_appr = picture("processed");    pi_appr = painting(t3, p_appr, "approved")
q_row = M.UploadTracking(saved_poster_id=p_appr.id, processed_image_id=pi_appr.id,
                         account_id=acct.id, project_id=proj.id, target_site="fineartamerica",
                         remote_title="Place 3", status="pending")
t4, p_live = picture("uploaded");     pi_live = painting(t4, p_live, "approved")
pi_live.storage_path = "old/live.jpg"
live_row = M.UploadTracking(saved_poster_id=p_live.id, processed_image_id=pi_live.id,
                            account_id=acct.id, project_id=proj.id, target_site="fineartamerica",
                            remote_title="Place 4", status="uploaded")
db.add_all([q_row, live_row]); db.commit()
q_row_id = q_row.id

# Before the catch-up, a queued upload of an approved painting is claimable.
ok(P.painted_before_step2(db, proj).count() == 4, "catch-up finds pending, held, approved and live")
sent = P.send_painted_to_step2(db, proj, by="admin"); db.commit()
ok(sent == 4, f"catch-up marks all four for step 2 ({sent})")
for pi in (pi_pending, pi_held, pi_appr, pi_live):
    db.refresh(pi)
ok(all(pi.review_status == "step2" for pi in (pi_pending, pi_held, pi_appr, pi_live)),
   "every one now waits for step 2")
ok(db.query(M.UploadTracking).filter_by(id=q_row_id).first() is None,
   "the approved painting's QUEUED upload row is removed (it would have sent the painting)")
ok(db.query(M.UploadTracking).filter_by(id=live_row.id).first().status == "uploaded",
   "the live listing's row is untouched")
ok(not P.picture_can_change(db, p_live), "a live picture can never be changed by the worker")

# ── THE UPLOAD GUARD ───────────────────────────────────────────────────────
# A POSITIVE CONTROL FIRST (the first version of this test passed with the
# guard deleted, because the quiet window had closed intake and NOTHING was
# claimable — a check that cannot fail). Quiet window off, then prove an
# ordinary released picture IS claimed before proving the other is not.
P.set_setting(db, "earnings_quiet_from", "off", by="t"); db.commit()   # "" falls back to 22:00
t0, p_ok = picture("processed"); pi_ok = painting(t0, p_ok, "approved")
pi_ok.storage_path = "ok/print.jpg"
good = M.UploadTracking(saved_poster_id=p_ok.id, processed_image_id=pi_ok.id,
                        account_id=acct.id, project_id=proj.id, target_site="fineartamerica",
                        remote_title="Place 0", status="pending")
db.add(good); db.commit()
claim = P.claim_upload_batch(db, node="win", account_id=acct.id)
ok([i["tracking_id"] for i in (claim.get("items") or [])] == [good.id],
   "control: a released picture's row IS claimed")
db.delete(good); p_ok.pipeline_status = "uploaded"; pi_ok.is_current = 0; db.commit()

t5, p_old = picture("processed"); pi_old = painting(t5, p_old, "step2")
stale = M.UploadTracking(saved_poster_id=p_old.id, processed_image_id=pi_old.id,
                         account_id=acct.id, project_id=proj.id, target_site="fineartamerica",
                         remote_title="Place 5", status="pending")
db.add(stale); db.commit()
claim = P.claim_upload_batch(db, node="win", account_id=acct.id)
ok(not (claim.get("items") or []), "the uploader refuses a row whose picture is waiting for step 2")
chk = D.check_queued_uploads_point_at_released_pictures(db, D.Scope(db, proj.id))
ok(chk.count == 1, "Diagnostics lists that refused row")
db.delete(stale); db.commit()

# ── BATCH SEND (fakes record what would go to OpenAI) ──────────────────────
sentfiles, created = [], []
def fake_upload(key, path):
    sentfiles.append(Path(path).read_text(encoding="utf-8")); return f"file-{len(sentfiles)}"
def fake_create(key, fid):
    created.append(fid)
    return {"id": f"batch_{len(created)}", "status": "validating",
            "request_counts": {"total": 0, "completed": 0, "failed": 0},
            "expires_at": int((datetime.utcnow() + timedelta(hours=24)).timestamp())}
G.batch_upload_file, G.batch_create = fake_upload, fake_create

ok(AB.send_due(db, proj), "a send goes out")
b2 = db.query(M.AIBatch).filter_by(stage=2).one()
b1 = db.query(M.AIBatch).filter_by(stage=1).one()
lines2 = [json.loads(l) for l in sentfiles[0].splitlines()]
body = lines2[0]["body"]
ok(len(lines2) == 5, f"step 2 batch carries the five waiting paintings ({len(lines2)})")
ok(lines2[0]["url"] == "/v1/images/edits" and body["model"] == "gpt-image-2-2026-04-21"
   and body["quality"] == "medium" and body["background"] == "opaque"
   and body["size"] == "1024x1536" and body["n"] == 1,
   "step 2 lines: pinned model, medium, opaque, same shape as the painting, n=1")
ok(len(body["images"]) == 1 and body["images"][0]["image_url"].startswith("data:image/png;base64,"),
   "step 2 sends the painting alone, as a PNG data URL")
raw = base64.b64decode(body["images"][0]["image_url"].split(",", 1)[1])
ok(Image.open(io.BytesIO(raw)).mode == "RGBA", "the see-through painting is sent see-through")
lines1 = [json.loads(l) for l in sentfiles[1].splitlines()]
ok(len(lines1) == 1 and f"-p{retried.id}-" in lines1[0]["custom_id"],
   "step 1 batch (step 1 OFF) carries only the picture named by hand")
db.refresh(retried)
ok(retried.pipeline_status == "processing" and retried.claimed_by == f"batch:{b1.id}"
   and retried.claimed_at is None and retried.paint_requested_at is not None,
   "a batch painting is claimed with NO claim time (the reaper cannot free it), mark KEPT while away")
P.reap_stale_claims(db); db.commit(); db.refresh(retried)
ok(retried.pipeline_status == "processing", "the stale-claim reaper leaves a batch claim alone")
ok(GW.step2_queue(db, proj).count() == 0, "paintings at OpenAI are not sent twice")
ok(not AB.send_due(db, proj), "no second send inside the gather window")

# ── COLLECT: OpenAI answers ────────────────────────────────────────────────
out_png = base64.b64encode(png(rgba=False)).decode()
def answer(cid, ok_=True):
    if ok_:
        return {"custom_id": cid, "response": {"status_code": 200, "body": {
            "data": [{"b64_json": out_png}], "usage": {"output_tokens": 10}}}}
    return {"custom_id": cid, "response": {"status_code": 400, "body": {
        "error": {"message": "Your request was rejected by the safety system. safety_violations=[violence]",
                  "type": "invalid_request_error"}}}}

# The worker swaps the HELD picture's photo while it is away: its answer
# must not be filed over the new state.
db.refresh(pi_held); pi_held.is_current = 0; pi_held.review_status = "superseded"; db.commit()

by_poster = {int(l["custom_id"].split("-p")[1].split("-")[0]): l["custom_id"] for l in lines2}
files = {
    "out2": "\n".join(json.dumps(answer(by_poster[p.id])) for p in (p_pending, p_held, p_appr, p_live)),
    "err2": json.dumps(answer(by_poster[p_old.id], ok_=False)),
    "out1": json.dumps(answer(lines1[0]["custom_id"])),
}
def fake_get(key, bid):
    if bid == b2.openai_batch_id:
        return {"id": bid, "status": "completed", "output_file_id": "out2", "error_file_id": "err2",
                "request_counts": {"total": 5, "completed": 4, "failed": 1}}
    return {"id": bid, "status": "completed", "output_file_id": "out1", "error_file_id": None,
            "request_counts": {"total": 1, "completed": 1, "failed": 0}}
def fake_download(key, fid, dest):
    Path(dest).write_text(files[fid], encoding="utf-8")
G.batch_get, G.file_download = fake_get, fake_download
for b in (b1, b2):
    b.last_checked_at = None
db.commit()
AB.poll(db, proj)
db.refresh(b1); db.refresh(b2)
ok(b1.collected_at and b2.collected_at, "both batches are collected")

s2 = {pi.saved_poster_id: pi for pi in db.query(M.ProcessedImage).filter_by(stage=2).all()}
ok(set(s2) == {p_pending.id, p_appr.id, p_live.id},
   "step 2 pictures filed for the three still waiting — not for the one that changed meanwhile")
row = s2[p_pending.id]
ok(row.is_current == 1 and row.review_status == "pending" and row.step2_from == pi_pending.id
   and row.master_path.endswith("_step2.png") and row.storage_path == "",
   "the step 2 picture waits on Approve Artwork, named after its painting, print file not built yet")
db.refresh(pi_pending)
ok(pi_pending.review_status == "painted" and pi_pending.is_current == 0,
   "its painting steps aside but is KEPT as a source for redoing step 2")
items = {it.saved_poster_id: it for it in db.query(M.AIBatchItem).filter_by(batch_id=b2.id).all()}
ok(items[p_held.id].outcome == "dropped", "the changed one is recorded as dropped, not filed")
db.refresh(pi_old)
ok(pi_old.review_status == "step2_failed" and "safety" in (pi_old.step2_error or ""),
   "a refused line parks the painting on Needs Attention with OpenAI's words")
db.refresh(retried)
new_painting = db.query(M.ProcessedImage).filter_by(saved_poster_id=retried.id, is_current=1).one()
ok(retried.paint_requested_at is None, "the hand-named mark is spent when the painting is filed")
ok(retried.pipeline_status == "processed" and new_painting.review_status == "step2",
   "a batch PAINTING is filed and goes straight on to step 2")

# ── THE LIVE LISTING: approve its step 2 picture -> on the swap list ───────
live2 = s2[p_live.id]
live2.review_status = "approved"; live2.storage_path = live2.master_path[:-4] + ".jpg"
db.commit()
sw = P.swap_waiting(db, proj).all()
ok(len(sw) == 1 and sw[0][0].id == live_row.id and sw[0][1].id == live2.id,
   "the live listing now waits to have its picture swapped")
P.ensure_upload_rows(db, poster=p_live, title=t4, processed=live2, project=proj); db.commit()
ok(db.query(M.UploadTracking).filter_by(saved_poster_id=p_live.id).count() == 1,
   "approving it does NOT queue a second listing")
live_row.processed_image_id = live2.id; db.commit()
ok(P.swap_waiting(db, proj).count() == 0, "recording the swap takes it off the list by itself")

# ── RECOVERY ───────────────────────────────────────────────────────────────
t6, p_s = picture("greenlit"); p_s.paint_requested_at = datetime.utcnow(); db.commit()
bx = M.AIBatch(project_id=proj.id, stage=1, status="sending"); db.add(bx); db.flush()
p_s.pipeline_status = "processing"; p_s.claimed_by = f"batch:{bx.id}"
db.add(M.AIBatchItem(batch_id=bx.id, saved_poster_id=p_s.id, custom_id="x")); db.commit()
ok(AB.recover_unsent(db) == 1, "a send cut short by a restart is found at start-up")
db.commit(); db.refresh(p_s); db.refresh(bx)
ok(p_s.pipeline_status == "greenlit" and bx.status == "send_failed",
   "its picture is back in the queue and the batch says why")
ok(p_s.paint_requested_at is not None and p_s.id in {sp.id for sp, _t in GW.paint_queue(db, proj).all()},
   "step 1 OFF: a hand-named picture back from a cut-short send keeps its mark and is painted again")
p_s.pipeline_status = "processing"; p_s.claimed_by = "batch:999"; db.commit()
chk = D.check_batch_claims_have_an_open_batch(db, D.Scope(db, proj.id))
ok(chk.count == 1 and chk.severity == "error", "Diagnostics: a picture held by a closed batch is an error")
AB.recover_unsent(db); db.commit(); db.refresh(p_s)
ok(p_s.pipeline_status == "greenlit", "start-up repair frees a picture held by a closed batch")

# ── STEP 2 SWITCHED OFF WITH WORK WAITING ─────────────────────────────────
P.set_setting(db, "step2_enabled", False, project=proj, by="t"); db.commit()
chk = D.check_step2_work_can_move(db, D.Scope(db, proj.id))
ok(chk.count >= 1 and chk.severity == "error", "Diagnostics: paintings waiting for a step 2 that is off")
P.set_setting(db, "step2_enabled", True, project=proj, by="t"); db.commit()

# ── SEND REFUSED BY OPENAI -> everything back, no retry for an hour ────────
t7, p_r = picture("greenlit"); p_r.paint_requested_at = datetime.utcnow()
for b in db.query(M.AIBatch).all():
    b.created_at = datetime.utcnow() - timedelta(hours=2)
db.commit()
def refuse(key, fid):
    raise G.PermanentFailure("model does not support batch", kind="bad_request")
G.batch_create = refuse
AB.send_due(db, proj)
db.refresh(p_r)
last = db.query(M.AIBatch).order_by(M.AIBatch.id.desc()).first()
ok(p_r.pipeline_status == "greenlit" and last.status == "send_failed"
   and last.error.startswith("[refused]"), "a refused send puts every picture back and records why")
ok(AB._recently_refused(db, proj, 1), "and is not retried for an hour")
ok(p_r.paint_requested_at is not None and p_r.id in {sp.id for sp, _t in GW.paint_queue(db, proj).all()},
   "step 1 OFF: a hand-named picture from a REFUSED batch keeps its mark (TO_TEST 113's 95)")

# ── THE LIVE CALL'S FAILURES: the mark lasts exactly as long as the tries ──
t8, p_t = picture("greenlit"); p_t.paint_requested_at = datetime.utcnow(); db.commit()
taken, _t = GW._claim_next(db, proj)
ok(taken is not None and taken.pipeline_status == "processing" and taken.paint_requested_at is not None,
   "the live call takes a hand-named picture and the mark stays")
taken.pipeline_status = "greenlit"; taken.claimed_by = None; taken.claimed_at = None
p_t.pipeline_status = "processing"; p_t.claimed_by = "server"; db.commit()
GW.record_paint_failure(db, p_t, t8, proj, G.TransientFailure("timeout")); db.commit()
ok(p_t.pipeline_status == "greenlit" and p_t.paint_requested_at is not None
   and p_t.id in {sp.id for sp, _t in GW.paint_queue(db, proj).all()},
   "a temporary error puts it back in the queue STILL MARKED")
p_t.process_attempts = 99; p_t.pipeline_status = "processing"; db.commit()
GW.record_paint_failure(db, p_t, t8, proj, G.TransientFailure("timeout")); db.commit()
ok(p_t.pipeline_status == "failed_processing" and p_t.paint_requested_at is None,
   "past the last try the mark ends, so the panel stops counting it as waiting")
t9, p_p = picture("greenlit"); p_p.paint_requested_at = datetime.utcnow(); db.commit()
GW.record_paint_failure(db, p_p, t9, proj, G.PermanentFailure("no", kind="moderation")); db.commit()
ok(p_p.paint_requested_at is None, "a permanent refusal ends the mark too (RETRY sets it again)")

# ── NAMING: a redo of step 2 never overwrites the first ────────────────────
names = GW.step2_names(db, t1, p_pending, pi_pending, proj)
ok(names["master"].endswith("_step2-2.png"), f"a second step 2 from one painting gets its own name ({names['master']})")

print("\nALL STEP 2 / BATCH CHECKS PASSED")
