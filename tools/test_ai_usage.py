"""
Behaviour test: THE USAGE TAB COUNTS EVERY PAID ANSWER, ONCE (v292).

The Usage tab reads only ai_usage, so a paid answer missing from it, or
counted twice, makes the owner's spend chart wrong. Checked here:

  * prices: the measured step 2 answer (1,536 image + 249 text in, 343 out)
    costs $0.0119 by batch, double live;
  * a batch answer file: each billed line is one row on its UTC `created`
    day, a design that already had a painting is a repaint, a line with no
    usage (an error) adds nothing, and a line for a picture that was
    dropped is still counted; copying the file twice adds nothing;
  * collect() copies the counts from the file it downloads;
  * the sweep closes a batch with no answer file, copies one with a file,
    waits two minutes between batches, and notes a file OpenAI refuses;
  * a live painting and a dashboard test are recorded by gpt_images itself;
  * the report: tiles, repaint rate, cost per approved design, prompt
    versions split by when a request was SENT, and change markers;
  * a settings save logs which keys really changed;
  * Diagnostics warns about a batch the sweep never reached;
  * the page's pure JavaScript helpers, lifted from the shipped file;
  * sabotage: the repaint rule and the de-duplication each go red.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Run from the repo root:

    python tools/test_ai_usage.py
"""
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_ai_usage_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.exc import IntegrityError

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import ai_usage as U, ai_batches as AB, gpt_images as G, diagnostics as D
from app.routes import pipeline_admin as PA

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
w = M.User(username="humphrey", password_hash="x", role="worker")
boss = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, boss]); db.commit()
PA._project = lambda request, admin, db, explicit=None: proj

n = [0]


def poster(*, painted_at=None):
    """A design; with `painted_at`, it already has a painting from then."""
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id, title_folder_path=f"{n[0]}. Place {n[0]}")
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", title_folder_path=t.title_folder_path,
                       original_save_date=date(2026, 10, 1),
                       filename=f"{n[0]}.jpg", source_url=f"u{n[0]}",
                       pipeline_status="processed")
    db.add(sp); db.flush()
    if painted_at:
        db.add(M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id,
                                storage_path=f"s/{n[0]}.png", filename=f"{n[0]}.png",
                                created_at=painted_at))
    db.commit()
    return sp


def answer(cid, created, text=1031, image=2477, out=158, status=200):
    body = {"created": int(created.timestamp()), "data": [{"b64_json": "x"}]}
    if status == 200:
        body["usage"] = {"input_tokens": text + image, "output_tokens": out,
                         "input_tokens_details": {"text_tokens": text, "image_tokens": image}}
    else:
        body = {"error": {"message": "refused"}}
    return json.dumps({"custom_id": cid, "response": {"status_code": status, "body": body}})


def epoch(s):
    """A naive UTC datetime whose .timestamp() is that UTC moment."""
    from datetime import timezone
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=timezone.utc)


# ── Prices ────────────────────────────────────────────────────────────────
c = U.cost(249, 1536, 343, "batch")
ok(abs(c - 0.0119115) < 1e-6, f"a step 2 answer costs $0.0119 by batch (got {c})")
ok(abs(U.cost(249, 1536, 343, "live") - 2 * c) < 3e-6, "live costs double")
ok(U.tokens_of({"input_tokens": 3508, "output_tokens": 158,
                "input_tokens_details": {"text_tokens": 1031, "image_tokens": 2477}})
   == (1031, 2477, 158), "tokens_of reads text, image and out")
ok(U.tokens_of({"input_tokens": 100, "output_tokens": 5}) == (0, 100, 5),
   "tokens_of with no detail counts the input as image")

# ── One answer file ──────────────────────────────────────────────────────
sent = datetime(2026, 11, 3, 22, 0)
a = poster()
b = poster(painted_at=datetime(2026, 10, 30, 12, 0))
gone = poster()
batch = M.AIBatch(project_id=proj.id, stage=1, model="gpt-image-x", status="completed",
                  output_file_id="file-out-1", created_at=sent,
                  collected_at=datetime(2026, 11, 4, 2, 0))
db.add(batch); db.flush()
for sp, cid in ((a, "s1-a"), (b, "s1-b"), (gone, "s1-gone"), (a, "s1-err")):
    db.add(M.AIBatchItem(batch_id=batch.id, saved_poster_id=sp.id, custom_id=cid))
db.commit()
f1 = Path(_TMP) / "out1.jsonl"
f1.write_text("\n".join([
    answer("s1-a", epoch("2026-11-03T23:59:00")),
    answer("s1-b", epoch("2026-11-04T00:01:00")),
    answer("s1-gone", epoch("2026-11-04T00:02:00")),
    answer("s1-err", epoch("2026-11-04T00:03:00"), status=500),
    "not json",
    answer("s1-unknown", epoch("2026-11-04T00:04:00")),
]), encoding="utf-8")

added = U.record_file(db, proj, batch, f1)
rows = {r.custom_id: r for r in db.query(M.AIUsage).filter_by(batch_id=batch.id)}
ok(added == 4, f"4 billed lines become 4 rows; the error line adds none (got {added})")
ok(rows["s1-a"].occurred_at == datetime(2026, 11, 3, 23, 59),
   "a row sits on OpenAI's created time, in UTC")
ok(rows["s1-b"].occurred_at.date() == date(2026, 11, 4), "…so 00:01 is the next UTC day")
ok(rows["s1-a"].kind == "first", "a design with no painting before is a first painting")
ok(rows["s1-b"].kind == "repaint", "a design that already had a painting is a repaint")
ok("s1-unknown" in rows and rows["s1-unknown"].saved_poster_id is None,
   "a billed line for a picture with no item is still counted")
ok(rows["s1-a"].sent_at == sent, "sent_at is when the batch left the server")
ok(rows["s1-a"].mode == "batch" and abs(rows["s1-a"].cost_usd - U.cost(1031, 2477, 158, "batch")) < 1e-9,
   "a batch row is priced at batch rates")
db.refresh(batch)
ok(batch.usage_recorded_at is not None, "the batch is marked recorded")
again = U.record_file(db, proj, batch, f1)
ok(again == 0 and db.query(M.AIUsage).filter_by(batch_id=batch.id).count() == 4,
   "copying the same file twice adds nothing")

# Sabotage 1: the repaint rule gone — b reads as a first painting.
src = inspect.getsource(U.record_file)
bad = src.replace("kind=_kind(db, int(batch.stage), poster_id, at)", 'kind="first"')
ok(bad != src, "sabotage 1 found its target")
ns = dict(U.__dict__); exec(bad, ns)
b2 = M.AIBatch(project_id=proj.id, stage=1, status="completed", output_file_id="f",
               created_at=sent, collected_at=sent)
db.add(b2); db.flush()
db.add(M.AIBatchItem(batch_id=b2.id, saved_poster_id=b.id, custom_id="s1-b")); db.commit()
ns["record_file"](db, proj, b2, f1)
ok(db.query(M.AIUsage).filter_by(batch_id=b2.id, custom_id="s1-b").one().kind == "first",
   "sabotage 1 goes red: without the rule a repaint reads as a first painting")
db.query(M.AIUsage).filter_by(batch_id=b2.id).delete(); db.delete(b2); db.commit()

# Sabotage 2: the "already have it" check gone — the unique key still refuses.
bad = src.replace("if not cid or cid in have or not usage:", "if not cid or not usage:")
ok(bad != src, "sabotage 2 found its target")
ns = dict(U.__dict__); exec(bad, ns)
try:
    ns["record_file"](db, proj, batch, f1)
    refused = False
except IntegrityError:
    db.rollback(); refused = True
ok(refused, "sabotage 2: without the check the database still refuses a second copy")
ok(db.query(M.AIUsage).filter_by(batch_id=batch.id).count() == 4, "…and nothing doubled")

# ── collect() copies the counts from the file it downloads ───────────────
c_poster = poster()
cb = M.AIBatch(project_id=proj.id, stage=1, model="m", status="completed",
               output_file_id="file-out-2", created_at=sent)
db.add(cb); db.flush()
db.add(M.AIBatchItem(batch_id=cb.id, saved_poster_id=c_poster.id, custom_id="c-1")); db.commit()
_real_download = G.file_download


def fake_download(api_key, file_id, dest):
    if file_id == "gone":
        raise G.PermanentFailure("404 no such file")
    if file_id == "flaky":
        raise G.TransientFailure("timeout")
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_text(answer("c-1", epoch("2026-11-04T05:00:00")) if file_id == "file-out-2"
                          else answer("w-1", epoch("2026-11-01T10:00:00")), encoding="utf-8")


G.file_download = fake_download
AB.collect(db, proj, cb, api_key="k")
db.refresh(cb)
ok(db.query(M.AIUsage).filter_by(batch_id=cb.id, custom_id="c-1").count() == 1,
   "collect() records the answer it downloaded (the picture itself was dropped)")
ok(cb.usage_recorded_at is not None and cb.collected_at is not None,
   "collect() marks the batch recorded and collected")

# ── The sweep ───────────────────────────────────────────────────────────
G.api_key_for = lambda db, project: "k"
old = datetime(2026, 10, 29, 9, 0)
empty = M.AIBatch(project_id=proj.id, stage=1, status="failed", created_at=old, collected_at=old)
withf = M.AIBatch(project_id=proj.id, stage=2, status="completed", output_file_id="file-w",
                  created_at=old, collected_at=old)
gone_b = M.AIBatch(project_id=proj.id, stage=2, status="completed", output_file_id="gone",
                   created_at=old, collected_at=old)
db.add_all([empty, withf, gone_b]); db.flush()
db.add(M.AIBatchItem(batch_id=withf.id, saved_poster_id=a.id, processed_id=1, custom_id="w-1"))
db.commit()
res = D.check_usage_recorded(db, D.Scope(db, None))
ok(res.severity == "warn" and res.count == 3, f"Diagnostics warns about 3 uncounted batches (got {res.count})")
U._last_sweep.clear()
ok(U.sweep(db, proj) is True, "the sweep settles one batch")
db.refresh(empty)
ok(empty.usage_recorded_at is not None and "nothing was billed" in (empty.usage_note or ""),
   "a batch with no answer file is closed as costing nothing")
ok(U.sweep(db, proj) is False, "the sweep waits two minutes before the next batch")
U._last_sweep.clear(); U.sweep(db, proj)
ok(db.query(M.AIUsage).filter_by(batch_id=withf.id).count() == 1, "the sweep copies an old batch's file")
r = db.query(M.AIUsage).filter_by(batch_id=withf.id).one()
ok(r.stage == 2 and r.kind == "first", "an old step 2 answer is step 2, first")
U._last_sweep.clear(); U.sweep(db, proj)
db.refresh(gone_b)
ok(gone_b.usage_recorded_at is not None and "could not be read" in (gone_b.usage_note or ""),
   "a file OpenAI refuses is noted and not tried for ever")
flaky = M.AIBatch(project_id=proj.id, stage=1, status="completed", output_file_id="flaky",
                  created_at=old, collected_at=old)
db.add(flaky); db.commit()
U._last_sweep.clear(); U.sweep(db, proj)
db.refresh(flaky)
ok(flaky.usage_recorded_at is None, "a download that times out is tried again later")
flaky.usage_recorded_at = datetime.utcnow(); db.commit()
res = D.check_usage_recorded(db, D.Scope(db, None))
ok(res.severity == "ok", "Diagnostics is green once every batch is counted")
G.file_download = _real_download

# ── Live calls record themselves ─────────────────────────────────────────
G._send_and_read = lambda headers, data, files, emit: G.Generation(
    image_bytes=b"x", input_tokens=2477, output_tokens=158, text_tokens=1031)
G.request_fields = lambda db, project, **kw: {"model": "gpt-live"}
G.step1_images = lambda db, project, **kw: [("a.png", b"x", "image/png")]
G.step2_images = lambda db, project, **kw: [("a.png", b"x", "image/png")]
live_p = poster(painted_at=datetime(2026, 11, 1))
G.generate(db, source=Path("x"), style=Path(""), project=proj, poster_id=live_p.id)
G.generate(db, source=Path("x"), style=Path(""), project=proj, poster_id=a.id, purpose="test")
G.generate_step2(db, painting_png=b"x", input_size=(1024, 1536), style=Path(""),
                 project=proj, sky=False, poster_id=live_p.id)
db.commit()
lives = db.query(M.AIUsage).filter_by(mode="live").order_by(M.AIUsage.id).all()
ok(len(lives) == 3, f"two live paintings and one live step 2 are recorded (got {len(lives)})")
ok(lives[0].kind == "repaint" and lives[0].model == "gpt-live", "a live repaint, with its model")
ok(lives[1].kind == "test", "a dashboard test is recorded as a test")
ok(lives[2].stage == 2 and lives[2].kind == "first", "a live step 2 is recorded as step 2")
ok(abs(lives[0].cost_usd - U.cost(1031, 2477, 158, "live")) < 1e-9, "a live row is priced double")
db.query(M.AIUsage).filter_by(mode="live").delete(); db.commit()

# ── The report ──────────────────────────────────────────────────────────
db.query(M.AIUsage).delete(); db.commit()
today = date(2026, 11, 10)
change = datetime(2026, 11, 8, 4, 52)
db.add(M.ActivityLog(user_id=boss.id, username="admin", action="pipeline_setting",
                     target_type="pipeline", created_at=change,
                     details=json.dumps({"key": "openai_prompt", "project": "travel"})))
db.add(M.ActivityLog(user_id=boss.id, username="admin", action="pipeline_settings",
                     target_type="pipeline", created_at=datetime(2026, 11, 9, 9, 0),
                     details=json.dumps({"keys": ["step2_prompt", "step2_quality"],
                                         "changed": ["step2_quality"], "project": "travel"})))
db.add(M.ActivityLog(user_id=boss.id, username="admin", action="pipeline_setting",
                     target_type="pipeline", created_at=datetime(2026, 11, 9, 10, 0),
                     details=json.dumps({"key": "openai_prompt", "project": "other"})))


def use(day, kind="first", stage=1, cost=1.0, text=7000, sent=None):
    at = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)
    db.add(M.AIUsage(project_id=proj.id, occurred_at=at, sent_at=sent or at, stage=stage,
                     mode="batch", kind=kind, text_in=text, cost_usd=cost))


def approve(day, k):
    for _ in range(k):
        sp = poster()
        db.add(M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="x",
                                filename="x", review_status="approved",
                                reviewed_at=datetime.combine(day, datetime.min.time())
                                + timedelta(hours=8)))


for i in range(14):                 # 4 Nov … 10 Nov is "last 7"; 28 Oct … 3 Nov "previous 7"
    d = today - timedelta(days=i)
    recent = i < 7
    use(d, cost=1.0, text=1030 if d >= change.date() else 7000)
    use(d, kind="repaint" if not recent or i % 2 else "first", cost=1.0)
    use(d, stage=2, cost=0.5)
    approve(d, 2)
# A batch sent just before the change and answered after it: the OLD prompt.
use(date(2026, 11, 8), cost=0.0, text=7000, sent=change - timedelta(minutes=5))
db.commit()

rep = U.report(db, proj, start=today - timedelta(days=6), end=today, today=today)
t = rep["tiles"]
ok(abs(t["last7"]["spend"] - 17.5) < 1e-6, f"last 7 days spend (got {t['last7']['spend']})")
ok(abs(t["prev7"]["spend"] - 17.5) < 1e-6, "previous 7 days spend")
ok(t["last7"]["per_approved"] == 1.25, f"cost per approved design (got {t['last7']['per_approved']})")
ok(abs(t["prev7"]["repaint_rate"] - 0.5) < 1e-6, "previous repaint rate: 1 of 2 paintings")
ok(abs(t["last7"]["repaint_rate"] - 3 / 15) < 1e-4,
   f"last 7 repaint rate counts every painting row (got {t['last7']['repaint_rate']})")
ok(abs(t["today"]["spend"] - 2.5) < 1e-6, "today's spend")
ok(abs(t["mtd"]["spend"] - 25.0) < 1e-6, f"month so far: 10 days (got {t['mtd']['spend']})")
ok(abs(t["forecast"] - (25.0 + 17.5 / 7 * 20)) < 0.01, "forecast carries on at the 7-day pace")
ok(len(rep["days"]) == 7 and rep["days"][-1]["day"] == "2026-11-10", "seven days, oldest first")
d8 = [x for x in rep["days"] if x["day"] == "2026-11-08"][0]
ok(d8["paintings"] == 3 and d8["approved"] == 2, "8 Nov: three paintings, two approved")
ok(d8["text_per_painting"] == round((1030 + 7000 + 7000) / 3), "prompt tokens per painting")
labels = sorted(m["label"] for m in rep["markers"])
ok(labels == ["painting prompt", "step 2 quality"],
   f"markers: the prompt change and the setting that really moved, not other projects (got {labels})")
ok([v["id"] for v in rep["prompt_versions"]] == ["0", "1"], "two prompt versions")
new = U.report(db, proj, start=today - timedelta(days=6), end=today, today=today, prompt="1")
old = U.report(db, proj, start=today - timedelta(days=6), end=today, today=today, prompt="0")
n8_new = [x for x in new["days"] if x["day"] == "2026-11-08"][0]["paintings"]
n8_old = [x for x in old["days"] if x["day"] == "2026-11-08"][0]["paintings"]
ok((n8_new, n8_old) == (2, 1), f"a request counts under the prompt it was SENT with (got {n8_new}, {n8_old})")
s2 = U.report(db, proj, start=today - timedelta(days=6), end=today, today=today, stage="2")
ok(abs(s2["tiles"]["last7"]["spend"] - 3.5) < 1e-6, "the step filter narrows every number")
rp = U.report(db, proj, start=today - timedelta(days=6), end=today, today=today, kind="repaint")
ok(abs(rp["tiles"]["last7"]["spend"] - 3.0) < 1e-6, "the kind filter narrows to repaints")

# ── A settings save logs which keys really changed ───────────────────────
class _Req:
    pass


P.set_setting(db, "step2_quality", "medium", project=proj); db.commit()
PA.api_set_settings(_Req(), payload={"settings": {"step2_quality": "high", "step2_size": P.get_setting(db, "step2_size", project=proj)},
                                     "scope": "project"}, admin=boss, db=db)
log = (db.query(M.ActivityLog).filter_by(action="pipeline_settings")
         .order_by(M.ActivityLog.id.desc()).first())
d = json.loads(log.details)
ok(d["changed"] == ["step2_quality"] and set(d["keys"]) == {"step2_quality", "step2_size"},
   f"the save logs every key sent, and only the changed one as changed (got {d.get('changed')})")

# ── The page's helpers, from the shipped file ────────────────────────────
js = (ROOT / "app/static/js/admin_usage.js").read_text(encoding="utf-8")


def lift(name):
    m = re.search(r"\n(  function " + name + r"\(.*?\n  \})\n", js, re.S)
    ok(m is not None, f"{name} found in the shipped file")
    return m.group(1)


script = f"""
{lift('pctChange')}
{lift('rollingAvg')}
{lift('fmtUsd')}
console.log([pctChange(8, 10), pctChange(0, 0), pctChange(5, 0), pctChange(null, 3),
  JSON.stringify(rollingAvg([1, 2, 3, null, 5], 3)), fmtUsd(0.0149), fmtUsd(12.453), fmtUsd(null)].join('|'));
"""
got = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                     encoding="utf-8")
ok(got.returncode == 0, "the lifted JavaScript runs" + (": " + got.stderr if got.returncode else ""))
want = "-0.2|0|||[1,1.5,2,2.5,4]|$0.015|$12.45|—"
ok(got.stdout.strip() == want, f"pctChange, rollingAvg, fmtUsd (got {got.stdout.strip()})")

print("ALL PASS")
