"""
Behaviour test: a missing style picture holds painting back, and fails
no photo (v279). It used to mark every photo of a send as permanently
failed (attempts 999), each as that photo's own fault. Builds a
throw-away SQLite database in a temp folder, so it never touches
poster.db, and never talks to OpenAI (the send is replaced). Run from the
repo root:

    python tools/test_style_missing.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_style_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import date, datetime
from PIL import Image
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app import gpt_images as G, gpt_worker as GW, ai_batches as AB, diagnostics as D

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
P.set_setting(db, "openai_api_key", P.encrypt_secret("sk-test"), project=proj, by="t")
P.set_setting(db, "step1_enabled", True, project=proj, by="t")
P.set_setting(db, "openai_use_style_image", True, project=proj, by="t")
P.set_setting(db, "openai_style_image", "styles/travel.png", project=proj, by="t")
P.set_setting(db, "step2_send_style", False, project=proj, by="t")
P.set_setting(db, "batch_gather_min", 0, project=proj, by="t")
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add(w); db.flush()
for n in (1, 2):
    t = M.MasterTitle(title=f"Place {n}", external_id=n, status="complete", project_id=proj.id)
    db.add(t); db.flush()
    sp = M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=date(2026, 10, 20),
                       title_folder_path=f"{n}. Place {n}", filename=f"{n}.jpg",
                       source_url="u", pipeline_status="greenlit", reviewed_at=datetime.utcnow())
    db.add(sp); db.flush()
    from app.utils import saved_poster_path
    f = saved_poster_path(sp); f.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 1200), (200, 120, 60)).save(f, "JPEG")
db.commit()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
check = lambda: D.check_style_picture_is_there(db, D.Scope(db, None))
sent = []
AB._send_one_file = lambda db, project, **k: sent.append(k["items"]) or len(k["items"])

ok(len(GW.paint_queue(db, proj).all()) == 2, "two photos wait to be painted")
ok(G.settings_problem(db, proj, stage=1) is not None, "the missing style picture is seen")
AB.send_due(db, proj)
db.expire_all()
photos = db.query(M.SavedPoster).all()
ok(not sent, "nothing was sent to OpenAI")
ok(all(p.pipeline_status == "greenlit" and not p.process_attempts for p in photos),
   f"no photo was failed ({[(p.pipeline_status, p.process_attempts) for p in photos]})")
ok("style" in (AB.last_error(proj) or {}).get("text", ""), "the reason is on the AI panel")
ok(check().severity == "error", f"Diagnostics says so: {check().title}")

# The one-at-a-time loop (batch off) claims nothing either.
P.set_setting(db, "openai_batch_enabled", False, project=proj, by="t"); db.commit()
claims = []
_real_claim = GW._claim_next
GW._claim_next = lambda db, project: claims.append(project.slug) or (None, None)
ok(proj.processor == "gpt" and proj.is_active == 1, "travel is painted on this server")
GW._cycle()
ok(not claims, "the one-at-a-time loop claims no photo while the picture is missing")
GW._claim_next = _real_claim
P.set_setting(db, "openai_batch_enabled", True, project=proj, by="t"); db.commit()

style = Path(os.environ["WORKSPACE_DIR"]) / "styles" / "travel.png"
style.parent.mkdir(parents=True, exist_ok=True)
Image.new("RGB", (64, 64), (1, 2, 3)).save(style, "PNG")
ok(check().severity == "ok", f"Diagnostics quiet once it is back: {check().title}")
ok(G.settings_problem(db, proj, stage=1) is None, "nothing holds painting back any more")
AB.send_due(db, proj)
ok(sum(len(x) for x in sent) == 2, f"both photos go in the next send ({sent and len(sent[0])})")
print("ALL STYLE CHECKS PASSED")
