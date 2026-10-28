"""
Behaviour test: taking paid-ahead money back settles only what the payment
covers (v272). Builds a throw-away SQLite database in a temp folder, so it
never touches poster.db. Run from the repo root:

    python tools/test_advance_deduction.py

Before v272, a debt bigger than the week's pay stopped the amount at 0 and
recorded EVERY owed picture as repaid, so the rest was silently forgiven.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_deduct_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from datetime import datetime, date
from decimal import Decimal
from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, payments as PAY
from app.routes import admin as ADM

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for c in P.sync_projects(db): pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()

w = M.User(username="humphrey", password_hash="x", role="worker")
a = M.User(username="admin", password_hash="x", role="admin")
db.add_all([w, a]); db.flush()
NOW = datetime.utcnow()
n = [0]
def title():
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete", project_id=proj.id)
    db.add(t); db.flush(); return t
def pic(day):
    sp = M.SavedPoster(master_title_id=title().id, user_id=w.id, username=w.username,
                       project_folder="travel", original_save_date=day, title_folder_path="x",
                       filename=f"{n[0]}.jpg", source_url="u")
    db.add(sp); db.flush(); return sp

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

# Five pictures paid ahead at 7 KES, then all removed without pay: 35 KES owed.
old = [pic(date(2026, 10, 10)) for _ in range(5)]
db.add(M.PaymentRun(worker_id=w.id, worker_username="humphrey", period_start=date(2026, 10, 10),
                    period_end=date(2026, 10, 10), poster_count=5, rate_kes="7", amount_kes="35",
                    poster_ids_json=json.dumps([p.id for p in old]), is_advance=1, created_by="admin"))
for p in old:
    p.deleted_at = NOW
db.commit()
owed = PAY.advance_shortfall(db, w.id)
ok(owed["count"] == 5 and Decimal(owed["kes"]) == 35, f"35 KES owed back ({owed['kes']})")

# This week: two new pictures, paid ahead. Worth less than the debt.
new = [pic(date(2026, 10, 27)) for _ in range(2)]
db.commit()
gross = PAY.price_run(db, PAY.advance_poster_ids(db, worker_id=w.id, start=date(2026, 10, 27),
                                                 end=date(2026, 10, 27)))["total"]
fits = int(Decimal(gross) // 7)
ok(0 < fits < 5, f"this week is worth {gross} KES, room for {fits} of the 5 owed pictures")

def mark_paid(mode, ids):
    return ADM.payments_mark_paid(
        worker_id=w.id, start="2026-10-27", end="2026-10-27", amount_kes="0",
        reference="", note="", push_to_worker=0, include_back_pay_dates="",
        pay_ahead=1, settle_ids=",".join(str(i) for i in ids), settle_mode=mode,
        admin=a, db=db)

# The page sends every id (as the old page did) — the server still caps it.
mark_paid("deducted", [p.id for p in old])
run = db.query(M.PaymentRun).order_by(M.PaymentRun.id.desc()).first()
settled = json.loads(run.advance_settled_json)
ok(len(settled["deducted"]) == fits and Decimal(settled["kes"]) == 7 * fits,
   f"only what fits is settled: {settled}")
left = PAY.advance_shortfall(db, w.id)
ok(left["count"] == 5 - fits and Decimal(left["kes"]) == 7 * (5 - fits),
   f"the rest is still owed next time ({left['count']} pictures, {left['kes']} KES)")

# A waive is his decision: it settles everything shown, whatever the pay.
more = [pic(date(2026, 10, 28))]
db.commit()
ADM.payments_mark_paid(
    worker_id=w.id, start="2026-10-28", end="2026-10-28", amount_kes="0",
    reference="", note="", push_to_worker=0, include_back_pay_dates="",
    pay_ahead=1, settle_ids=",".join(str(i["poster_id"]) for i in left["items"]),
    settle_mode="waived", admin=a, db=db)
ok(PAY.advance_shortfall(db, w.id)["count"] == 0, "waiving the rest clears it")

# Diagnostics: quiet on these runs, warns on one written the old way.
from app import diagnostics as D
check = lambda: D.check_deductions_fit_their_payment(db, D.Scope(db, None))
ok(check().severity == "ok", f"Diagnostics quiet: {check().title}")
db.add(M.PaymentRun(worker_id=w.id, worker_username="humphrey", period_start=date(2026, 10, 29),
                    period_end=date(2026, 10, 29), poster_count=1, rate_kes="5", amount_kes="0",
                    poster_ids_json="[]", is_advance=1, created_by="admin",
                    advance_settled_json=json.dumps({"deducted": [1, 2], "kes": "14"})))
db.commit()
ok(check().severity == "warn", f"Diagnostics warns on an over-settled run: {check().title}")

print("ALL ADVANCE DEDUCTION CHECKS PASSED")
