"""
Auth: bcrypt password hashing + signed-cookie sessions.

We use itsdangerous to sign the session cookie containing the username. The
cookie is HttpOnly + SameSite=Lax. Set SESSION_SECRET in production.
"""

from __future__ import annotations

from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
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
        # In production behind HTTPS, also set secure=True via reverse proxy or here:
        # secure=True,
    )


def clear_session(response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


# ── FastAPI dependencies ─────────────────────────────────────────────────────

def _live_users_session():
    """
    Return a freshly-opened Session bound to the *live* env's DB, regardless
    of which env the request is currently in.

    Why: the `users` table is the system-wide source of truth for who can log
    in. Test envs start with empty user tables, so if auth used the active
    env's DB, switching into a test env would log everyone out instantly.
    Auth therefore always consults the live env; the data the user *operates
    on* (titles, posters, revisions) is the only thing that's per-env.
    """
    from .envs import _build_engine, _sessions, LIVE_ENV, _engines, _engines_lock
    if LIVE_ENV not in _sessions:
        with _engines_lock:
            if LIVE_ENV not in _sessions:
                _build_engine(LIVE_ENV)
    return _sessions[LIVE_ENV]()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Return the logged-in User or None. Does not raise.

    Reads from the live users table (see `_live_users_session` for the why).
    `db` is still injected (and used by the rest of the request) but is
    intentionally NOT used here.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    username = read_session_cookie(token)
    if not username:
        return None
    live_db = _live_users_session()
    try:
        return live_db.query(User).filter_by(username=username, is_active=1).first()
    finally:
        live_db.close()


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Raise 302 redirect to /login if not authenticated.

    Returns the user record from the *current env's* users table (attached to
    the request's `db` session), seeded from the live record on first touch.

    Why the dance: per-env mutable state — `locked_master_id` and
    `last_pull_size` — is meaningful PER ENV (a master_title id only makes
    sense in one env's master_titles table). Auth fields (password_hash,
    role, env, is_active) are authoritative on the live row; we re-check
    them on every request via `get_current_user`. The env row gets seeded
    once per env-the-user-touches and then drifts naturally on auth fields
    until the next touch.
    """
    live_user = get_current_user(request, db)
    if live_user is None:
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/login"},
            detail="Not authenticated",
        )

    env_user = db.query(User).filter_by(id=live_user.id).first()
    if env_user is None:
        # First time this user has touched this env — seed a row in env's
        # users table mirroring live, with empty per-env state.
        env_user = User(
            id            = live_user.id,
            username      = live_user.username,
            password_hash = live_user.password_hash,
            role          = live_user.role,
            is_active     = live_user.is_active,
            env           = live_user.env,
            created_at    = live_user.created_at,
            locked_master_id = None,
            last_pull_size   = None,
        )
        db.add(env_user)
        db.flush()
    else:
        # Refresh auth-relevant fields from live in case admin changed them.
        env_user.password_hash = live_user.password_hash
        env_user.role          = live_user.role
        env_user.is_active     = live_user.is_active
        env_user.env           = live_user.env
        # NOTE: deliberately do NOT touch locked_master_id / last_pull_size —
        # those are per-env state and live row's copies are not authoritative.
    return env_user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def require_worker_or_admin(user: User = Depends(require_user)) -> User:
    """Both roles can access worker views; admins can act as workers too.

    The "workers must be in live env" rule is enforced in the env-selection
    middleware (main.py), not here, so it can also clear the stale cookie
    in the same response.
    """
    return user
