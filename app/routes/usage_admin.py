"""
The Usage tab — /admin/usage (v292).

What this site's OpenAI key has cost, from OpenAI's own token counts on every
answer (app/ai_usage.py). Read only: nothing here sends, spends or changes a
setting. Per project, because each project has its own key.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from .. import ai_usage as U
from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..templating import templates

router = APIRouter(prefix="/admin", tags=["usage-admin"])

CHOICES = {
    "stage": ("all", "1", "2"),
    "kind": ("all", "first", "repaint", "test"),
    "mode": ("all", "batch", "live"),
}


@router.get("/usage", response_class=HTMLResponse)
def usage_page(request: Request, admin: User = Depends(require_admin)):
    # `user` draws the navigation (see listing_admin.listings_page).
    return templates.TemplateResponse(
        request, "admin_usage.html",
        {"user": admin, "admin": admin, "active_tab": "usage"})


@router.get("/api/usage")
def api_usage(request: Request,
              days: int = Query(14, ge=1, le=366),
              start: str = Query(""), end: str = Query(""),
              stage: str = Query("all"), kind: str = Query("all"),
              mode: str = Query("all"), prompt: str = Query("all"),
              admin: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    from .admin import current_project
    project = current_project(request, admin, db)
    for name, value in (("stage", stage), ("kind", kind), ("mode", mode)):
        if value not in CHOICES[name]:
            raise HTTPException(400, f"{name} must be one of {', '.join(CHOICES[name])}.")
    if prompt != "all" and not prompt.isdigit():
        raise HTTPException(400, "prompt must be 'all' or a version number.")
    today = datetime.utcnow().date()
    try:
        d1 = date.fromisoformat(end) if end else today
        d0 = date.fromisoformat(start) if start else d1 - timedelta(days=days - 1)
    except ValueError:
        raise HTTPException(400, "Dates must look like 2026-11-04.")
    if d0 > d1:
        raise HTTPException(400, "The start date is after the end date.")
    if (d1 - d0).days > 366:
        raise HTTPException(400, "Pick at most a year.")
    out = U.report(db, project, start=d0, end=d1, stage=stage, kind=kind,
                   mode=mode, prompt=prompt, today=today)
    out["project"] = project.slug
    out["start"], out["end"] = d0.isoformat(), d1.isoformat()
    return JSONResponse(out)


# ── Running costs and the pay table (v298, owner 2026-11-17) ────────────
# The two server bills and each worker's month-by-month pictures, shown
# small under the charts. NOT part of the charts: the charts are what the
# OpenAI key cost, these are fixed bills and wages.

SERVER_PRICES = (
    # key, label, currency symbol, default (the owner's bills, 2026-11-17)
    ("server_cost_hetzner_usd", "Hetzner", "$", "14.09"),
    ("server_cost_contabo_eur", "Contabo", "€", "24.50"),
)
BILL_PAID_KEY = "server_bill_paid_month"   # "YYYY-MM" of the last bill ticked Paid
BILL_DAY = 24                              # the reminder shows from this day
# Before anything is ticked, October 2026 counts as paid: the owner had paid
# it before this shipped (17 Nov), so the first pill is on 24 Nov.
BILL_PAID_START = "2026-10"


def _servers(db: Session) -> list[dict]:
    from ..payments import get_setting
    return [{"key": k, "name": name, "symbol": sym,
             "price": get_setting(db, k, default)}
            for k, name, sym, default in SERVER_PRICES]


def bill_due_month(today: date) -> str:
    """The month whose bill is due: this month from the 24th, else last month."""
    if today.day >= BILL_DAY:
        return f"{today.year:04d}-{today.month:02d}"
    prev = today.replace(day=1) - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}"


def bill_is_due(paid_month: str, today: date) -> bool:
    """True while the due month's bill has not been ticked Paid."""
    return (paid_month or "") < bill_due_month(today)


def _month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _last_months(today: date, n: int) -> list[str]:
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


@router.get("/api/usage/costs")
def api_usage_costs(request: Request, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    """Server prices, plus each worker's pictures and pay per month (12 months)."""
    from decimal import Decimal, InvalidOperation
    from sqlalchemy import func
    from ..models import MasterTitle, PaymentRun, SavedPoster
    from ..payments import get_rate_kes, payable_criteria
    from ..projects import scope_titles
    from ..timeutil import local_today
    from .admin import current_project
    project = current_project(request, admin, db)
    today = local_today()
    months = _last_months(today, 12)
    first = date.fromisoformat(months[-1] + "-01")
    try:
        rate = Decimal(get_rate_kes(db, project))
    except (InvalidOperation, TypeError):
        rate = Decimal(0)
    workers = (db.query(User).filter(User.role == "worker", User.is_deleted == 0)
                 .order_by(User.username).all())
    out_workers = []
    for w in workers:
        q = (db.query(SavedPoster.original_save_date, func.count(SavedPoster.id))
               .join(MasterTitle, MasterTitle.id == SavedPoster.master_title_id)
               .filter(*payable_criteria(w.id),
                       SavedPoster.original_save_date >= first))
        q = scope_titles(q, project)
        pictures: dict[str, int] = {}
        for day, n in q.group_by(SavedPoster.original_save_date).all():
            pictures[_month_key(day)] = pictures.get(_month_key(day), 0) + n
        paid: dict[str, Decimal] = {}
        for end, amount in (db.query(PaymentRun.period_end, PaymentRun.amount_kes)
                              .filter(PaymentRun.worker_id == w.id,
                                      PaymentRun.period_end >= first).all()):
            try:
                paid[_month_key(end)] = paid.get(_month_key(end), Decimal(0)) + Decimal(amount)
            except (InvalidOperation, TypeError):
                pass
        rows = [{"month": m, "pictures": pictures.get(m, 0),
                 "pay_kes": str((rate * pictures.get(m, 0)).quantize(Decimal("1"))),
                 "paid_kes": str(paid.get(m, Decimal(0)).quantize(Decimal("1"))),
                 "so_far": m == months[0]}
                for m in months]
        if any(r["pictures"] or r["paid_kes"] != "0" for r in rows):
            out_workers.append({"name": w.username, "rows": rows})
    from ..payments import get_setting
    return JSONResponse({
        "servers": _servers(db),
        "bill_day": BILL_DAY,
        "paid_month": get_setting(db, BILL_PAID_KEY, BILL_PAID_START),
        "rate_kes": str(rate),
        "project": project.slug if project else "",
        "workers": out_workers,
    })


@router.post("/api/usage/server_prices")
async def api_server_prices(request: Request, admin: User = Depends(require_admin),
                            db: Session = Depends(get_db)):
    """Change a server price shown on Usage (a note for the owner, nothing is paid)."""
    from decimal import Decimal, InvalidOperation
    from ..audit import log as log_activity
    from ..payments import set_setting
    body = await request.json()
    keys = {k for k, *_ in SERVER_PRICES}
    key, value = str(body.get("key", "")), str(body.get("price", "")).strip()
    if key not in keys:
        raise HTTPException(400, "Unknown server.")
    try:
        if Decimal(value) < 0 or Decimal(value) > 100000:
            raise InvalidOperation
    except InvalidOperation:
        raise HTTPException(400, "The price must be a number like 14.09.")
    set_setting(db, key, value, by=admin.username)
    log_activity(db, user=admin, action="server_price_set",
                 details={"key": key, "price": value})
    db.commit()
    return JSONResponse({"servers": _servers(db)})


@router.get("/api/server_bill")
def api_server_bill(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Is the monthly server bill due? Draws the small reminder in the top bar."""
    from ..payments import get_setting
    from ..timeutil import local_today
    today = local_today()
    paid_month = get_setting(db, BILL_PAID_KEY, BILL_PAID_START)
    return JSONResponse({"due": bill_is_due(paid_month, today),
                         "month": bill_due_month(today),
                         "servers": _servers(db)})


@router.post("/api/server_bill/paid")
def api_server_bill_paid(admin: User = Depends(require_admin),
                         db: Session = Depends(get_db)):
    """The owner ticked Paid: hide the reminder until the next 24th."""
    from ..audit import log as log_activity
    from ..payments import set_setting
    from ..timeutil import local_today
    month = bill_due_month(local_today())
    set_setting(db, BILL_PAID_KEY, month, by=admin.username)
    log_activity(db, user=admin, action="server_bill_paid", details={"month": month})
    db.commit()
    return JSONResponse({"due": False, "month": month})
