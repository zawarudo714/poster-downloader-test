"""
Behaviour test: A REVIEW BATCH KEEPS ITS DESIGNS UNTIL IT IS SAVED (v291).

The owner opens a batch of N on Approve Artwork and works through it. A
repaint that comes back meanwhile is an OLDER design, so it sorts to the
front of "everything waiting". Before v291 any reload of that door re-picked
the first N, the repaint pushed a design off the end, and NEXT NOT MARKED
jumped to it (owner, 2026-11-01: "only when i save current batch then work
on next does it come there"). Checked here:

  * positive control: with no batch sent, the repaint IS in the first N;
  * with the open batch sent back, the same titles return, in order;
  * a title that left review elsewhere drops out of the batch;
  * when none of the batch is left, a fresh first N is picked (repaint in);
  * the date door and the reruns door ignore `batch`;
  * the 'modified' sort keeps the membership;
  * the screen remembers the batch and forgets it only on a save from the
    "everything waiting" door (the JavaScript, lifted from the shipped file);
  * a sabotage of the keep rule goes red.

Builds a throw-away SQLite database in a temp folder, so it never touches
poster.db. Run from the repo root:

    python tools/test_review_batch.py
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

_TMP = tempfile.mkdtemp(prefix="pd_review_batch_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import pipeline_admin as PA

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
P.set_setting(db, "review_batch_size", 3, project=proj)
db.commit()

w = M.User(username="humphrey", password_hash="x", role="worker")
boss = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, boss]); db.commit()

PA._project = lambda request, admin, db, explicit=None: proj

n = [0]


def design(saved, *, attempt=1, painted=None):
    """One title with one worker picture, painted and waiting for review."""
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id, title_folder_path=f"{n[0]}. Place {n[0]}",
                      original_save_date=saved)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=saved,
                       title_folder_path=t.title_folder_path, filename=f"{n[0]}.jpg",
                       source_url=f"u{n[0]}", pipeline_status="processed")
    db.add(sp); db.flush()
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id,
                          storage_path=f"s/{n[0]}.jpg", filename=f"{n[0]}.jpg",
                          is_current=1, review_status="pending", stage=2,
                          attempt=attempt,
                          created_at=painted or datetime(2026, 11, 1, 9, n[0]))
    db.add(pi); db.commit()
    return t, pi


def queue(batch="", *, status="pending", sort="saved", start="", end=""):
    res = PA.api_review_queue(None, start=start, end=end, status=status,
                              sort=sort, batch=batch, admin=boss, db=db)
    return json.loads(res.body)


def ids(d):
    return [t["title_id"] for t in d["titles"]]


a, pa = design(date(2026, 10, 20))
b, pb = design(date(2026, 10, 21))
c, pc = design(date(2026, 10, 22))
e, pe = design(date(2026, 10, 23))

first = queue()
ok(ids(first) == [a.id, b.id, c.id], "the first batch is the first 3 by save date")
ok(first["batch_title_ids"] == [a.id, b.id, c.id], "the reply names the batch to send back")
ok(first["total_waiting"] == 4, "all 4 are counted as waiting")
open_batch = ",".join(str(x) for x in first["batch_title_ids"])

# A repaint comes back while the batch is open: an OLDER design, fresh paint.
d, pd = design(date(2026, 10, 1), attempt=2, painted=datetime(2026, 11, 1, 12, 0))

ok(d.id in ids(queue()), "positive control: with no batch sent, the repaint IS in the first 3")
kept = queue(open_batch)
ok(ids(kept) == [a.id, b.id, c.id], "with the open batch sent back, the same 3 return in order")
ok(d.id not in ids(kept), "the repaint does not enter the open batch")
ok(kept["total_waiting"] == 5, "the repaint is still counted as waiting")
ok(ids(queue(open_batch, sort="modified")) == [c.id, b.id, a.id],
   "the 'modified' sort reorders the batch but keeps its members")

# One of the batch is decided elsewhere (another device, a rerun).
pb.review_status = "approved"; db.commit()
ok(ids(queue(open_batch)) == [a.id, c.id], "a title decided elsewhere drops out of the batch")

# The rest of the batch is decided: the next batch is a fresh first 3.
pa.review_status = "approved"; pc.review_status = "approved"; db.commit()
fresh = queue(open_batch)
ok(ids(fresh) == [d.id, e.id], "with none of the batch left, a fresh batch is picked, repaint in")
ok(fresh["batch_title_ids"] == [d.id, e.id], "the fresh batch is the one to send back next")

# Doors that do not batch ignore what the screen sends.
pa.review_status = "pending"; db.commit()
ok(set(ids(queue(str(a.id), start="2026-10-01", end="2026-10-31"))) == {a.id, d.id, e.id},
   "the date door shows the whole range whatever batch is sent")
dated = queue(str(a.id), start="2026-10-01", end="2026-10-31")
ok(dated["batch_title_ids"] == [], "the date door names no batch")
ok(ids(queue(str(a.id), status="rerun")) == [d.id], "the reruns door ignores the batch")
ok(set(ids(queue("x,,-3"))) <= {a.id, d.id, e.id} and len(ids(queue("x,,-3"))) == 3,
   "a garbled batch falls back to the first 3, never an error")

# ── SABOTAGE: drop the keep rule, and the repaint must jump in again ────────
pa.review_status = "pending"; pb.review_status = "pending"; pc.review_status = "pending"
db.commit()
src = inspect.getsource(PA.api_review_queue)
needle = "if size > 0 and keep else []"
ok(src.count(needle) == 1, "sabotage anchor found exactly once")
broken = src.replace(needle, "if False else []")
ns = dict(PA.__dict__)
exec(compile("\n".join(l for l in broken.splitlines() if not l.startswith("@router")),
             "sabotage", "exec"), ns)
res = ns["api_review_queue"](None, start="", end="", status="pending", sort="saved",
                             batch=open_batch, admin=boss, db=db)
ok(d.id in [t["title_id"] for t in json.loads(res.body)["titles"]],
   "sabotage goes red: without the keep rule the repaint enters the open batch")

# ── THE SCREEN: remember the batch, forget it only on a save from that door ─
js = (ROOT / "app/static/js/admin_review_images.js").read_text(encoding="utf-8")


def lift(name):
    m = re.search(r"\n(  function " + name + r"\(.*?\n  \})\n", js, re.S)
    ok(m is not None, f"{name} found in the shipped file")
    return m.group(1)


key = re.search(r"const BATCH_KEY = '([^']+)';", js)
ok(key is not None, "BATCH_KEY found in the shipped file")
open_range = js[js.index("async function openRange"):]
open_range = open_range[:open_range.index("\n  }\n")]
ok("qs.set('batch', readBatch().join(','))" in open_range,
   "openRange sends the open batch back")
ok("writeBatch(d.batch_title_ids || [])" in open_range,
   "openRange remembers the batch the server names")
commit_src = js[js.index("async function commit"):]
commit_src = commit_src[:commit_src.index("\n  }\n")]
ok(re.search(r"mode === 'pending' && lastDoor && !lastDoor\.start && !lastDoor\.end\) \{\s*"
             r"writeBatch\(\[\]\);", commit_src) is not None,
   "a save from the 'everything waiting' door forgets the batch")
script = f"""
const store = {{}};
global.localStorage = {{
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => {{ store[k] = String(v); }},
  removeItem: (k) => {{ delete store[k]; }},
}};
const BATCH_KEY = '{key.group(1)}';
{lift('readBatch')}
{lift('writeBatch')}
const out = [];
out.push(JSON.stringify(readBatch()));
writeBatch([7, 8, 9]); out.push(JSON.stringify(readBatch()));
store[BATCH_KEY] = 'not json'; out.push(JSON.stringify(readBatch()));
store[BATCH_KEY] = '{{"a":1}}'; out.push(JSON.stringify(readBatch()));
writeBatch([7]); writeBatch([]); out.push(String(BATCH_KEY in store));
console.log(out.join('|'));
"""
got = subprocess.run(["node", "-e", script], capture_output=True, text=True)
ok(got.returncode == 0, "the lifted JavaScript runs" + (": " + got.stderr if got.returncode else ""))
ok(got.stdout.strip() == "[]|[7,8,9]|[]|[]|false",
   f"readBatch/writeBatch round-trip and survive garbage (got {got.stdout.strip()})")

print("ALL PASS")
