"""
FastAPI entry point.

Run locally:
    uvicorn app.main:app --reload

Make sure to create the first admin first:
    python scripts/create_admin.py
"""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .config import APP_DIR
from .db import init_db
from .envs import LIVE_ENV, set_active_env, test_env_exists
from .routes import admin as admin_routes
from .routes import auth as auth_routes
from .routes import worker as worker_routes


app = FastAPI(title="Poster Downloader", version="1.0.0")

# Static files (CSS, JS).
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.middleware("http")
async def select_env_middleware(request: Request, call_next):
    """
    Decide which environment this request operates in.

    - Admins can switch envs freely via the env-switcher (writes the
      `pd_env` cookie). They get whichever env the cookie names, falling
      back to "live" if it points at an env that no longer exists.
    - Workers ALWAYS operate in the env recorded on their user row
      (`User.env`). The cookie is ignored for them. This way an admin can
      put `worker_test1` in the `sandbox` env and the next time that
      worker logs in, every request goes to `sandbox` automatically.
    - Anonymous requests (login page, static, healthz) default to "live".
    - If the user's recorded env has been deleted, fall back to "live"
      and silently rewrite the user's record so subsequent logins are clean.
    """
    cookie_env = request.cookies.get("pd_env") or LIVE_ENV
    env = cookie_env if (cookie_env == LIVE_ENV or test_env_exists(cookie_env)) else LIVE_ENV

    # Anything other than "live"/known-test gets normalised below; we still
    # need to look up the user to know whether to honour their pinned env.
    from .config import SESSION_COOKIE_NAME
    token = request.cookies.get(SESSION_COOKIE_NAME)
    rewrite_bad_user_env = False
    if token:
        from .auth import read_session_cookie, _live_users_session
        from .models import User
        username = read_session_cookie(token)
        if username:
            live_db = _live_users_session()
            try:
                u = live_db.query(User).filter_by(username=username, is_active=1).first()
                if u is not None:
                    if u.role == "worker":
                        # Worker's env is *pinned* — ignore the cookie entirely.
                        if u.env != LIVE_ENV and not test_env_exists(u.env):
                            # Their pinned env was deleted. Bounce them to live
                            # and quietly fix the user record so next time is clean.
                            env = LIVE_ENV
                            u.env = LIVE_ENV
                            live_db.commit()
                            rewrite_bad_user_env = True
                        else:
                            env = u.env
                    else:
                        # Admin: cookie wins, bounded above to known envs.
                        pass
                else:
                    # Username in cookie no longer exists — log out via env reset.
                    env = LIVE_ENV
            finally:
                live_db.close()
    else:
        # Unauthenticated: never enter a test env.
        env = LIVE_ENV

    set_active_env(env)
    response = await call_next(request)
    response.headers["X-Poster-Env"] = env
    # Browsers heuristically cache GET responses lacking a Cache-Control
    # header. That broke worker-side counters: rapid /api/state polls
    # served stale poster lists, so a worker who saved 3 posters and
    # clicked DONE got "you only have 0 posters" prompts. Prevent any
    # caching of dynamic responses; static + image-serving routes opt out.
    path = request.url.path
    if not (path.startswith("/static/") or path.startswith("/file_own/")
            or path.startswith("/admin/file/") or path.startswith("/admin/zip/download/")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    # If we forced someone out of a test env, also clear the stale cookie so
    # subsequent requests don't have to repeat the dance.
    if cookie_env != LIVE_ENV and env == LIVE_ENV:
        response.delete_cookie("pd_env", path="/")
    return response


@app.on_event("startup")
def on_startup():
    init_db()
    # Start the daily-backup scheduler (also runs a catch-up backup if today's
    # is missing, e.g. server was offline over midnight). The same scheduler
    # also resets every test env nightly.
    from .backups import start_background_scheduler
    start_background_scheduler()


# Routers — auth at root, worker routes at root, admin under /admin.
app.include_router(auth_routes.router)
app.include_router(worker_routes.router)
app.include_router(admin_routes.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
