"""
Behaviour test: the daily upload limit counts the LAST 24 HOURS, and the
node stops a run when FineArtAmerica logs the account out or after three
failures in a row (v293).

MEASURED 2026-11-05: the limit counted a calendar day in Nairobi time
against upload times stored in UTC, so from 00:00 to 03:00 Nairobi it
counted nothing. 100 pictures went up in the morning, 101 more just after
midnight, and FineArtAmerica logged the account out ("Error Code: 2404").
The node then kept going and 117 more pictures failed on the logged-out
form.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Never talks to a marketplace. Run from the repo root:

    python tools/test_upload_limit_24h.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_limit24_")
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
boss = M.User(username="boss", password_hash="x", role="admin")
db.add(boss); db.flush()
acc = M.UploadAccount(name="one", email="a@x", password_enc="x", project_id=proj.id,
                      artist_name="artist-one", daily_limit=100)
db.add(acc); db.flush()
db.add(M.AccountProject(account_id=acc.id, project_id=proj.id)); db.flush()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
n = [0]


def poster(track_status, uploaded_at=None):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=boss.id, username="boss",
                       project_folder="travel", original_save_date=date(2026, 10, 1),
                       title_folder_path="x", filename=f"{t.id}.jpg", source_url=f"u{t.id}",
                       pipeline_status="uploaded" if track_status == "uploaded" else "processed")
    db.add(sp); db.flush()
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="n.jpg",
                          filename="n.jpg", is_current=1, review_status="approved")
    db.add(pi); db.flush()
    tr = M.UploadTracking(saved_poster_id=sp.id, processed_image_id=pi.id,
                          account_id=acc.id, project_id=proj.id, status=track_status,
                          remote_title=t.title, uploaded_at=uploaded_at)
    db.add(tr); db.flush()
    return tr


# ── LAST NIGHT, REPLAYED ──────────────────────────────────────────────────
# 100 uploads at 21:00-21:50 UTC on 4 Nov = 00:00-00:50 Nairobi on 5 Nov,
# the window the old calendar-day count did not see.
night = datetime(2026, 11, 4, 21, 0)
rows = [poster("uploaded", night + timedelta(seconds=30 * i)) for i in range(100)]
db.commit()

q = P.account_quota(db, acc, now=datetime(2026, 11, 4, 22, 0))
ok(q["used"] == 100 and q["remaining"] == 0,
   f"an hour after 100 uploads, nothing is left ({q})")
q = P.account_quota(db, acc, now=datetime(2026, 11, 5, 20, 59))
ok(q["remaining"] == 0, f"23 hours later, still nothing is left ({q})")
q = P.account_quota(db, acc, now=datetime(2026, 11, 5, 21, 10))
ok(0 < q["remaining"] < 100,
   f"24 hours after the first ones, those come back one by one ({q})")
q = P.account_quota(db, acc, now=datetime(2026, 11, 6, 0, 0))
ok(q["used"] == 0 and q["remaining"] == 100,
   f"uploads older than 24 hours are not counted ({q})")

# The claim hands out nothing while the last 24 hours are full.
for _ in range(5):
    poster("pending")
db.commit()
recent = datetime.utcnow() - timedelta(hours=1)
for r in rows:
    r.uploaded_at = recent
db.commit()
claim = P.claim_upload_batch(db, node="n")
db.commit()
ok(not claim.get("items"),
   f"the claim hands out nothing after 100 in the last 24 hours "
   f"({len(claim.get('items') or [])} handed out)")
for r in rows:
    r.uploaded_at = datetime.utcnow() - timedelta(hours=25)
db.commit()
claim = P.claim_upload_batch(db, node="n")
db.commit()
ok(len(claim.get("items") or []) == 5,
   f"and hands the queue out once they are 25 hours old "
   f"({len(claim.get('items') or [])} handed out)")

# ── THE NODE STOPS ────────────────────────────────────────────────────────
from worker_service import uploader as U

LOGGED_OUT_PAGE = ("<div class='error'>You have been logged out of your account due "
                   "to inactivity.  Please login again.  Error Code: 2404</div>")
ok(U.logged_out(LOGGED_OUT_PAGE), "the real FineArtAmerica message is recognised")
ok(U.logged_out(LOGGED_OUT_PAGE.replace("  ", "\n   ")),
   "with any spacing or line breaks")
ok(not U.logged_out("<form>Title Description Tags</form>"),
   "an ordinary upload form is not taken for a log-out")


class Client:
    def __init__(self, items):
        self.items = items
        self.failures, self.sent = [], []

    def report_uploaded(self, tracking_id, **_):
        self.sent.append(tracking_id)

    def report_upload_failure(self, tracking_id, **kw):
        self.failures.append((tracking_id, kw))

    def claim_upload_batch(self, **_):
        return {"account": {"name": "one", "target_site": "fineartamerica",
                            "timings": {"between_images": 0}},
                "settings": {"storage_root": _TMP},
                "quota": {"used": 0, "limit": 100, "remaining": 100},
                "items": [{"tracking_id": i, "remote_title": f"T{i}"}
                          for i in range(1, self.items + 1)]}


def fake_uploader(outcomes):
    """outcomes[i] is what upload_one does for item i: 'ok', 'fail', 'out'."""
    class Fake:
        def __init__(self, **_):
            self.driver = type("D", (), {"page_source": ""})()

        def start(self): pass
        def login(self): pass
        def open_work_tab(self): pass
        def stop(self): pass
        def emit(self, *a, **k): pass
        def capture_evidence(self, label): return None

        def upload_one(self, item, image_path):
            what = outcomes[item["tracking_id"] - 1]
            if what == "out":
                self.driver.page_source = LOGGED_OUT_PAGE
                if U.logged_out(self.driver.page_source):
                    raise U.UploadError("logged out", pause_minutes=U.LOGGED_OUT_PAUSE_MINUTES,
                                        fatal=True, pause_reason="logged out")
            if what == "fail":
                raise U.UploadError("Submit did not complete")
    return Fake


def run(outcomes, run_batch=None):
    client = Client(len(outcomes))
    stage = U.UploadStage(client, {"temp_dir": _TMP}, lambda *a, **k: None)
    stage.receipts_file = Path(_TMP) / "unsent_receipts.json"
    stage._resolve_image = lambda item, root: Path(_TMP) / "x.jpg"
    run_batch = run_batch or U.UploadStage.run_batch
    # run_batch builds its uploader by name, from its own module globals.
    run_batch.__globals__["MarketplaceUploader"] = fake_uploader(outcomes)
    run_batch(stage)
    return client


c = run(["ok", "ok", "out", "ok", "ok", "ok"])
ok(c.sent == [1, 2] and [t for t, _ in c.failures] == [3],
   f"a log-out stops the run at once: 2 up, 1 failed, 3 untouched "
   f"(up {c.sent}, failed {[t for t, _ in c.failures]})")
ok(c.failures[0][1].get("pause_minutes") == 240,
   f"and the account is paused for 4 hours ({c.failures[0][1].get('pause_minutes')})")

c = run(["fail"] * 10)
ok(len(c.failures) == 3,
   f"ten form failures stop after 3 in a row ({len(c.failures)} tried)")

c = run(["fail", "fail", "ok", "fail", "fail", "ok", "fail", "fail", "ok"])
ok(len(c.failures) == 6 and len(c.sent) == 3,
   f"a success in between resets the count, so the whole batch runs "
   f"({len(c.sent)} up, {len(c.failures)} failed)")

# The real upload_one raises the log-out error, not only the fake above.
real_src = inspect.getsource(sys.modules["worker_service.uploader"])
ok("if logged_out(self.driver.page_source):" in real_src
   and "pause_minutes=LOGGED_OUT_PAUSE_MINUTES, fatal=True" in real_src,
   "the shipped upload checks for the log-out and stops fatally")

# ── SABOTAGE ──────────────────────────────────────────────────────────────
print("\n  sabotage — the old calendar-day count")
qsrc = inspect.getsource(P.uploads_last_24h)
cut = "    since = (now or datetime.utcnow()) - UPLOAD_LIMIT_WINDOW\n"
ok(cut in qsrc, "the 24-hour line is in the shipped count")
for i, r in enumerate(rows):
    r.uploaded_at = night + timedelta(seconds=30 * i)
db.commit()
# What the old code built at 01:00 Nairobi on 5 Nov: midnight of the
# Nairobi day, compared as if it were UTC.
ns2 = dict(P.__dict__)
exec(qsrc.replace(cut, "    since = datetime(2026, 11, 5, 0, 0)\n"), ns2)
used = ns2["uploads_last_24h"](db, acc.id, now=datetime(2026, 11, 4, 22, 0))
ok(used == 0,
   f"a count from midnight Nairobi stored as midnight UTC sees none of last night's 100 ({used})")

print("\n  sabotage — the node with no stop after failures in a row")
rsrc = inspect.getsource(U.UploadStage.run_batch)
stop = ("                    if in_a_row >= STOP_AFTER_FAILURES_IN_A_ROW:\n"
        "                        uploader.emit(\n"
        "                            f\"Stopping this run — {in_a_row} failures in a row.\",\n"
        "                            level=\"error\")\n"
        "                        break\n")
ok(rsrc.count(stop) == 2, "both stops are in the shipped run")
import textwrap
uns = dict(U.__dict__)
exec(textwrap.dedent(rsrc.replace(stop, "")), uns)
c = run(["fail"] * 10, run_batch=uns["run_batch"])
ok(len(c.failures) == 10, f"without the stop all ten are tried ({len(c.failures)})")

print("\nALL 24-HOUR LIMIT CHECKS PASSED")
