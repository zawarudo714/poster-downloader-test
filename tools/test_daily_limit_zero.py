"""
Behaviour test: an account's daily upload limit of 0 is saved as 0 (v281).
The server turned it into 100, so an owner holding uploads back to build up
a stock of pictures had an account still set to upload 100 a day. Builds a
throw-away SQLite database in a temp folder, so it never touches poster.db.
Run from the repo root:

    python tools/test_daily_limit_zero.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_daily_zero_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import pipeline_admin as PA

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
admin = M.User(username="boss", password_hash="x", role="admin")
db.add(admin); db.commit()
PA._project = lambda request, admin, db, *a, **k: proj

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
limit = lambda: db.query(M.UploadAccount).one().daily_limit

PA.api_create_account(request=None, payload={"name": "Shop", "email": "a@b.c", "password": "pw",
                                             "target_site": "fineartamerica", "daily_limit": 0},
                      admin=admin, db=db)
db.expire_all()
ok(limit() == 0, f"a new account set to 0 a day is saved as 0 (saved {limit()})")
acc_id = db.query(M.UploadAccount).one().id

PA.api_update_account(acc_id, payload={"daily_limit": 40}, admin=admin, db=db)
db.expire_all()
ok(limit() == 40, "a limit of 40 is saved as 40")

PA.api_update_account(acc_id, payload={"daily_limit": 0}, admin=admin, db=db)
db.expire_all()
ok(limit() == 0, f"changing it to 0 saves 0 (saved {limit()})")
ok(P.account_quota(db, db.query(M.UploadAccount).one())["remaining"] == 0,
   "and the uploader is told it may upload nothing today")

PA.api_update_account(acc_id, payload={"daily_limit": ""}, admin=admin, db=db)
db.expire_all()
ok(limit() == 100, "only a blank box falls back to 100")
print("ALL DAILY LIMIT CHECKS PASSED")
