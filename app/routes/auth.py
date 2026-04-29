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
    user = db.query(User).filter_by(username=username.strip(), is_active=1).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid username or password."},
            status_code=401,
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
