"""
FastAPI entry point.

Run locally:
    uvicorn app.main:app --reload

Make sure to create the first admin first:
    python scripts/create_admin.py
"""

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import APP_DIR
from .db import init_db
from .routes import admin as admin_routes
from .routes import auth as auth_routes
from .routes import pipeline_admin as pipeline_admin_routes
from .routes import pipeline_api as pipeline_api_routes
from .routes import worker as worker_routes


app = FastAPI(title="Poster Downloader", version="1.0.0")

# Static files (CSS, JS).
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.middleware("http")
async def no_cache_dynamic(request: Request, call_next):
    """
    Tell browsers not to cache dynamic responses.

    Without this, GET /api/state polls were served from the disk cache —
    a worker who saved 3 posters and clicked DONE then saw "you only have
    0 posters" prompts. We exclude static assets and image-serving routes
    so those still benefit from normal caching.

    Also clears any leftover pd_env cookie from old builds. The env-switcher
    feature was removed; the cookie is harmless but no longer meaningful.
    """
    response = await call_next(request)
    path = request.url.path
    if not (path.startswith("/static/") or path.startswith("/file_own/")
            or path.startswith("/admin/file/") or path.startswith("/admin/zip/download/")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    # Garbage-collect leftover env cookies from older deployments.
    if request.cookies.get("pd_env"):
        response.delete_cookie("pd_env", path="/")
    return response


@app.on_event("startup")
def on_startup():
    init_db()
    # Daily auto-backup of poster.db at 00:00:05 server time, with a catch-up
    # if today's backup is missing (e.g. server was offline over midnight).
    from .backups import start_background_scheduler
    start_background_scheduler()

    # Guarantee the default pipeline project exists so the Pipeline tab and
    # the worker API have something to resolve against on a fresh install.
    # Note: new pipeline *columns* on existing tables are added by
    # scripts/migrate_pipeline.py — create_all() only creates new tables.
    from .db import SessionLocal
    from .pipeline import ensure_default_project
    db = SessionLocal()
    try:
        ensure_default_project(db)
        db.commit()
    except Exception:
        # A missing column means the migration hasn't run yet. Don't block
        # boot — the worker dashboard stays usable and the Pipeline tab will
        # report the problem.
        db.rollback()
    finally:
        db.close()


# Routers — auth at root, worker routes at root, admin under /admin.
app.include_router(auth_routes.router)
app.include_router(worker_routes.router)
app.include_router(admin_routes.router)
# Pipeline: admin UI under /admin/pipeline (session auth), machine API under
# /api/pipeline (worker-node bearer token). Deliberately separate routers so a
# leaked node token can never reach admin functionality.
app.include_router(pipeline_admin_routes.router)
app.include_router(pipeline_api_routes.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}
