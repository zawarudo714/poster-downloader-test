"""
Behaviour test: a picture OpenAI has already been paid for is never thrown
away and painted again because filing it failed (v287).

Filing a picture needs the Storage Box AND a database write. Until v287
either one failing deleted the picture and put the photo back in the queue,
so it was painted, and paid for, a second time. Now the picture is kept
(gpt_worker.keep_paid) until its row is committed, and the painter, the
step-2 painter, the batch sender and file_kept() all use the kept picture
instead of going back to OpenAI.

Also: a batch whose "start" request got no answer is left for the next
poll to settle, instead of being closed and every picture sent again.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. No network: every OpenAI call is replaced. Storage is the local
fallback folder. Run from the repo root:

    python tools/test_paid_kept.py
"""
import io
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_paid_kept_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
from PIL import Image
from sqlalchemy.exc import OperationalError
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import gpt_images as G, gpt_worker as GW, ai_batches as AB
from app import storage_remote as SR

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (1024, 1536), (40, 90, 160, 120)).save(out, "PNG")
    return out.getvalue()


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
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.commit()
n = [0]


def picture(status):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=date(2026, 10, 20),
                       title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"{n[0]}.jpg",
                       source_url="u", pipeline_status=status)
    db.add(sp); db.flush()
    from app.utils import saved_poster_path
    f = saved_poster_path(sp); f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 1200), (200, 120, 60)).save(f, "JPEG")
    return t, sp


paid = []
def no_second_payment(*a, **k):
    paid.append(1)
    raise AssertionError("OpenAI was asked to paint a picture already paid for")
G.generate = G.generate_step2 = no_second_payment

_real_write = SR.write_bytes
def storage_down(*a, **k):
    raise SR.StorageError("Storage Box not answering")


def painted(sp):
    return db.query(M.ProcessedImage).filter_by(saved_poster_id=sp.id, is_current=1).first()


# ── THE STORAGE BOX FAILS ─────────────────────────────────────────────────
t1, p1 = picture("processing"); p1.claimed_by = "server"; db.commit()
SR.write_bytes = storage_down
ok(GW.file_painting(db, p1, t1, proj, G.Generation(image_bytes=png())) is False,
   "storage down: the painting is not filed")
db.refresh(p1)
ok(p1.pipeline_status == "greenlit" and GW.has_kept(1, p1.id),
   f"…the photo is back in the queue, and the paid picture is KEPT ({p1.pipeline_status})")
ok("not paid for again" in (p1.process_error or ""), "the error says the picture is kept")
SR.write_bytes = _real_write
p1.pipeline_status = "processing"; p1.claimed_by = "server"; db.commit()
ok(GW.process_one(db, p1, t1, proj) is True and painted(p1) is not None,
   "storage back: the painter files the kept picture")
ok(paid == [] and not GW.has_kept(1, p1.id), "without paying again, and the kept copy is let go")

# ── THE DATABASE IS LOCKED AT THE LAST STEP ───────────────────────────────
t2, p2 = picture("processing"); p2.claimed_by = "server"; db.commit()
_real_recompute = P.recompute_title_status
def locked(*a, **k):
    raise OperationalError("UPDATE …", {}, Exception("database is locked"))
P.recompute_title_status = locked
try:
    GW.file_painting(db, p2, t2, proj, G.Generation(image_bytes=png()))
    ok(False, "a locked database raises")
except OperationalError:
    db.rollback()
finally:
    P.recompute_title_status = _real_recompute
ok(GW.has_kept(1, p2.id) and painted(p2) is None,
   "database locked: nothing is recorded, and the paid picture is KEPT")
P.report_process_failure(db, poster_id=p2.id, node="server", error="database is locked")
db.commit()                                     # what the painting loop does next
ok(GW.file_kept(db, proj) is True and painted(p2) is not None,
   "the next pass files the kept picture")
ok(paid == [] and not GW.has_kept(1, p2.id), "without paying again")

# ── A KEPT PICTURE FOR A PHOTO THAT WENT AWAY IS LET GO ───────────────────
t3, p3 = picture("greenlit"); db.commit()
GW.keep_paid(1, p3.id, png())
p3.deleted_at = datetime.utcnow(); db.commit()
GW.file_kept(db, proj)
ok(not GW.has_kept(1, p3.id) and painted(p3) is None, "a deleted photo's kept picture is let go")

# ── STEP 2 ────────────────────────────────────────────────────────────────
t4, p4 = picture("processed")
rel, name = P.storage_path_for(db, t4, p4, project=proj, attempt=1)
master = rel.rsplit(".", 1)[0] + "_master.png"
_real_write(db, master, png(), project=proj)
pi4 = M.ProcessedImage(saved_poster_id=p4.id, project_id=proj.id, storage_path="",
                       filename=name, is_current=1, review_status="step2",
                       attempt=1, master_path=master, preview_path="p.jpg")
db.add(pi4); db.commit()
SR.write_bytes = storage_down
ok(GW.file_step2(db, pi4, p4, t4, proj, G.Generation(image_bytes=png())) is False,
   "storage down: step 2 is not filed")
db.commit()
SR.write_bytes = _real_write
ok(GW.has_kept(2, pi4.id), "…and the paid step-2 picture is KEPT")
ok(GW.process_step2_one(db, pi4, p4, t4, proj) is True,
   "storage back: the step-2 painter files the kept picture")
db.refresh(pi4)
ok(paid == [] and not GW.has_kept(2, pi4.id) and pi4.review_status == "painted",
   "without paying again")

# ── THE BATCH SENDER LEAVES OUT WHAT IS ALREADY PAID FOR ──────────────────
sent = []
G.batch_upload_file = lambda key, path: sent.append(Path(path).read_text(encoding="utf-8")) or "file-1"
G.batch_create = lambda key, fid: {"id": "batch_1", "status": "validating",
                                   "request_counts": {"total": 0}}
t5, p5 = picture("greenlit"); p5.paint_requested_at = datetime.utcnow()
t6, p6 = picture("greenlit"); p6.paint_requested_at = datetime.utcnow()
db.commit()
GW.keep_paid(1, p5.id, png())
AB.send_due(db, proj)
body = "".join(sent)
ok(f"-p{p6.id}-" in body, "a photo waiting to be painted goes in the batch")
ok(f"-p{p5.id}-" not in body, "a photo whose painting is kept does NOT go to OpenAI again")
db.refresh(p5)
ok(p5.pipeline_status == "greenlit", "it stays in the queue for file_kept()")
GW.forget_paid(1, p5.id)

# ── A BATCH WHOSE START GOT NO ANSWER IS ASKED ABOUT, NOT SENT AGAIN ──────
for b in db.query(M.AIBatch).all():
    b.created_at = datetime.utcnow() - timedelta(hours=2)
db.commit()
def no_answer(key, fid):
    raise G.TransientFailure("network error: read timed out")
G.batch_create = no_answer
sent.clear()
AB.send_due(db, proj)
bx = db.query(M.AIBatch).order_by(M.AIBatch.id.desc()).first()
db.refresh(p5)
ok(bx.status == "sending" and bx.collected_at is None and bx.input_file_id,
   f"no answer when starting it: the batch stays 'sending' for the next poll ({bx.status})")
ok(p5.pipeline_status == "processing", "and its pictures are NOT put back to be sent again")
ok("checking with OpenAI" in (bx.error or ""), "it says what happens next")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
print("\n  sabotage — the painting not kept before it is filed")
src = inspect.getsource(GW.file_painting)
line = "    keep_paid(1, poster.id, gen.image_bytes)\n"
ok(line in src, "the keep line is in the shipped function")
ns = dict(GW.__dict__)
exec(src.replace(line, ""), ns)
t7, p7 = picture("processing"); p7.claimed_by = "server"; db.commit()
SR.write_bytes = storage_down
ns["file_painting"](db, p7, t7, proj, G.Generation(image_bytes=png()))
SR.write_bytes = _real_write
ok(not GW.has_kept(1, p7.id), "without it the paid picture IS lost (so the check can fail)")

print("\n  sabotage — a start with no answer closed as 'not sent'")
src = inspect.getsource(AB._send_one_file)
cond = "if isinstance(e, G.TransientFailure) and batch.input_file_id:"
ok(cond in src, "the branch is in the shipped function")
ns = dict(AB.__dict__)
exec(src.replace(cond, "if False:"), ns)
for b in db.query(M.AIBatch).all():
    b.created_at = datetime.utcnow() - timedelta(hours=2)
t8, p8 = picture("greenlit"); p8.paint_requested_at = datetime.utcnow(); db.commit()
path = Path(_TMP) / "one.jsonl"
path.write_text('{"custom_id": "s1-x"}\n', encoding="utf-8")
ns["_send_one_file"](db, proj, stage=1, model="m", api_key="k", path=path,
                     items=[{"cid": "s1-x", "poster_id": p8.id, "processed_id": None}])
db.refresh(p8)
ok(p8.pipeline_status == "greenlit",
   "without it the picture IS put back to be sent again (so the check can fail)")

print("\nALL PAID-PICTURE CHECKS PASSED")
