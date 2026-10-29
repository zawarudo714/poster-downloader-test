"""
Behaviour test: the Settings page refuses a key another key on the same
screen already uses (v287).

Approve Artwork reads its dashboard keys BEFORE its fixed letters and the
version digits, so a key set to the same character as another key takes
that key's press, and the other action can no longer be reached from the
keyboard. Nothing said so. Builds a throw-away SQLite database in a temp
folder, so it never touches poster.db. Run from the repo root:

    python tools/test_key_clash.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_key_clash_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from fastapi import HTTPException
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import pipeline_admin as PA

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
boss = M.User(username="boss", password_hash="x", role="admin")
db.add(boss); db.commit()
PA._project = lambda request, admin, db, explicit=None: proj

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
KEYS = ("review_key_rerun", "review_key_photoshop", "review_key_keep", "review_key_step2")


def save(**values):
    try:
        r = PA.api_set_settings(None, {"settings": values, "scope": "project"},
                                admin=boss, db=db)
        return json.loads(r.body).get("ok"), ""
    except HTTPException as e:
        return False, e.detail


def key(name):
    db.expire_all()
    return str(P.get_setting(db, name, project=proj))


# Positive control: the shipped defaults save as a group.
good, why = save(review_key_rerun="7", review_key_photoshop="8",
                 review_key_keep="9", review_key_step2="3")
ok(good, f"the default keys save ({why})")

good, why = save(review_key_rerun="9", review_key_photoshop="8",
                 review_key_keep="7", review_key_step2="3")
ok(good and key("review_key_rerun") == "9" and key("review_key_keep") == "7",
   f"two keys can be SWAPPED in one save ({why})")

good, why = save(review_key_rerun="9", review_key_photoshop="8",
                 review_key_keep="9", review_key_step2="3")
ok(not good and "both '9'" in why, f"two decision keys on one key are refused ({why})")
ok(key("review_key_keep") == "7", "and nothing of that save is kept")

good, why = save(review_key_step2="6")
ok(not good and "move right" in why, f"6, the number pad's move key, is refused ({why})")
good, why = save(review_key_photoshop="u")
ok(not good and "UNUSABLE" in why, f"a fixed letter is refused ({why})")
good, why = save(review_key_paste="7")
ok(not good and "Worker Images" in why, f"paste on the keep key is refused ({why})")
good, why = save(review_key_paste="3")
ok(good, "paste on 3 is fine: REDO STEP 2 is another screen's key")

# A clash already stored can be fixed by the save that fixes it.
P.set_setting(db, "review_key_step2", "8", project=proj, by="t"); db.commit()
good, why = save(review_key_rerun="9", review_key_photoshop="8",
                 review_key_keep="7", review_key_step2="3")
ok(good and key("review_key_step2") == "3", f"a stored clash can be fixed ({why})")

# RESET brings back a default that may clash with a key set since.
P.set_setting(db, "review_key_rerun", "3", project=proj, by="t")
P.set_setting(db, "review_key_step2", "5", project=proj, by="t"); db.commit()
try:
    PA.api_reset_setting(None, {"key": "review_key_step2", "scope": "project"},
                         admin=boss, db=db)
    ok(False, "a reset onto another key's value is refused")
except HTTPException as e:
    ok("both '3'" in e.detail, f"a reset onto another key's value is refused ({e.detail})")
ok(key("review_key_step2") == "5", "and the key is left as it was")

# Sabotage: the route without its clash check must let the clash through.
print("\n  sabotage — the save without the clash check")
_real = P.key_clash
P.key_clash = lambda *a, **k: None
try:
    good, why = save(review_key_keep="5", review_key_photoshop="5")
finally:
    P.key_clash = _real
ok(good, "without it two keys on one key ARE saved (so the check can fail)")

print("\nALL KEY CLASH CHECKS PASSED")
