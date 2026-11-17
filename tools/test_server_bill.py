"""
Behaviour test: THE USAGE TAB'S SERVER BILLS, PAY TABLE AND REMINDER (v298).

The owner (2026-11-17) asked for three small things, none in the charts:

  * the Hetzner and Contabo prices shown small on Usage, editable;
  * a reminder from the 24th that only ticking Paid removes, and that comes
    back the next month;
  * each worker's pictures and pay per month in KES.

Checked here:
  * the due month: before the 24th it is last month, from the 24th this
    month, across a year end;
  * the reminder shows until Paid is ticked, stays hidden the rest of the
    month, and comes back on the next 24th;
  * the price edit refuses a key that is not a server and a non-number, and
    logs a change;
  * the pay table counts only payable pictures (an admin-added one and a
    deleted one are left out), only this project's, by month; pay =
    pictures x rate; Paid sums the payment runs ending that month;
  * the date inputs keep color-scheme so their text stays readable;
  * sabotage: a wrong due day and counting admin-added pictures go red.

Builds a throw-away SQLite database in a temp folder. Run from the repo root:

    python tools/test_server_bill.py
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_server_bill_test_")
os.environ["WORKSPACE_DIR"] = os.path.join(_TMP, "ws")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db").replace("\\", "/")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException

from app.db import init_db, SessionLocal
from app import models as M, pipeline as P, payments as PAY
from app import timeutil as TU
from app.routes import usage_admin as UA, admin as A

ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or (cond or sys.exit(1))

init_db()
from app.schema_migrations import migrate_schema; migrate_schema()
db = SessionLocal()
for _c in P.sync_projects(db):
    pass
db.commit()
proj = db.query(M.Project).filter_by(slug="travel").one()
other = M.Project(slug="elsewhere", name="Elsewhere")  # a second project
db.add(other); db.flush()
P.set_setting(db, "pay_rate_kes", "7", project=proj, by="t")
admin = M.User(username="owner", password_hash="x", role="admin")
w = M.User(username="humphrey", password_hash="x", role="worker")
db.add_all([admin, w]); db.commit()
A.current_project = lambda request, admin, db: proj


def body(resp):
    return json.loads(resp.body)


class Req:
    def __init__(self, data):
        self._d = data

    async def json(self):
        return self._d


# 1. The due month
ok(UA.bill_due_month(date(2026, 11, 23)) == "2026-10", "23 Nov: the October bill is the one due")
ok(UA.bill_due_month(date(2026, 11, 24)) == "2026-11", "24 Nov: the November bill is due")
ok(UA.bill_due_month(date(2027, 1, 5)) == "2026-12", "5 Jan: December's bill, across the year end")
ok(UA.bill_is_due("2026-10", date(2026, 11, 24)), "24 Nov with October paid: reminder shows")
ok(not UA.bill_is_due("2026-11", date(2026, 11, 30)), "November ticked paid: hidden for the rest of the month")
ok(not UA.bill_is_due("2026-11", date(2026, 12, 23)), "23 Dec: still hidden")
ok(UA.bill_is_due("2026-11", date(2026, 12, 24)), "24 Dec: back again")
ok(UA.bill_is_due("", date(2026, 11, 2)), "never ticked: shows")
TU.local_today = lambda: date(2026, 11, 17)
ok(not body(UA.api_server_bill(admin=admin, db=db))["due"], "nothing ticked yet, 17 Nov: October counts as paid, no pill")

# 2. Ticking Paid through the routes
TU.local_today = lambda: date(2026, 11, 24)
ok(body(UA.api_server_bill(admin=admin, db=db))["due"], "the bill endpoint says due on the 24th")
UA.api_server_bill_paid(admin=admin, db=db)
ok(not body(UA.api_server_bill(admin=admin, db=db))["due"], "after Paid the endpoint says not due")
ok(PAY.get_setting(db, UA.BILL_PAID_KEY, "") == "2026-11", "Paid stored the November bill")
ok(db.query(M.ActivityLog).filter_by(action="server_bill_paid").count() == 1, "ticking Paid is logged")
TU.local_today = lambda: date(2026, 12, 24)
ok(body(UA.api_server_bill(admin=admin, db=db))["due"], "the next 24th it is due again")

# 3. Editing a price
for bad, why in (({"key": "openai_api_key", "price": "1"}, "a key that is not a server"),
                 ({"key": "server_cost_hetzner_usd", "price": "abc"}, "a non-number")):
    try:
        asyncio.run(UA.api_server_prices(Req(bad), admin=admin, db=db))
        refused = False
    except HTTPException:
        refused = True
    ok(refused, f"the price edit refuses {why}")
out = body(asyncio.run(UA.api_server_prices(
    Req({"key": "server_cost_hetzner_usd", "price": "15.20"}), admin=admin, db=db)))
ok([s["price"] for s in out["servers"]] == ["15.20", "24.50"], "Hetzner changed, Contabo kept its default")
ok(db.query(M.ActivityLog).filter_by(action="server_price_set").count() == 1, "the price change is logged")

# 4. The pay table
TU.local_today = lambda: date(2026, 11, 17)
n = [0]


def picture(day, project=proj, **extra):
    n[0] += 1
    t = M.MasterTitle(title=f"Place {n[0]}", external_id=n[0], status="complete", project_id=project.id)
    db.add(t)
    db.flush()
    db.add(M.SavedPoster(master_title_id=t.id, user_id=w.id, username=w.username,
                         project_folder=project.slug, original_save_date=day,
                         title_folder_path=f"{n[0]}. Place {n[0]}", filename=f"{n[0]}.jpg",
                         source_url="u", **extra))
    db.flush()
    return db.query(M.SavedPoster).order_by(M.SavedPoster.id.desc()).first().id


october = [picture(date(2026, 10, 5)) for _ in range(3)]
picture(date(2026, 11, 2))
picture(date(2026, 11, 3), added_by=admin.id)                 # the owner's own: not pay
picture(date(2026, 11, 4), deleted_at=datetime(2026, 11, 5))  # deleted: not pay
picture(date(2026, 11, 6), project=other)                     # another project
# October's pictures were paid at the OLD rate by a run that ENDED in
# November: the case that made Paid look like overpayment (owner, 2026-11-17).
db.add(M.PaymentRun(worker_id=w.id, worker_username=w.username, period_start=date(2026, 9, 12),
                    period_end=date(2026, 11, 1), poster_count=3, rate_kes="5", amount_kes="15",
                    poster_ids_json=json.dumps(october), created_by=admin.id))
db.commit()


def table():
    return body(UA.api_usage_costs(request=None, admin=admin, db=db))


data = table()
rows = {r["month"]: r for r in data["workers"][0]["rows"]}
ok(data["workers"][0]["name"] == "humphrey" and len(rows) == 12, "one worker, twelve months")
ok(rows["2026-10"]["pictures"] == 3 and rows["2026-10"]["paid_kes"] == "15",
   "October: 3 pictures, paid 15 KES at the 5 KES they were paid at, though the run ended in November")
ok(rows["2026-10"]["pay_kes"] == "15" and rows["2026-10"]["owed"] == 0, "October fully paid: Pay equals Paid")
ok(rows["2026-11"]["pictures"] == 1, "November: the admin-added, deleted and other-project ones are left out")
ok(rows["2026-11"]["so_far"] and not rows["2026-10"]["so_far"], "only the current month is 'so far'")
ok(rows["2026-11"]["paid_kes"] == "0" and rows["2026-11"]["owed"] == 1 and rows["2026-11"]["pay_kes"] == "7",
   "November: nothing paid, 1 to pay at today's 7 KES")
ok(data["servers"][0]["price"] == "15.20", "the costs answer carries the server prices")

# 5. Date inputs keep their colour scheme
css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
ok(re.search(r"input\[type=date\][^{]*\{[^}]*color-scheme:\s*dark", css) is not None,
   "date inputs say they sit on a dark page")
ok(re.search(r"html\.theme-light input\[type=date\][^{]*\{[^}]*color-scheme:\s*light", css) is not None,
   "and on a light page in the light theme")

# 6. Sabotage: each rule above, broken, must be seen
real_day = UA.BILL_DAY
UA.BILL_DAY = 25
ok(UA.bill_due_month(date(2026, 11, 24)) == "2026-10", "sabotage seen: a 25th due day misses the 24th")
UA.BILL_DAY = real_day
real_crit = PAY.payable_criteria
PAY.payable_criteria = lambda wid: [M.SavedPoster.user_id == wid]
rows = {r["month"]: r for r in table()["workers"][0]["rows"]}
ok(rows["2026-11"]["pictures"] == 3, "sabotage seen: without the pay rule November counts 3")
PAY.payable_criteria = real_crit
print("ALL PASS")
