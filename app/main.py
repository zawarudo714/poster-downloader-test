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


@app.middleware("http")
async def attach_project_context(request: Request, call_next):
    """
    Resolve the active project once per page view and hang it off
    `request.state` so base.html can render the right nav.

    Done in middleware rather than as a dependency because EVERY template
    needs it and there are dozens of TemplateResponse calls across four
    routers. One forgotten `active_project=` would render a page with the
    wrong nav — a bug you'd only notice by looking. This way there is nothing
    to remember and nothing to forget.

    Skipped for anything that doesn't render a template (static assets, the
    JSON APIs, image serving, the machine API), because the extra DB session
    would be pure overhead on the highest-frequency requests in the app —
    the /api/state poll and the browse gallery's image loads.
    """
    path = request.url.path
    skip = (
        path.startswith("/static/")
        or path.startswith("/api/")
        or path.startswith("/admin/api/")
        or path.startswith("/admin/file/")
        or path.startswith("/admin/zip/")
        or path.startswith("/file_own/")
        or path == "/healthz"
    )
    request.state.project_ctx = None
    if not skip:
        from .db import SessionLocal
        from .auth import get_current_user
        from .projects import project_context

        db = SessionLocal()
        try:
            user = get_current_user(request, db)
            request.state.project_ctx = project_context(request, db, user)
        except Exception:
            # The nav is not worth a 500. Fall through with no context and
            # base.html degrades to the master nav.
            request.state.project_ctx = None
        finally:
            db.close()

    return await call_next(request)


@app.on_event("startup")
def on_startup():
    # Create any new tables, then add any new columns to existing ones.
    #
    # ORDER MATTERS: create_all() makes new tables but never ALTERs existing
    # ones, so the column migration has to follow it. Running this here rather
    # than as a deploy step removes an ordering trap that produced a bare
    # "Internal Server Error" with no clue as to why — see
    # app/schema_migrations.py for the full explanation.
    #
    # Only additive, idempotent changes run automatically. Data migrations and
    # backfills stay in scripts/migrate_pipeline.py where a human runs them
    # deliberately.
    init_db()

    import logging
    log = logging.getLogger("uvicorn.error")
    try:
        from .schema_migrations import migrate_schema
        result = migrate_schema()
        if result["added"]:
            log.info("Schema migration added: %s", ", ".join(result["added"]))
    except Exception as e:
        # Don't take the whole app down over this — but make it loud, because
        # the symptom otherwise is 500s on whichever page uses the new column.
        log.error("Schema migration FAILED: %s", e)

    # Daily auto-backup of poster.db at 00:00:05 server time, with a catch-up
    # if today's backup is missing (e.g. server was offline over midnight).
    from .backups import start_background_scheduler
    start_background_scheduler()

    # Reconcile the projects declared in pipeline.PROJECT_DEFS into the
    # database. Projects are code, not a form — see the registry's comment for
    # why — so this is what makes a rename or a new niche take effect. Logged
    # loudly, because a project rename also changes its storage folder and
    # that should never happen invisibly.
    from .db import SessionLocal
    from .pipeline import sync_projects
    db = SessionLocal()
    try:
        for change in sync_projects(db):
            log.info("Project sync: %s", change)
        db.commit()

        # Split the raw workspace by project. Safe to automate because it is
        # an atomic directory rename and because saved_poster_folder() accepts
        # both layouts — see the module docstring.
        from .workspace_migration import run_startup_migration
        run_startup_migration(db)

        # GPT generation runs HERE, not on the Windows node — it is an HTTPS
        # call, so it needs no desktop and keeps working when that box is
        # down. Started only if a project actually declares processor='gpt'.
        from .gpt_worker import start_background_worker
        start_background_worker()
    except Exception as e:
        db.rollback()
        log.error("Could not sync projects: %s", e)
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
