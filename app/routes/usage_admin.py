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
