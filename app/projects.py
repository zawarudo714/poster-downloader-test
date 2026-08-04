"""
Active-project resolution — the master/project split.

════════════════════════════════════════════════════════════════════════════
THE SHAPE
════════════════════════════════════════════════════════════════════════════
There are two levels of navigation:

  MASTER  — the account as a whole. Payments, chat, users, backups, email,
            the master activity log, cross-project stats. One of everything.
  PROJECT — one niche on one marketplace. Review Posters, Title List, Changes
            Requested, Skipped, Pipeline, project stats, CSV import. One of
            these per niche, and there will be many.

A page belongs to exactly one level. The nav REPLACES itself: at master level
you see master links plus a project picker; once you enter a project you see
that project's links plus a way back out. Never both at once — with ten
pipelines a combined nav is unusable, especially on a phone.

════════════════════════════════════════════════════════════════════════════
WHY A COOKIE AND NOT /admin/p/<slug>/...
════════════════════════════════════════════════════════════════════════════
Putting the project in the path is the textbook answer, and it would mean
rewriting every admin route, every fetch() in eight JS files, and every
redirect — a large, bug-prone change to code that currently works, in exchange
for bookmarkability the operator has never asked for.

Instead the active project is a piece of session state:

    cookie `pd_project` (slug)  →  User.last_project_id  →  default project

Existing URLs keep working and simply mean "…within the project I'm in".
Every scoped query goes through `scope_titles()`, so the scoping lives in one
place rather than being sprinkled through routes.

The cookie is a UI preference, NOT an authorisation token: it is always
validated against what the user is actually allowed to see (`allowed_projects`)
before it is honoured. A worker who edits their cookie to another project's
slug gets their own project back, not someone else's queue.

════════════════════════════════════════════════════════════════════════════
NULL project_id
════════════════════════════════════════════════════════════════════════════
The 101,605 imported master rows have project_id = NULL and always will until
the backfill runs. NULL means "the default project" everywhere — see
`pipeline.project_scope()`, which this module reuses rather than reimplements.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, object_session

from .models import MasterTitle, Project, User, UserProject
from .pipeline import (
    DEFAULT_PROJECT_SLUG,
    ensure_default_project,
    project_scope,
)


PROJECT_COOKIE = "pd_project"
# Long-lived: it's a preference, and losing it just means landing on the
# master dashboard. Matches the session cookie's 14 days.
PROJECT_COOKIE_MAX_AGE = 60 * 60 * 24 * 14


# ── Which projects a user may see ────────────────────────────────────────────

def allowed_projects(db: Session, user: User) -> list[Project]:
    """
    Active projects this user may work in, in display order.

    Admins see everything. Workers see only what they've been assigned — and
    a worker with NO assignments falls back to the default project, because
    that is exactly the state every existing worker is in right now and they
    must keep working through the upgrade without the admin touching anything.
    """
    q = db.query(Project).filter(Project.is_active == 1)

    if user.role == "admin":
        return q.order_by(Project.id).all()

    ids = [
        row.project_id
        for row in db.query(UserProject).filter(UserProject.user_id == user.id).all()
    ]
    if not ids:
        return [ensure_default_project(db)]
    return q.filter(Project.id.in_(ids)).order_by(Project.id).all()


def may_access(db: Session, user: User, project: Optional[Project]) -> bool:
    if project is None:
        return False
    return any(p.id == project.id for p in allowed_projects(db, user))


# ── Resolving the active project ─────────────────────────────────────────────

def project_by_slug(db: Session, slug: Optional[str]) -> Optional[Project]:
    if not slug:
        return None
    return db.query(Project).filter_by(slug=slug, is_active=1).first()


def active_project(request, db: Session, user: User) -> Optional[Project]:
    """
    The project the user is currently inside, or None for the master level.

    Resolution order — first hit that the user is actually allowed to see:

      1. the `pd_project` cookie   — where they navigated to this session
      2. User.last_project_id      — where they were when they last logged out
      3. for WORKERS only, their single project (or the default)

    Admins deliberately get None when neither 1 nor 2 applies: a fresh admin
    login lands on the master dashboard, which is the whole point of having
    one. Workers never see the master level, so they always get a project.
    """
    permitted = allowed_projects(db, user)
    if not permitted:
        return None
    by_id = {p.id: p for p in permitted}

    proj = project_by_slug(db, request.cookies.get(PROJECT_COOKIE))
    if proj is not None and proj.id in by_id:
        return proj

    if user.last_project_id and user.last_project_id in by_id:
        return by_id[user.last_project_id]

    if user.role != "admin":
        return permitted[0]

    return None


def remember_project(db: Session, user: User, project: Optional[Project]) -> None:
    """
    Persist the user's location so they return to it after logging back in.

    Not committed here — the caller owns the transaction.
    """
    user.last_project_id = project.id if project else None


def set_project_cookie(response, project: Optional[Project]) -> None:
    if project is None:
        response.delete_cookie(PROJECT_COOKIE, path="/")
    else:
        response.set_cookie(
            key=PROJECT_COOKIE,
            value=project.slug,
            max_age=PROJECT_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )


# ── Scoping queries ──────────────────────────────────────────────────────────

def _default_id(project: Project) -> Optional[int]:
    """
    The default project's id, resolved from a Project we already hold.

    Needed because `project_scope` has to know whether the project being
    scoped to is the default one — that's what decides whether the 101,605
    NULL-project master rows belong to it. Compared by SLUG rather than by
    calling ensure_default_project(), so scoping never issues a write against
    a session that might be read-only or mid-transaction.
    """
    obj = object_session(project)
    if obj is None:
        return None
    row = obj.query(Project.id).filter_by(slug=DEFAULT_PROJECT_SLUG).first()
    return row[0] if row else None


def scope_titles(q, project: Optional[Project]):
    """
    Restrict a MasterTitle query to one project.

    `project=None` means master level — no filter, everything included. That
    is correct for cross-project reporting and wrong for a project page, so
    project pages must always resolve a project before calling this.
    """
    if project is None:
        return q
    return q.filter(project_scope(project.id, default_project_id=_default_id(project)))


def scope_titles_multi(q, projects: list[Project]):
    """
    Restrict to ANY of several projects — used for a worker's queue, since a
    worker may cover more than one niche.

    Built from the same `project_scope()` clauses OR'd together, so the
    NULL-means-default rule can never drift between the two functions.
    """
    if not projects:
        return q
    from sqlalchemy import or_
    default_id = _default_id(projects[0])
    return q.filter(or_(*[
        project_scope(p.id, default_project_id=default_id) for p in projects
    ]))


# ── Display ──────────────────────────────────────────────────────────────────

def worker_label(project: Optional[Project]) -> str:
    """
    What a worker sees in the yellow brand slot.

    Whatever the admin named the project, verbatim. Workers only ever see one
    project at a time, so the project name IS the app name as far as they're
    concerned — which is also what makes a second niche feel like a separate
    tool to the people working it.
    """
    if project is None:
        return "Poster Downloader"
    return project.name or project.slug


def project_context(request, db: Session, user: Optional[User]) -> dict:
    """
    The template variables every page needs for the nav. Attached to
    `request.state` by middleware so no route has to remember to pass it.
    """
    if user is None:
        return {"active_project": None, "user_projects": [], "worker_label": "Poster Downloader"}

    permitted = allowed_projects(db, user)
    proj = active_project(request, db, user)
    return {
        "active_project": proj,
        "user_projects": permitted,
        "worker_label": worker_label(proj),
    }
