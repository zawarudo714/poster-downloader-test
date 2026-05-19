"""Login / logout routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..auth import (
    clear_session, get_current_user, set_session, verify_password,
)
from ..db import get_db
from ..models import User
from ..templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        # Already logged in — bounce to the right dashboard.
        return RedirectResponse("/admin" if user.role == "admin" else "/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # Query WITHOUT is_active filter so we can distinguish "wrong password"
    # from "correct password but account disabled". Disabled workers see a
    # friendly maintenance message instead of a confusing credentials error.
    user = db.query(User).filter_by(username=username.strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
        )
    # Credentials match. Check if the account is disabled or soft-deleted.
    if not user.is_active or user.is_deleted:
        # Show a friendly "under maintenance" message instead of revealing
        # that the account was disabled. Admin can customize the message
        # via app_settings (key: disabled_login_message).
        from ..models import AppSetting
        setting = db.query(AppSetting).filter_by(key="disabled_login_message").first()
        msg = (setting.value if setting else None) or \
              "This site is currently undergoing an update. Please try again later."
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": msg, "error_kind": "maintenance"},
            status_code=200,  # 200, not 401 — it's not a credentials error
        )
    redirect_to = "/admin" if user.role == "admin" else "/"
    response = RedirectResponse(redirect_to, status_code=302)
    set_session(response, user.username)
    return response


@router.post("/logout")
@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=302)
    clear_session(response)
    # Also clear the env cookie so the next user on this browser starts clean.
    response.delete_cookie("pd_env", path="/")
    return response
