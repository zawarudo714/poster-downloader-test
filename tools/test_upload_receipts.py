"""
Behaviour test: a picture that is LIVE on the marketplace is never reported
or retried as failed, and a picture marked unusable is never handed out for
upload (v287).

Until v287 the node sent its "done" message inside the same try as the
upload, so a "done" that did not get through (a 500, a locked database)
fell into the catch-all and was reported as FAILED. The server took that
failure even for a row it already had as uploaded, and RETRY ALL AFFECTED
put live rows back in the queue. Both ways the next run listed the same
picture a second time.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Never talks to a marketplace. Run from the repo root:

    python tools/test_upload_receipts.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_receipts_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
from datetime import date
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
boss = M.User(username="boss", password_hash="x", role="admin")
db.add(boss); db.flush()
acc = M.UploadAccount(name="one", email="a@x", password_enc="x", project_id=proj.id,
                      artist_name="artist-one", daily_limit=100)
db.add(acc); db.flush()
db.add(M.AccountProject(account_id=acc.id, project_id=proj.id)); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
n = [0]


def poster(track_status, poster_status="processed"):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=boss.id, username="boss",
                       project_folder="travel", original_save_date=date(2026, 10, 1),
                       title_folder_path="x", filename=f"{t.id}.jpg", source_url=f"u{t.id}",
                       pipeline_status=poster_status)
    db.add(sp); db.flush()
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="n.jpg",
                          filename="n.jpg", is_current=1, review_status="approved")
    db.add(pi); db.flush()
    tr = M.UploadTracking(saved_poster_id=sp.id, processed_image_id=pi.id,
                          account_id=acc.id, project_id=proj.id, status=track_status,
                          remote_title=t.title)
    db.add(tr); db.flush()
    return sp, tr


# ── A FAILURE REPORT FOR A LIVE PICTURE IS IGNORED ────────────────────────
_sp, live = poster("uploaded", "uploaded")
db.commit()
P.report_upload_failure(db, tracking_id=live.id, node="n", error="500 on the receipt")
db.commit(); db.refresh(live)
ok(live.status == "uploaded", f"a failure report does not unmark a live picture ({live.status})")

# ── RETRY ALL AFFECTED LEAVES LIVE AND IN-FLIGHT ROWS ALONE ───────────────
_sp, failed = poster("failed")
_sp, flying = poster("uploading", "uploading")
db.commit()
P.retry_uploads(db, [live.id, failed.id, flying.id]); db.commit()
for r in (live, failed, flying):
    db.refresh(r)
ok(live.status == "uploaded", f"RETRY leaves a live picture live ({live.status})")
ok(flying.status == "uploading", f"RETRY leaves a picture being uploaded alone ({flying.status})")
ok(failed.status == "pending", f"RETRY still requeues a genuine failure ({failed.status})")

# ── UNUSABLE MEANS NOT LISTED ─────────────────────────────────────────────
db.query(M.UploadTracking).filter(M.UploadTracking.id != live.id).delete()
db.commit()
_sp, wanted = poster("pending")
bad_sp, banned = poster("pending", "unusable")
db.commit()
claim = P.claim_upload_batch(db, node="n", project_id=proj.id, limit=10)
db.commit()
got = {i["tracking_id"] for i in claim["items"]}
ok(wanted.id in got, f"a ready picture is handed out ({sorted(got)})")
ok(banned.id not in got, "a picture marked UNUSABLE is never handed out for upload")
db.refresh(bad_sp)
ok(bad_sp.pipeline_status == "unusable", "and it stays unusable")

src = inspect.getsource(P.claim_upload_batch)
ok('SavedPoster.pipeline_status != "unusable"' in src, "the unusable rule is in the shipped claim")

# ── THE NODE KEEPS A RECEIPT IT COULD NOT SEND ────────────────────────────
from worker_service import uploader as U


class Client:
    def __init__(self):
        self.down = True
        self.sent, self.failed, self.claims = [], [], 0

    def report_uploaded(self, tracking_id, **_):
        if self.down:
            raise ConnectionError("server busy")
        self.sent.append(tracking_id)

    def report_upload_failure(self, tracking_id, **_):
        self.failed.append(tracking_id)

    def claim_upload_batch(self, **_):
        self.claims += 1
        return {"account": None, "items": []}


lines = []
client = Client()
stage = U.UploadStage(client, {"temp_dir": _TMP}, lambda *a, **k: lines.append(a[0]))
stage.receipts_file = Path(_TMP) / "unsent_receipts.json"

ok(stage._send_receipt(41, waits=(0, 0)) is False, "a receipt the server will not take is not lost…")
ok(stage._unsent() == [41], f"…it is written down ({stage._unsent()})")
ok(client.failed == [], "and the live picture is NOT reported as failed")
r = stage.run_batch()
ok(r["claimed"] == 0 and client.claims == 0,
   "nothing new is claimed while a receipt is still unsent")
client.down = False
r = stage.run_batch()
ok(client.sent == [41] and not stage.receipts_file.exists(),
   f"the kept receipt is sent first, and forgotten once taken ({client.sent})")
ok(client.claims == 1, "then the run claims as normal")

# The item loop: the receipt is sent in the try's ELSE, never inside the
# try whose catch-all reports a failure.
run_src = inspect.getsource(U.UploadStage.run_batch)
body = run_src.split("uploader.upload_one(item, image_path)", 1)[1]
before_else = body.split("\n                else:", 1)[0]
ok("report_uploaded" not in before_else and "_send_receipt" not in before_else,
   "the receipt is not sent inside the upload's try")
ok("_send_receipt(item[\"tracking_id\"])" in body.split("\n                else:", 1)[1],
   "the receipt is sent in its else")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
# The old server rule — take every failure report — must break the first
# check. Run the shipped function with its guard line removed.
print("\n  sabotage — the server takes a failure report for a live picture")
fsrc = inspect.getsource(P.report_upload_failure)
guard = '    if tracking.status == "uploaded":\n        return\n'
ok(guard in fsrc, "the guard is in the shipped function")
ns = dict(P.__dict__)
exec(fsrc.replace(guard, ""), ns)
_sp, live2 = poster("uploaded", "uploaded"); db.commit()
ns["report_upload_failure"](db, tracking_id=live2.id, node="n", error="x")
db.commit(); db.refresh(live2)
ok(live2.status != "uploaded", f"without the guard the live picture IS unmarked ({live2.status})")

print("\n  sabotage — RETRY that requeues every row it is given")
rsrc = inspect.getsource(P.retry_uploads)
rule = ',\n                      UploadTracking.status.notin_(("uploaded", "uploading"))'
ok(rule in rsrc, "the RETRY rule is where the sabotage expects it")
ns = dict(P.__dict__)
exec(rsrc.replace(rule, ""), ns)
ns["retry_uploads"](db, [live2.id]); db.commit()
live2.status = "uploaded"; db.commit()
ns["retry_uploads"](db, [live2.id]); db.commit(); db.refresh(live2)
ok(live2.status == "pending", "without the rule RETRY requeues a live picture (so the check can fail)")

print("\n  sabotage — the claim without the unusable rule")
csrc = inspect.getsource(P.claim_upload_batch)
rule = ('                      or_(SavedPoster.pipeline_status.is_(None),\n'
        '                          SavedPoster.pipeline_status != "unusable"),\n')
ok(rule in csrc, "the rule line is where the sabotage expects it")
ns = dict(P.__dict__)
exec(csrc.replace(rule, ""), ns)
banned.status = "pending"; bad_sp.pipeline_status = "unusable"; db.commit()
claim = ns["claim_upload_batch"](db, node="n", project_id=proj.id, limit=10)
db.rollback()
ok(banned.id in {i["tracking_id"] for i in claim["items"]},
   "without the rule the unusable picture IS handed out (so the check can fail)")

print("\nALL UPLOAD RECEIPT CHECKS PASSED")
