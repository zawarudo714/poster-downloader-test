"""
Auth: bcrypt password hashing + signed-cookie sessions.

Single-environment setup — auth, user list, and per-user state all live
in the same DB. The earlier multi-env machinery has been removed.
"""

from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .config import SESSION_COOKIE_NAME, SESSION_MAX_AGE, SESSION_SECRET
from .db import get_db
from .models import User


_signer = URLSafeSerializer(SESSION_SECRET, salt="poster-session-v1")


# ── Password hashing ─────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ── Session cookie ───────────────────────────────────────────────────────────

def make_session_cookie(username: str) -> str:
    return _signer.dumps({"u": username})


def read_session_cookie(token: str) -> Optional[str]:
    try:
        data = _signer.loads(token)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    return data.get("u")


def set_session(response, username: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=make_session_cookie(username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def clear_session(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


# ── FastAPI dependencies ─────────────────────────────────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Return the logged-in User or None. Does not raise."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    username = read_session_cookie(token)
    if not username:
        return None
    return db.query(User).filter_by(username=username, is_active=1).first()


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Raise 302 redirect to /login if not authenticated."""
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
            detail="Not authenticated",
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def require_worker_or_admin(user: User = Depends(require_user)) -> User:
    """Both roles can access worker views; admins can act as workers too."""
    return user
