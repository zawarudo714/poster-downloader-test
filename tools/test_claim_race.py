"""
Behaviour test: two workers pressing GET (or claiming from the list) at the
same moment never both get the same title (v280). The claim used to read
the free titles, then write the claim with no check on the row itself, so
the later writer silently took titles the earlier one had just been told
were theirs. Builds a throw-away SQLite database in a temp folder, so it
never touches poster.db. Run from the repo root:

    python tools/test_claim_race.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_claim_race_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime as _real_datetime
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P
from app.routes import worker as W

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj_id = db.query(M.Project).filter_by(slug="travel").one().id
for name in ("alice", "bob"):
    db.add(M.User(username=name, password_hash="x", role="worker"))
db.commit()
db.close()

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))
W._worker_project = lambda request, db, user: db.get(M.Project, proj_id)


def fresh_titles(n):
    s = SessionLocal()
    s.query(M.MasterTitle).delete()
    ids = []
    for i in range(1, n + 1):
        t = M.MasterTitle(title=f"Place {i}", external_id=i, status="pending", project_id=proj_id)
        s.add(t); s.flush(); ids.append(t.id)
    s.commit(); s.close()
    return ids


def race(first):
    """Runs `first(alice)`; between its read and its write, bob presses GET."""
    out = {}

    class _Clock:
        fired = False

        @classmethod
        def utcnow(cls):
            if not cls.fired:
                cls.fired = True
                s = SessionLocal()
                bob = s.query(M.User).filter_by(username="bob").one()
                out["bob"] = json.loads(W.pull_next(request=None, n=3, user=bob, db=s).body)["ids"]
                s.close()
            return _real_datetime.utcnow()

    W.datetime = _Clock
    try:
        s = SessionLocal()
        alice = s.query(M.User).filter_by(username="alice").one()
        out["alice"] = json.loads(first(alice, s).body)["ids"]
        s.close()
    finally:
        W.datetime = _real_datetime
    s = SessionLocal()
    owner = {t.id: t.claimed_by_name for t in s.query(M.MasterTitle)}
    s.close()
    return out, owner


for label, first in (
    ("GET", lambda u, s: W.pull_next(request=None, n=3, user=u, db=s)),
    ("claim from the list", lambda u, s: W.select_titles(request=None, ids=",".join(map(str, ids)), user=u, db=s)),
):
    ids = fresh_titles(3)
    out, owner = race(first)
    both = set(out["alice"]) & set(out["bob"])
    ok(not both, f"{label}: no title was handed to both workers ({out})")
    ok(all(owner[i] == "alice" for i in out["alice"]) and all(owner[i] == "bob" for i in out["bob"]),
       f"{label}: every title is held by the worker who was told it is theirs ({owner})")
    ok(len(out["alice"]) + len(out["bob"]) == 3, f"{label}: all three titles were handed out once")

print("ALL CLAIM RACE CHECKS PASSED")
