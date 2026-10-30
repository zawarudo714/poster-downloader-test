"""
Behaviour test: the SKY GRADIENT tick on REDO STEP 2 (v288). Builds a
throw-away SQLite database and storage folder, and replaces the OpenAI calls
with fakes that record what was sent — so it never touches poster.db and
never spends money.

    python tools/test_step2_sky.py

What it proves, from the SAVE press to the request OpenAI would receive:
  · REDO STEP 2 with the tick marks the painting; a redo WITHOUT it clears
    the mark, so one tick never follows the painting into the next redo.
  · A batch line and a live call both carry the sky prompt for a marked
    painting and the normal prompt for any other.
  · The step 2 picture that comes back keeps the mark (the "· sky" label).
  · A blank sky prompt is refused at SAVE before anything is saved, and,
    if it is blanked later, fails only that picture — never the whole send.

WHAT THE FAKES SIMPLIFY (rule 5d): no network; OpenAI's upload/create and
the live edit call are replaced; storage is the local fallback folder.
"""
import io
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_sky_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from fastapi import HTTPException

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import gpt_images as G, gpt_worker as GW, ai_batches as AB
from app.routes import pipeline_admin as PA

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))


def png(size=(1024, 1536)) -> bytes:
    out = io.BytesIO(); Image.new("RGBA", size, (40, 90, 160, 120)).save(out, "PNG")
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

SKY = str(P.get_setting(db, "step2_prompt_sky", project=proj))
NORMAL = str(P.get_setting(db, "step2_prompt", project=proj))
ok("The sky needs a strong, vivid, gradient color" in SKY,
   "travel: the owner's sky gradient prompt is in place")
ok(SKY.strip() != NORMAL.strip(), "and it is not the same as the normal step 2 prompt")

w = M.User(username="humphrey", password_hash="x", role="worker")
a = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, a]); db.flush()

n = [0]
def picture():
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete",
                      project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=date(2026, 10, 20),
                       title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"{n[0]}.jpg",
                       source_url="u", pipeline_status="processed")
    db.add(sp); db.flush()
    rel, name = P.storage_path_for(db, t, sp, project=proj, attempt=1)
    master = rel.rsplit(".", 1)[0] + "_master.png"
    from app.storage_remote import write_bytes
    write_bytes(db, master, png(), project=proj)
    pi = M.ProcessedImage(saved_poster_id=sp.id, project_id=proj.id, storage_path="",
                          filename=name, is_current=1, review_status="pending",
                          attempt=1, master_path=master, preview_path="p.jpg")
    db.add(pi); db.flush()
    return t, sp, pi

_real_project = PA._project
def decide(*decisions):
    PA._project = lambda request, admin, db, explicit=None: proj
    try:
        return json.loads(PA.api_review_decide(None, {"decisions": list(decisions)},
                                               admin=a, db=db).body)
    finally:
        PA._project = _real_project

# ── THE PROMPT CHOICE ──────────────────────────────────────────────────────
f_sky = G.request_fields(db, proj, stage=2, location="Dubai", sky=True)
f_plain = G.request_fields(db, proj, stage=2, location="Dubai", sky=False)
ok("gradient color" in f_sky["prompt"] and "gradient color" not in f_plain["prompt"],
   "ticked: the sky prompt is used; not ticked: the normal one")
ok({k: v for k, v in f_sky.items() if k != "prompt"}
   == {k: v for k, v in f_plain.items() if k != "prompt"},
   "everything else in the request (model, size, quality) is the same")

# ── SAVE: the tick reaches the painting, and does not linger ───────────────
t1, p1, s1 = picture()
t2, p2, s2 = picture()
db.commit()
r = decide({"processed_id": s1.id, "action": "step2", "sky": True},
           {"processed_id": s2.id, "action": "step2"})
db.refresh(s1); db.refresh(s2)
ok(r["step2"] == 2 and s1.review_status == "step2" and s2.review_status == "step2",
   "REDO STEP 2 sends both back to step 2 (positive control)")
ok(s1.step2_sky == 1 and s2.step2_sky == 0,
   f"only the ticked one is marked for the sky prompt ({s1.step2_sky}, {s2.step2_sky})")

# ── A BATCH SEND carries the right prompt per picture ──────────────────────
sentfiles = []
def fake_upload(key, path):
    sentfiles.append(Path(path).read_text(encoding="utf-8")); return f"file-{len(sentfiles)}"
def fake_create(key, fid):
    return {"id": f"batch_{len(sentfiles)}", "status": "validating",
            "request_counts": {"total": 0, "completed": 0, "failed": 0},
            "expires_at": int((datetime.utcnow() + timedelta(hours=24)).timestamp())}
G.batch_upload_file, G.batch_create = fake_upload, fake_create
ok(AB.send_due(db, proj), "a batch send goes out")
lines = [json.loads(l) for l in sentfiles[0].splitlines()]
by_poster = {int(l["custom_id"].split("-p")[1].split("-")[0]): l["body"]["prompt"] for l in lines}
ok(len(lines) == 2, f"both pictures are in the step 2 batch ({len(lines)})")
ok("gradient color" in by_poster[p1.id], "the ticked picture's line carries the sky prompt")
ok("gradient color" not in by_poster[p2.id], "the other picture's line carries the normal prompt")

# ── THE ANSWER IS FILED with the mark, so the version reads "· sky" ─────────
s1.step2_attempts = 0
GW.file_step2(db, s1, p1, t1, proj, G.Generation(image_bytes=png()))
GW.file_step2(db, s2, p2, t2, proj, G.Generation(image_bytes=png()))
v1 = db.query(M.ProcessedImage).filter_by(saved_poster_id=p1.id, stage=2).one()
v2 = db.query(M.ProcessedImage).filter_by(saved_poster_id=p2.id, stage=2).one()
ok(v1.step2_sky == 1 and v2.step2_sky == 0,
   f"the step 2 picture keeps the mark ({v1.step2_sky}, {v2.step2_sky})")

# ── A REDO WITHOUT THE TICK clears it — from the step 2 picture, as on screen
r = decide({"processed_id": v1.id, "action": "step2"})
db.refresh(s1)
ok(r["step2"] == 1 and s1.review_status == "step2" and s1.step2_sky == 0,
   f"the next REDO STEP 2 without the tick goes back to the normal prompt ({s1.step2_sky})")

# ── THE LIVE CALL (batch switched off) carries the mark too ────────────────
asked = []
def fake_generate_step2(db, **k):
    asked.append(k["sky"]); return G.Generation(image_bytes=png())
_real_g2 = G.generate_step2
G.generate_step2 = fake_generate_step2
try:
    r = decide({"processed_id": v2.id, "action": "step2", "sky": True})
    db.refresh(s2)
    GW.process_step2_one(db, s2, p2, t2, proj)
    ok(asked == [True], f"a live step 2 is asked for the sky prompt ({asked})")
    v2b = (db.query(M.ProcessedImage)
             .filter_by(saved_poster_id=p2.id, stage=2, is_current=1).one())
    ok(v2b.step2_sky == 1, "and its new picture carries the mark")
finally:
    G.generate_step2 = _real_g2

# ── A BLANK SKY PROMPT ─────────────────────────────────────────────────────
P.set_setting(db, "step2_prompt_sky", "", project=proj, by="t"); db.commit()
try:
    G.request_fields(db, proj, stage=2, location="Moscow", sky=True)
    ok(False, "a blank sky prompt is refused, never swapped for the normal one")
except G.PermanentFailure as e:
    ok(e.kind == "bad_request", "a blank sky prompt is refused, never swapped for the normal one")
    ok("prompt is empty" not in str(e),
       "and its message cannot be read as 'stop every send' by the batch sender")
ok("prompt" in G.request_fields(db, proj, stage=2, location="Moscow", sky=False),
   "an unticked picture is not affected by the blank sky prompt")

t3, p3, s3 = picture()
t4, p4, s4 = picture()
db.commit()
try:
    decide({"processed_id": s4.id, "action": "unusable", "reason": "blurry"},
           {"processed_id": s3.id, "action": "step2", "sky": True})
    ok(False, "SAVE refuses SKY GRADIENT while the sky prompt is blank")
except HTTPException as e:
    ok(e.status_code == 409, "SAVE refuses SKY GRADIENT while the sky prompt is blank")
db.rollback(); db.refresh(s3); db.refresh(s4)
ok(s3.review_status == "pending" and s4.review_status == "pending",
   "and NOTHING in that SAVE was kept, not even the decision before it")
r = decide({"processed_id": s4.id, "action": "unusable", "reason": "blurry"})
db.refresh(s4)
ok(r["unusable"] == 1 and s4.review_status == "unusable",
   "the same decision without the tick goes through (positive control)")

# ── THE PAGE: the tick rides on the REDO STEP 2 mark ───────────────────────
# Lifts the page's own functions and runs them in node with a stub screen.
import re, shutil, subprocess
js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js"
      / "admin_review_images.js").read_text(encoding="utf-8")
def lift(name):
    m = re.search(r"^  function " + name + r"\(.*?^  }\n", js, re.S | re.M)
    ok(m is not None, f"the page still has {name}()")
    return m.group(0)
if not shutil.which("node"):
    print("SKIP node is not installed: the page's tick was not run")
else:
    script = "\n".join([
        "const decisions = new Map();", "let skyWanted = new Set();",
        "const render = () => {};", "let step2On = true;",
        "const keepRefused = () => false;",
        lift("markFor"), lift("decide"), lift("skyTicked"), lift("toggleSky"),
        "const out = [];",
        "const sky = (id) => { const d = decisions.get(id); return d ? !!d.sky : null; };",
        "toggleSky(1); decide(1, 'step2'); out.push(sky(1));",      # tick, then redo
        "decide(2, 'step2'); toggleSky(2); out.push(sky(2));",      # redo, then tick
        "toggleSky(2); out.push(sky(2));",                          # untick
        "decide(3, 'step2'); out.push(sky(3));",                    # never ticked
        "toggleSky(4); decide(4, 'unusable'); out.push(sky(4));",   # tick, other mark
        "out.push(skyTicked(1), skyTicked(2));",
        "console.log(JSON.stringify(out));",
    ])
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    ok(res.returncode == 0, "the page's tick functions run" + (f": {res.stderr}" if res.returncode else ""))
    out = json.loads(res.stdout)
    ok(out[0] is True, "page: tick then REDO STEP 2 carries the tick")
    ok(out[1] is True, "page: REDO STEP 2 then tick carries the tick")
    ok(out[2] is False, "page: unticking takes it off the mark")
    ok(out[3] is False, "page: a REDO STEP 2 never ticked carries no tick")
    ok(out[4] is False, "page: the tick rides only on REDO STEP 2, not on other marks")
    ok(out[5] is True and out[6] is False, "page: the button shows what the mark will send")
    ok("sky: !!(d && d.action === 'step2' && fate !== 'left' && d.sky)" in js,
       "page: SAVE sends the tick with each REDO STEP 2")

print("\nALL SKY GRADIENT CHECKS PASSED")
