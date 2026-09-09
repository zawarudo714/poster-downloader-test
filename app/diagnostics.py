"""
Consistency scanner — read-only.

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════
State lives in three places that can disagree with each other:

    the database   ·   the workspace on disk   ·   the marketplace

Nothing keeps them in lockstep. A file gets deleted outside the app, a node
dies mid-batch and leaves a claim behind, a title is marked complete but its
last poster was removed, a listing is taken down for copyright. Each of those
is silent: the app carries on and the damage only surfaces weeks later as
"why is this image not on FineArtAmerica".

This module answers that class of question on demand. Every check returns a
list of findings with enough identifying detail to act on, and NOTHING here
writes. That's deliberate and it should stay that way:

  * An automatic "fix" for a missing file is a guess about which of the two
    sides is right, and it's wrong about half the time. Deleting a database
    row because a file vanished destroys the audit trail and the worker's pay
    record; re-downloading because a row exists overwrites a file an admin may
    have replaced on purpose.
  * A report you read is a decision you made. A repair that ran by itself at
    3am is a mystery you get to debug later.

So: findings, counts, and a link to the page that owns the problem. The fix is
always a deliberate action somewhere else.

════════════════════════════════════════════════════════════════════════════
MULTI-PROJECT
════════════════════════════════════════════════════════════════════════════
Every check takes a `Scope`. With no project it looks at everything and each
finding says which niche it came from; with one, it looks only there.

That is not cosmetic. "14 titles complete with no images" means nothing until
you know whether that is the movie backlog or the celebrity project you set
up yesterday, and the answers are completely different.

Two rules for anything added here:

  * Scope through `scope.posters` / `scope.titles`. Never filter on
    project_id by hand — NULL means the DEFAULT project, not "any", and
    getting that wrong makes one niche inherit another's 101,605 rows.
  * Never say "poster". Use `scope.noun` / `scope.nouns`, which come from
    the project itself. A check that says "3 posters missing" is wrong on
    every project that calls them something else.

════════════════════════════════════════════════════════════════════════════
ADDING A CHECK
════════════════════════════════════════════════════════════════════════════
Write a function taking (db, scope) and returning a `CheckResult`, then add
it to CHECKS. Keep each one bounded — LIMIT every query. This runs against
101,605 master rows and 7,972 posters today and will run against several
times that; a check that materialises the whole table will make the page
unusable exactly when the operator most needs it.

If a check cannot mean anything for a project — because that project has no
such stage — return `skipped()` rather than an empty result. "Not applicable
here" and "checked, all clear" look identical otherwise, and only one of them
is reassuring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, or_, true as sa_true
from sqlalchemy.orm import Session

from .config import BASE_DIR, WORKSPACE_DIR
from .models import (
    AccountProject, LedgerEntry, MarketplaceSnapshot, MasterTitle,
    ProcessedImage, Project, Revision, SavedPoster,
    UploadAccount, UploadTracking, User, WorkerNode,
)
from .utils import saved_poster_path


# Findings are capped per check. If you hit the cap the point is already made —
# nobody triages 4,000 rows in a browser, and the count tells you the scale.
MAX_ROWS = 200


@dataclass
class Finding:
    what: str                      # one line, plain language
    detail: str = ""               # ids, paths, whatever identifies it
    link: Optional[str] = None     # page that owns the fix, if there is one
    # Which niche this belongs to. Filled in when scanning everything, so a
    # mixed list is still readable; left blank when already scoped to one.
    project: str = ""


class Scope:
    """
    Which project a run covers, and the words to describe it.

    Built once per run and handed to every check, so no check has to know how
    project scoping works — or, more importantly, get it subtly wrong. The
    NULL rule in particular has caused real damage twice, and lives in
    exactly one place.
    """

    def __init__(self, db: Session, project_id: Optional[int] = None):
        from .pipeline import _default_project_id, project_scope, resolve_project

        self.db = db
        self.project_id = project_id or None
        self.all_projects = not self.project_id
        self.default_id = _default_project_id(db)
        self.names = {p.id: p.name for p in db.query(Project).all()}

        proj = resolve_project(db, self.project_id) if self.project_id else None
        self.project = proj
        self.name = proj.name if proj else "all projects"
        self.noun = (proj.item_noun if proj else "item")
        self.nouns = (proj.item_noun_plural if proj else "items")
        self.processor = (proj.processor if proj else None)

        self._title_criterion = (
            project_scope(self.project_id, default_project_id=self.default_id)
            if self.project_id else sa_true()
        )

    @property
    def titles(self):
        """Criterion for a query that already includes MasterTitle."""
        return self._title_criterion

    @property
    def posters(self):
        """Criterion for a SavedPoster query, without needing a join."""
        if not self.project_id:
            return sa_true()
        ids = self.db.query(MasterTitle.id).filter(self._title_criterion)
        return SavedPoster.master_title_id.in_(ids.scalar_subquery())

    def label(self, project_id: Optional[int]) -> str:
        """The project name for a finding. NULL means the default project."""
        if not self.all_projects:
            return ""
        return self.names.get(project_id or self.default_id, "")

    def title_of(self, t) -> str:
        """
        A title's display name, with the year only where a year means
        something. Artists have none, and "(None)" after every name is the
        kind of noise that teaches people to stop reading.
        """
        return f"{t.title} ({t.year})" if t.year else (t.title or "")


@dataclass
class CheckResult:
    key: str
    title: str
    # What it means and what to do — shown next to the result, because a
    # finding you don't understand is a finding you ignore.
    explain: str
    severity: str                  # 'error' | 'warn' | 'info'
    count: int = 0
    truncated: bool = False
    findings: list[Finding] = field(default_factory=list)
    error: Optional[str] = None    # the check itself blew up
    # "This cannot apply here", which is a different message from "all clear"
    # and must not be reported as a pass.
    skipped: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key, "title": self.title, "explain": self.explain,
            "severity": self.severity, "count": self.count,
            "truncated": self.truncated, "error": self.error,
            "skipped": self.skipped,
            "findings": [
                {"what": f.what, "detail": f.detail, "link": f.link,
                 "project": f.project}
                for f in self.findings
            ],
        }


def _result(key, title, explain, severity, rows, total=None) -> CheckResult:
    total = len(rows) if total is None else total
    return CheckResult(
        key=key, title=title, explain=explain, severity=severity,
        count=total, truncated=total > len(rows), findings=rows,
    )


_PROJECT_OF_CACHE: dict[int, Optional[int]] = {}


def _project_of(db: Session, poster) -> Optional[int]:
    """
    Which project a saved item belongs to, via its title.

    Cached per process because the poster-level checks call it once per
    finding, and 200 findings would otherwise be 200 identical queries.
    A title's project effectively never changes; if it does, a restart or
    the next deploy clears this.
    """
    mid = poster.master_title_id
    if mid not in _PROJECT_OF_CACHE:
        row = db.query(MasterTitle.project_id).filter(MasterTitle.id == mid).first()
        _PROJECT_OF_CACHE[mid] = row[0] if row else None
    return _PROJECT_OF_CACHE[mid]


def _skipped(key, title, why) -> CheckResult:
    """Not applicable to this project — reported as such, never as a pass."""
    return CheckResult(key=key, title=title, explain=why,
                       severity="info", count=0, skipped=why)


# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE ↔ DISK
# ═══════════════════════════════════════════════════════════════════════════

def check_missing_files(db: Session, scope: Scope) -> CheckResult:
    """
    Live poster rows whose file is not on disk.

    Bounded by scanning the most recent rows first — the ones most likely to
    matter, and the ones an admin can still do something about. A full scan of
    every poster ever saved is what the CLI is for.
    """
    rows = (
        db.query(SavedPoster)
          .filter(SavedPoster.deleted_at.is_(None), scope.posters)
          .order_by(SavedPoster.created_at.desc())
          .limit(4000)
          .all()
    )
    bad = []
    for sp in rows:
        try:
            if not saved_poster_path(sp).is_file():
                bad.append(Finding(
                    what=f"{sp.username} · {sp.title_folder_path} · {sp.filename}",
                    detail=f"#{sp.id}, saved {sp.original_save_date}",
                    link=f"/admin/browse?worker={sp.username}"
                         f"&date={sp.original_save_date}",
                    project=scope.label(_project_of(db, sp)),
                ))
        except Exception as e:                      # unreadable path, bad chars
            bad.append(Finding(f"#{sp.id}: {e}"))
        if len(bad) >= MAX_ROWS:
            break
    return _result(
        "missing_files", f"{scope.nouns.capitalize()} with no file on disk",
        "The database says this file exists; the workspace doesn't have it. "
        "Usually deleted outside the app. The worker was likely paid for it "
        "and the pipeline will fail on it. Decide one at a time: delete the "
        "record from the review page, or restore the file.",
        "error", bad,
    )


def check_posters_without_title(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: every living poster's master title must still exist.

    ════════════════════════════════════════════════════════════════════════
    WHY (Mega Audit, 2026-09-06)
    ════════════════════════════════════════════════════════════════════════
    A poster whose title row is gone does not error anywhere — it VANISHES.
    Every screen reaches posters by joining through master_titles, so an
    orphan simply drops out of the funnel, the counts and the review queues
    while its file sits on disk and its pay record stands. The clear button
    and the replace-import now REFUSE while living posters would be
    orphaned; this check is the net underneath that guard, because a state
    that is supposed to be impossible still deserves a tripwire (the guard
    itself could be walked around by a future code path).
    """
    rows = [
        Finding(f"#{sp.id} · {sp.filename}",
                f"saved by {sp.user_id}, title id {sp.master_title_id} "
                f"no longer exists", "/admin/diagnostics")
        for sp in (db.query(SavedPoster)
                     .outerjoin(MasterTitle,
                                SavedPoster.master_title_id == MasterTitle.id)
                     .filter(SavedPoster.deleted_at.is_(None),
                             MasterTitle.id.is_(None))
                     .limit(MAX_ROWS).all())
    ]
    return _result(
        "posters_without_title", "Saved images whose title row is gone",
        "These images exist on disk and in pay records but appear on NO "
        "screen — their title was deleted after they were saved. Restore "
        "the title list they belonged to, or soft-delete them deliberately.",
        "error", rows,
    )


def check_orphan_files(db: Session, scope: Scope) -> CheckResult:
    """
    Files sitting in the workspace with no database row pointing at them.

    The known-file set is built by ASKING saved_poster_path() where each row
    lives, not by re-deriving the path from columns. That mattered the moment
    the workspace gained a project level: this check used to rebuild
    "{worker}/{date}/{title}/{file}" by hand, so after the split every single
    real file looked orphaned — the check was looking for `worker1/...` while
    the files sat at `GR(Movie&Series)/worker1/...`.

    One source of truth for where a file is. If the layout changes again,
    this check follows automatically instead of crying wolf.
    """
    # The known set is built from EVERY poster regardless of scope. It has to
    # be: a file belonging to another project is not an orphan, and scoping
    # this set would report every other niche's files as unknown.
    known: set[str] = set()
    for sp in db.query(SavedPoster).all():
        try:
            known.add(saved_poster_path(sp).resolve().as_posix())
        except Exception:
            continue

    # Scanning one project's subtree when asked. The workspace is laid out
    # {project}/{worker}/{date}/..., so the project's own folder is the whole
    # of its files — and on a 100k-file archive, not walking the rest is the
    # difference between a fast page and one nobody opens.
    root = WORKSPACE_DIR
    if scope.project_id and scope.project is not None:
        from .workspace_migration import project_folder_for
        candidate = WORKSPACE_DIR / project_folder_for(scope.project)
        if candidate.is_dir():
            root = candidate

    # ── A FILE CAN BE CLAIMED THREE WAYS, AND THIS KNEW ONE ──────────────
    #
    # Until 2026-09-09 the known set above was the whole answer, so this
    # check told the owner his SIGNATURE and his REFERENCE PICTURE were
    # unknown files taking up space — under a heading saying the app "has no
    # idea they exist". Acting on that would have deleted the signature and
    # stopped the poster builder, because a signature switched on with no
    # file is a hard refusal. It listed the failure screenshots too.
    #
    # The three ways, and the general shape is bigger than this screen:
    #
    #   1. A ROW points at it        — every SavedPoster, handled above.
    #   2. A SETTING names it        — signature_image, openai_style_image.
    #   3. A COLUMN elsewhere holds it — UploadTracking.last_screenshot.
    #
    # **Before a check calls something unowned, enumerate every way it could
    # be owned.** One-of-three is not coverage, it is a confident wrong
    # answer — and this one pointed at a delete button.
    known |= _claimed_by_settings(db)
    known |= _claimed_by_columns(db)

    orphans: list[Finding] = []
    total = 0
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve().as_posix() in known:
                continue
            try:
                rel = path.relative_to(WORKSPACE_DIR).as_posix()
            except ValueError:
                rel = path.as_posix()
            total += 1
            if len(orphans) < MAX_ROWS:
                orphans.append(Finding(rel, f"{path.stat().st_size} bytes"))

    return _result(
        "orphan_files", "Files on disk with no database record",
        "These take up space and will never be processed, paid for or "
        "uploaded — the app has no idea they exist. Common after a restore "
        "from backup where the database is older than the workspace. Your "
        "signature, your reference picture and the failure screenshots are "
        "NOT counted here, because something does point at those.",
        "warn", orphans, total,
    )


def _claimed_by_settings(db: Session) -> set[str]:
    """
    Workspace files that a SETTING names — the signature and the style image.

    Read from `pipeline.DEFAULTS` rather than from a list here, so a future
    setting that names a file is covered the day it is added. The rule that
    decides which keys count is "the default is a path-shaped string and the
    key ends in _image", which is derived from the shape of the setting
    rather than being an exceptions list somebody has to extend.
    """
    out: set[str] = set()
    try:
        from .pipeline import DEFAULTS, get_setting
        from .models import Project
        keys = [k for k, v in DEFAULTS.items()
                if k.endswith("_image") and isinstance(v, str)]
        if not keys:
            return out
        # Per project as well as globally: these settings are per-project, so
        # reading only the global value would miss every real one.
        projects = [None] + list(db.query(Project).all())
        for proj in projects:
            for key in keys:
                rel = str(get_setting(db, key, project=proj) or "").strip()
                if rel:
                    out.add((WORKSPACE_DIR / rel).resolve().as_posix())
    except Exception:      # noqa: BLE001 — a blind spot must not break the page
        pass
    return out


def _claimed_by_columns(db: Session) -> set[str]:
    """
    Workspace files a COLUMN points at — today, the failure screenshots and
    page dumps recorded on `UploadTracking.last_screenshot`.

    The whole `_pipeline_artifacts` folder is claimed rather than only the
    paths currently on rows. The Failure Evidence panel lists that folder
    DIRECTLY, so a file whose row has since been pruned is still something
    the screen offers to open — which is the opposite of unknown.
    """
    out: set[str] = set()
    try:
        artifacts = (WORKSPACE_DIR / "_pipeline_artifacts").resolve()
        if artifacts.is_dir():
            for path in artifacts.rglob("*"):
                if path.is_file():
                    out.add(path.resolve().as_posix())
        for (shot,) in db.query(UploadTracking.last_screenshot).filter(
                UploadTracking.last_screenshot.isnot(None)).all():
            if shot:
                out.add((WORKSPACE_DIR / shot).resolve().as_posix())
    except Exception:      # noqa: BLE001
        pass
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  WORKFLOW CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════

def check_complete_without_posters(db: Session, scope: Scope) -> CheckResult:
    """Titles marked complete that have nothing saved on them at all."""
    sub = (
        db.query(SavedPoster.master_title_id)
          .filter(SavedPoster.deleted_at.is_(None))
          .distinct()
    )
    q = (
        db.query(MasterTitle)
          .filter(MasterTitle.status == "complete",
                  ~MasterTitle.id.in_(sub),
                  scope.titles)
    )
    total = q.count()
    rows = [
        Finding(scope.title_of(t),
                f"title #{t.id}, external id {t.external_id}",
                "/admin/master?q=" + (t.title or ""),
                project=scope.label(t.project_id))
        for t in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "complete_empty", f"Completed titles with no {scope.nouns}",
        "Marked done but everything saved on them has been deleted. Nothing "
        "will reach the marketplace for these. Send the title back to the "
        "worker, or accept it as genuinely unavailable and skip it.",
        "error", rows, total,
    )


def check_stale_claims(db: Session, scope: Scope) -> CheckResult:
    """
    Posters a worker node claimed and never reported back on.

    A node that dies mid-batch leaves its images in `processing` or
    `uploading` forever — they're not queued, not done, and invisible. The
    dispatcher reaps these on its own schedule; this surfaces the ones that
    have been sitting long enough to mean the node is gone, not busy.
    """
    cutoff = datetime.utcnow() - timedelta(hours=2)
    q = (
        db.query(SavedPoster)
          .filter(SavedPoster.pipeline_status.in_(("processing", "uploading")),
                  SavedPoster.claimed_at.isnot(None),
                  SavedPoster.claimed_at < cutoff,
                  scope.posters)
    )
    total = q.count()
    rows = [
        Finding(f"#{sp.id} · {sp.filename}",
                f"{sp.pipeline_status} on '{sp.claimed_by}' since "
                f"{sp.claimed_at:%Y-%m-%d %H:%M}",
                "/admin/pipeline",
                project=scope.label(_project_of(db, sp)))
        for sp in q.order_by(SavedPoster.claimed_at.asc()).limit(MAX_ROWS).all()
    ]
    return _result(
        "stale_claims", "Work claimed by a machine that never finished",
        "Something took these and stopped — crashed, rebooted, or lost its "
        "connection. They are in no queue and nothing is marked failed. "
        "RELEASE them from Pipeline → Needs Attention once you are sure "
        "nothing is still working on them.",
        "warn", rows, total,
    )


def check_greenlit_not_complete(db: Session, scope: Scope) -> CheckResult:
    """Work in the pipeline whose title isn't complete."""
    q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status.isnot(None),
                  SavedPoster.pipeline_status != "skipped",
                  SavedPoster.deleted_at.is_(None),
                  MasterTitle.status != "complete",
                  scope.titles)
    )
    total = q.count()
    rows = [
        Finding(f"{scope.title_of(mt)} · {sp.filename}",
                f"title is '{mt.status}', {scope.noun} is '{sp.pipeline_status}'",
                "/admin/pipeline",
                project=scope.label(mt.project_id))
        for sp, mt in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "greenlit_incomplete", "Pipeline work on titles that aren't complete",
        "These are being processed or are already live while their title is "
        "back in progress or was reopened. Not automatically wrong — a title "
        "can be reopened after its work went out — but worth knowing before "
        "you approve more on it.",
        "info", rows, total,
    )


def check_uploaded_without_processed(db: Session, scope: Scope) -> CheckResult:
    """Uploads recorded against something with no current processed file."""
    sub = (
        db.query(ProcessedImage.saved_poster_id)
          .filter(ProcessedImage.is_current == 1)
          .distinct()
    )
    q = db.query(UploadTracking).filter(
        UploadTracking.status == "uploaded",
        ~UploadTracking.saved_poster_id.in_(sub),
    )
    # UploadTracking carries its own project_id, so this one scopes directly.
    if scope.project_id:
        q = q.filter(UploadTracking.project_id == scope.project_id)
    total = q.count()
    # Account NAMES, not ids — "poster #12 → Golden Reel" is readable and
    # "poster #12 → account #3" is a number the owner has to translate.
    # Fetched once, because a lookup per row is a query per row.
    names = _account_names(db)
    rows = [
        Finding(f"poster #{ut.saved_poster_id} → "
                f"{names.get(ut.account_id, f'account #{ut.account_id}')}",
                ut.remote_title or "", "/admin/pipeline",
                project=scope.label(ut.project_id))
        for ut in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "upload_no_processed", "Live listings with no processed file on record",
        "The image is on the marketplace but the archive has no current "
        "derivative for it, so you could not re-upload it after a ban "
        "without hunting through folders.\n\n"
        "This is EXPECTED straight after a migration: the upload history "
        "comes from the old tool's own records and needs no files, while "
        "the files themselves live on the storage box, which only the "
        "worker machine can see. Press READ THE STORAGE BOX on the Pipeline "
        "page and most of these should disappear. Whatever is left is "
        "either work the old Photoshop script destroyed — the titles with a "
        "dot in the name — or a record genuinely lost.",
        "warn", rows, total,
    )


def check_unassigned_titles(db: Session, scope: Scope) -> CheckResult:
    """Master rows that were never given a project."""
    # Only meaningful across the whole install: a NULL row IS the default
    # project, so asking about it from inside a project is a contradiction.
    if not scope.all_projects:
        return _skipped(
            "unassigned_titles", "Titles with no project",
            "Only checked across all projects — a title with no project is "
            "treated as the default one, so the question does not arise "
            "inside a single project.")

    total = db.query(func.count(MasterTitle.id)).filter(
        MasterTitle.project_id.is_(None)
    ).scalar() or 0
    rows = []
    if total:
        rows = [Finding(
            f"{total:,} master rows have no project",
            "These are treated as belonging to the default project. The "
            "importer always stamps a project on every row, so this should "
            "be impossible — seeing it means rows arrived by some other "
            "route. Give them a project before a SECOND project's titles go "
            "into the same table, or the two will silently mix.",
            "/admin/master",
        )]
    return _result(
        "unassigned_titles", "Titles with no project",
        "The original import predates projects. Everything treats these as "
        "the default project, so nothing is broken — but the ambiguity should "
        "be resolved before a second niche shares the table.",
        "info", rows, 1 if total else 0,
    )


def check_sheet_columns_all_or_nothing(db: Session, scope: Scope) -> CheckResult:
    """
    Within one project, `search_query` and `marketplace_title` are either on
    EVERY title or on none.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS THE CHECK, AND WHY IT CANNOT BE A TEST
    ════════════════════════════════════════════════════════════════════════
    Both columns are optional and both fall back to the plain title. That is
    what lets an old sheet import untouched — and it is also what makes them
    dangerous, because a sheet that MEANT to carry them and lost them along
    the way fails completely silently. A renamed header, a column dropped
    while editing, a re-export that quietly changed the name: every title
    then searches on the bare name and lists under the worker's name, no
    error anywhere, and the first sign is the listing checker reporting
    healthy listings as missing weeks later.

    A test cannot catch it — the code is correct either way, and a test would
    be written by whoever chose the column names. The state is what is wrong,
    so the state is what gets watched.

    HALF a project filled is the tell. Nobody produces a sheet where some
    rows have a search query and some do not; that only happens when the
    import or the sheet went wrong.
    """
    # Grouped by project because "all or nothing" is a statement about ONE
    # sheet, and one sheet is one project. Asked per project even when the
    # run is scoped, so the query shape does not change between the two.
    totals = dict(
        db.query(MasterTitle.project_id, func.count(MasterTitle.id))
          .filter(scope.titles)
          .group_by(MasterTitle.project_id).all()
    )

    def filled_per_project(column) -> dict:
        return dict(
            db.query(MasterTitle.project_id, func.count(MasterTitle.id))
              .filter(scope.titles, column.isnot(None), column != "")
              .group_by(MasterTitle.project_id).all()
        )

    have_search = filled_per_project(MasterTitle.search_query)
    have_listing = filled_per_project(MasterTitle.marketplace_title)

    rows = []
    for project_id, total in totals.items():
        total = int(total or 0)
        if not total:
            continue
        name = scope.label(project_id) or "this project"
        for label, filled, column in (
            ("search query", int(have_search.get(project_id, 0)),
             "search_query"),
            ("marketplace title", int(have_listing.get(project_id, 0)),
             "marketplace_title"),
        ):
            if filled == 0 or filled == total:
                continue        # all or nothing — the two healthy states
            rows.append(Finding(
                f"{name}: {filled:,} of {total:,} titles have a {label}",
                f"Every title in one project should carry this column or "
                f"none of them should. A partial fill means the sheet lost "
                f"the column part-way, or two sheets were imported with "
                f"different headers. The {total - filled:,} without it will "
                f"quietly fall back to the plain title — no error, wrong "
                f"listing name. Re-import the sheet with the "
                f"`{column}` column present on every row.",
                "/admin/master",
            ))

    return _result(
        "sheet_columns_all_or_nothing",
        "Sheet columns filled on every title or none",
        "The search query and the marketplace name are optional and fall "
        "back to the title. That fallback is what makes a missing column "
        "invisible, so a half-filled project is the only sign there is.",
        "attention", rows, len(rows),
    )


def check_search_phrasings_name_a_place(db: Session, scope: Scope) -> CheckResult:
    """
    Every configured search phrasing must contain {title}.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS WATCHED AS WELL AS REFUSED
    ════════════════════════════════════════════════════════════════════════
    `pipeline.set_setting()` already refuses a phrasing with no placeholder,
    so this state should be impossible. It is watched anyway because the
    refusal is new and the settings table is old: a value written before the
    guard existed, or typed straight into the database, walks past it.

    What the bad state costs is the reason it is worth two rungs. A phrasing
    with no {title} does not fail. It searches the same literal words for
    every place in the catalogue and returns a full grid of real, plausible
    photographs — of somewhere else. The worker sees nothing wrong, saves
    one, and it is paid for, processed, and listed under a place name it has
    nothing to do with. The first person to notice would be a customer.

    Read from the settings the way the app reads them, per project, so an
    override that only exists on one niche is seen exactly as that niche's
    workers would see it.
    """
    from .pipeline import SEARCH_QUERY_KEYS, get_setting

    projects = db.query(Project).filter(Project.is_active == 1)
    if scope.project_id:
        projects = projects.filter(Project.id == scope.project_id)

    rows = []
    for project in projects.all():
        # An external project sends its workers to another site, so its
        # Brave settings govern nothing and a stray value there is clutter,
        # not a fault.
        if project.search_mode != "inpage":
            continue
        for key in SEARCH_QUERY_KEYS:
            raw = str(get_setting(db, key, project=project) or "")
            for line_no, line in enumerate(raw.splitlines(), 1):
                line = line.strip()
                if not line or "{title}" in line or "{artist}" in line:
                    continue
                rows.append(Finding(
                    f"{project.name}: {key} line {line_no} does not say which "
                    f"place to look for — {line!r}",
                    "This phrasing has no {title} in it, so every title in "
                    "the project searches for these same words and brings "
                    "back photographs of somewhere else. Nothing errors and "
                    "the grid looks normal, so nobody would notice. Fix it "
                    "on the Pipeline page under IMAGE SEARCH — for example "
                    "'places to visit in {title}'.",
                    "/admin/pipeline/settings#search",
                ))

    return _result(
        "search_phrasings_name_a_place",
        "Every search phrasing says which place to look for",
        "A phrasing without {title} searches the same words for every title "
        "and returns convincing photographs of the wrong place, with no "
        "error anywhere.",
        "error", rows, len(rows),
    )


def check_prompt_matches_style_toggle(db: Session, scope: Scope) -> CheckResult:
    """
    The prompt and the style-reference switch must agree about how many
    pictures are being sent.

    ════════════════════════════════════════════════════════════════════════
    WHY A HINT RATHER THAN A REFUSAL
    ════════════════════════════════════════════════════════════════════════
    A prompt is prose. Whether it depends on a reference image cannot be
    decided by reading it, so this cannot be a rule that blocks a save —
    that would be a check pretending to know something it does not.

    What it CAN do is spot the two obvious disagreements, and those are the
    ones that cost money:

      · the switch is OFF while the prompt talks about "the first image" —
        there is no first image, so the model reads the instruction against
        the photo itself and produces something nobody asked for
      · the switch is ON while the prompt never mentions a second image —
        a reference is sent that the prompt gives no job to, and the model
        blends two looks

    Neither one fails. Both produce a finished picture, charged for, that
    goes to the review gate looking like an ordinary bad result. Across a
    batch that is real money, and the cause is a switch nobody looked at.

    Deliberately worth checking, never certain: the wording says "worth a
    look", because a prompt could legitimately mention an image in passing.
    """
    from .pipeline import get_setting

    projects = db.query(Project).filter(Project.is_active == 1)
    if scope.project_id:
        projects = projects.filter(Project.id == scope.project_id)

    # Phrases that only make sense when two pictures are sent. Matched on the
    # WORDS a person would write, not on a single token like "image", which
    # appears in almost every prompt ever written about images.
    TWO_PICTURE_WORDS = ("first image", "second image", "1st image",
                         "2nd image", "other image", "reference image")

    rows = []
    for project in projects.all():
        if project.processor != "gpt":
            continue
        prompt = str(get_setting(db, "openai_prompt", project=project) or "")
        uses_style = bool(get_setting(db, "openai_use_style_image", project=project))
        mentions_two = any(w in prompt.lower() for w in TWO_PICTURE_WORDS)

        if mentions_two and not uses_style:
            rows.append(Finding(
                f"{project.name}: the prompt talks about more than one "
                f"picture, but the style reference is switched OFF",
                "Only the worker's photo is being sent, so there is no "
                "'first image' for the prompt to point at. The model will "
                "still produce a picture and you will still be charged for "
                "it. Either switch 'Send the style reference image' back on, "
                "or reword the prompt to describe the look in words.",
                "/admin/pipeline/settings#processing",
            ))
        elif uses_style and not mentions_two:
            rows.append(Finding(
                f"{project.name}: the style reference is switched ON, but "
                f"the prompt never mentions a second picture",
                "The reference is being sent with every request and the "
                "prompt gives it no job, so the model blends two looks and "
                "the result drifts from what the prompt asks for. Either "
                "switch the reference off, or say in the prompt what the "
                "first image is for. Worth a look rather than certainly "
                "wrong — a prompt can refer to the reference in other words.",
                "/admin/pipeline/settings#processing",
            ))

    return _result(
        "prompt_matches_style_toggle",
        "The prompt and the style-reference switch agree",
        "Sending a reference the prompt ignores, or writing a prompt about "
        "an image that is not being sent, produces a paid-for picture that "
        "is quietly wrong rather than an error.",
        "attention", rows, len(rows),
    )


def check_approved_images_have_a_background(db: Session, scope: Scope) -> CheckResult:
    """
    Every APPROVED image made from a transparent generation has a colour.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS WORTH WATCHING WHEN THE STATE IS ALREADY MEANT TO BE SAFE
    ════════════════════════════════════════════════════════════════════════
    The order is supposed to make it impossible: approving is what records
    the colour, and only approved images are released for upload. So an
    approved image without one should not exist.

    It is watched anyway because of HOW it would fail. Nothing errors, no
    upload is refused, no screen turns red — the picture simply ships on
    whatever colour it happened to have, and a semi-transparent sky that
    should have been blue goes to the marketplace looking muddy. It is a
    money defect that looks like a taste defect, and the owner would find it
    by noticing that a poster he liked on screen sells nothing.

    A single row here means someone has added a second way to approve an
    image and it does not set the colour. That is exactly the change nobody
    would think to test.
    """
    rows = []
    q = (db.query(ProcessedImage, MasterTitle)
           .join(SavedPoster, ProcessedImage.saved_poster_id == SavedPoster.id)
           .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
           .filter(scope.titles,
                   ProcessedImage.review_status == "approved",
                   ProcessedImage.master_path.isnot(None),
                   or_(ProcessedImage.background_color.is_(None),
                       ProcessedImage.background_color == "")))
    for processed, title in q.limit(200).all():
        rows.append(Finding(
            f"{title.title}: approved with no background colour recorded",
            "This picture came back from the model with see-through areas, "
            "and nothing says what colour was put behind them. It will "
            "upload on whatever it was last flattened onto, which for a "
            "semi-transparent sky usually means it looks muddy. Send it "
            "back through Approve Artwork and set a colour.",
            "/admin/pipeline/review",
        ))

    return _result(
        "approved_images_have_a_background",
        "Approved artwork says what colour is behind it",
        "A transparent generation must be flattened onto a chosen colour "
        "before it is listed. Nothing errors if it is not — the poster just "
        "ships looking wrong.",
        "error", rows, len(rows),
    )


def check_projects_match_registry(db: Session, scope: Scope) -> CheckResult:
    """
    Every ACTIVE project must be one the code still declares.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS
    ════════════════════════════════════════════════════════════════════════
    On 2026-09-01 the registry was stripped to a single travel project, and
    the deleted movie project went on appearing on the dashboard, in the
    project switcher, and as somewhere a worker could stand. `sync_projects()`
    only ever created and updated, so removing a spec did nothing whatever to
    a database that already had the row.

    The OWNER found it, by opening the app and seeing a niche he had deleted.
    Nothing mechanical could have: preflight reads source and proves nothing
    is disconnected, and the registry and the database agreeing is a fact
    about DATA, which only a check against the live database can see.

    `sync_projects()` now switches undeclared projects off, so this should
    never fire. That is exactly why it is worth having — it is the invariant
    watching the fix, and it costs one query.
    """
    if not scope.all_projects:
        return _skipped(
            "projects_match_registry", "Projects match the code",
            "Only checked across all projects — this asks whether the whole "
            "registry and the database agree, which is not a question about "
            "any single project.")

    from .pipeline import PROJECT_DEFS

    declared = {spec["slug"] for spec in PROJECT_DEFS}
    stray = [
        p for p in db.query(Project).filter(Project.is_active == 1).all()
        if p.slug not in declared
    ]
    rows = [
        Finding(
            f"'{p.name}' ({p.slug}) is active but not declared in the code",
            "It is visible on the dashboard and a worker can stand in it, "
            "yet PROJECT_DEFS no longer lists it. Startup is supposed to "
            "switch these off, so seeing one means that did not run or did "
            "not finish. Its rows are safe either way.",
            "/admin",
        )
        for p in stray
    ]
    return _result(
        "projects_match_registry", "Projects match the code",
        "Projects are declared in code and reconciled on every boot. An "
        "active project the code has never heard of is one that was deleted "
        "from the registry without the database being told.",
        "warn", rows, len(stray),
    )


def check_claims_by_inactive(db: Session, scope: Scope) -> CheckResult:
    """Titles held by a worker who can no longer log in."""
    q = (
        db.query(MasterTitle, User)
          .join(User, MasterTitle.claimed_by_id == User.id)
          .filter(MasterTitle.status == "in_progress",
                  ((User.is_active == 0) | (User.is_deleted == 1)),
                  scope.titles)
    )
    total = q.count()
    rows = [
        Finding(scope.title_of(mt),
                f"held by {u.username} "
                f"({'deleted' if u.is_deleted else 'disabled'})",
                "/admin/users",
                project=scope.label(mt.project_id))
        for mt, u in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "claims_by_inactive", "Titles held by disabled or deleted workers",
        "Nobody can work on these and nobody else can claim them — they are "
        "stuck out of the queue. Release them from the Users page.",
        "warn", rows, total,
    )


def check_upload_accounts(db: Session, scope: Scope) -> CheckResult:
    """Marketplace accounts that can't actually be used."""
    q = db.query(UploadAccount)
    if scope.project_id:
        # Through the link table: an account shared with another niche must
        # still be checked here, and an earn-only account must not appear in
        # any project's scan.
        q = q.join(AccountProject,
                   AccountProject.account_id == UploadAccount.id
                   ).filter(AccountProject.project_id == scope.project_id)

    rows: list[Finding] = []
    for acct in q.all():
        # A banned account is SUPPOSED to be unusable. Reporting it here
        # every scan would be a permanent false alarm, and a list that always
        # has something in it stops being read.
        if acct.banned_at is not None:
            continue
        problems = []
        if not acct.password_enc:
            problems.append("no stored password")
        if not acct.profile_url:
            problems.append("no profile URL")
        if not acct.daily_limit:
            problems.append("daily limit is 0")
        if problems:
            rows.append(Finding(f"{acct.name} ({acct.email})",
                                ", ".join(problems), "/admin/pipeline",
                                project=scope.label(scope.project_id)))
    return _result(
        "upload_accounts", "Marketplace accounts that can't be used",
        "The uploader will skip these. If an account is meant to be idle, "
        "disable it instead so it doesn't show up here every time. Banned "
        "accounts are excluded — being unusable is the point of them.",
        "warn", rows,
    )


def check_orphaned_bans(db: Session, scope: Scope) -> CheckResult:
    """Banned accounts whose catalogue was never rebuilt anywhere."""
    q = db.query(UploadAccount).filter(UploadAccount.banned_at.isnot(None),
                                       UploadAccount.replaced_by_id.is_(None))
    if scope.project_id:
        q = q.join(AccountProject,
                   AccountProject.account_id == UploadAccount.id
                   ).filter(AccountProject.project_id == scope.project_id)

    rows = []
    for acct in q.all():
        lost = (
            db.query(func.count(UploadTracking.id))
              .filter(UploadTracking.account_id == acct.id,
                      UploadTracking.status == "removed")
              .scalar() or 0
        )
        rows.append(Finding(
            f"{acct.name} — {lost} listing(s) not rebuilt",
            f"banned {acct.banned_at:%Y-%m-%d}: {acct.banned_reason or 'no reason recorded'}",
            "/admin/pipeline/settings#upload",
            project=scope.label(scope.project_id)))

    return _result(
        "orphaned_bans", "Banned accounts whose work was never re-listed",
        "These accounts were closed by the marketplace and their listings "
        "went with them. The images are still in the archive and still cost "
        "you money to make — until they are handed over to a replacement "
        "account they are earning nothing. Use HAND OVER TO… on the Upload "
        "tab.",
        "error", rows,
    )


def check_duplicate_accounts(db: Session, scope: Scope) -> CheckResult:
    """
    Two accounts on one marketplace that are really the same account.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS
    ════════════════════════════════════════════════════════════════════════
    On 2026-08-25 the history import created a SECOND 'GoldenR T' row on
    FineArtAmerica, because it looked accounts up by the dead legacy
    `project_id` column and the real one — which uses the `account_projects`
    link table and leaves that column NULL — was invisible to it. The
    duplicate carried an invented email address, no artist name, and became
    the only account linked to the movie project. Every nightly earnings read
    then tried to sign in as `unknown@example.com` and failed.

    The import bug is fixed. This check exists because NOTHING WOULD HAVE
    FOUND IT: the owner spotted it on the Earnings page two days later. Both
    rows were individually valid, so no invariant over a single account could
    disagree with anything. Only comparing accounts WITH EACH OTHER shows it.

    Matched on name as well as email, because the two rows of that incident
    did not share an address — inventing one is exactly what went wrong.
    """
    rows = []
    accounts = db.query(UploadAccount).all()

    by_email: dict[tuple, list] = {}
    by_name: dict[tuple, list] = {}
    for a in accounts:
        if a.email:
            by_email.setdefault((a.target_site, a.email.strip().lower()), []).append(a)
        if a.name:
            by_name.setdefault((a.target_site, a.name.strip().lower()), []).append(a)

    seen: set[tuple[int, ...]] = set()
    for (site, email), group in sorted(by_email.items()):
        if len(group) > 1:
            key = tuple(sorted(a.id for a in group))
            seen.add(key)
            rows.append(Finding(
                f"{site}: {len(group)} accounts share {email}",
                f"ids {', '.join(str(a.id) for a in group)} — "
                f"named {', '.join(repr(a.name) for a in group)}",
                "/admin/pipeline/settings#upload"))

    for (site, name), group in sorted(by_name.items()):
        if len(group) > 1 and tuple(sorted(a.id for a in group)) not in seen:
            rows.append(Finding(
                f"{site}: {len(group)} accounts are called '{group[0].name}'",
                " · ".join(f"id={a.id} {a.email or 'no address'}"
                           for a in group),
                "/admin/pipeline/settings#upload"))

    return _result(
        "duplicate_accounts", "The same marketplace account entered twice",
        "One real account should be one row here, however many projects it "
        "serves. Two rows means a daily upload limit the marketplace applies "
        "ONCE gets counted as two, earnings get split across both, and "
        "whichever row holds the wrong password fails every night. Check "
        "which row holds the real address and the artist name, move any "
        "project links onto it, and delete the other.",
        "error", rows,
    )


def check_account_never_read(db: Session, scope: Scope) -> CheckResult:
    """
    An account that has never once been read for earnings since it was made.

    A read that fails today is ordinary — a wall, a dropped session, a site
    having a moment — and the existing cooldown handles it. An account that
    has NEVER succeeded is a different thing entirely: it means the row is
    wrong, not the network. Wrong address, wrong password, or a row nobody
    meant to create.

    Kept separate from "the last read failed" for exactly the reason this
    codebase keeps relearning: two failures that look alike but mean
    different things need two answers. One is weather; this one is a defect.

    Deliberately quiet for the first day, so a genuinely new account you have
    just typed in does not shout before its first scheduled read.
    """
    cutoff = datetime.utcnow() - timedelta(days=1)
    rows = []
    for a in db.query(UploadAccount).filter(
            UploadAccount.banned_at.is_(None)).all():
        if a.created_at and a.created_at > cutoff:
            continue
        ledger = (db.query(func.count(LedgerEntry.id))
                    .filter(LedgerEntry.account_id == a.id).scalar() or 0)
        snaps = (db.query(func.count(MarketplaceSnapshot.id))
                   .filter(MarketplaceSnapshot.account_id == a.id).scalar() or 0)
        if ledger or snaps:
            continue
        rows.append(Finding(
            f"{a.name} ({a.target_site}) has never been read",
            f"created {a.created_at:%Y-%m-%d} · {a.email or 'no address'}",
            "/admin/earnings"))

    return _result(
        "account_never_read", "Accounts that have never reported any money",
        "A read failing today is normal and sorts itself out. An account "
        "that has never succeeded ONCE since it was created is telling you "
        "the row itself is wrong — wrong address, wrong password, or a row "
        "that was created by accident. Nothing else notices, because a "
        "failure that happens every single time looks the same as a failure "
        "that happened once.",
        "error", rows,
    )


def check_upload_project_starved(db: Session, scope: Scope) -> CheckResult:
    """
    An account serving two projects that only ever uploads one of them.

    ════════════════════════════════════════════════════════════════════════
    WHY
    ════════════════════════════════════════════════════════════════════════
    `claim_upload_batch` picks which of an account's projects gets its turn.
    Until 2026-08-27 it took the FIRST one with any work — the comment said
    "whichever has waited longest", but the query it read had no ordering,
    so in practice the project attached first won every time.

    An account serving two projects where one always has work then never
    uploads the other's work AT ALL. The movie project carries a backlog of
    roughly three thousand posters, so "always has work" is its normal
    state, and one FineArtAmerica account is meant to serve both niches
    after migration. Reproduced against a real database before fixing.

    The picker is fixed. This watches for the SYMPTOM rather than the cause,
    because the symptom is what costs money and it would show up whatever
    the reason — a paused project, a quota rule, a future change to the
    rotation. Anything that makes one niche silently stop uploading through
    a shared account looks like this.
    """
    from datetime import timedelta

    cutoff = datetime.utcnow() - timedelta(days=7)
    rows = []

    for account in db.query(UploadAccount).filter(
            UploadAccount.is_enabled == 1).all():
        links = (db.query(AccountProject.project_id)
                   .filter(AccountProject.account_id == account.id).all())
        pids = [p for (p,) in links]
        if len(pids) < 2:
            continue                       # one project cannot starve another

        waiting, served = [], []
        for pid in pids:
            queued = (db.query(func.count(UploadTracking.id))
                        .filter(UploadTracking.account_id == account.id,
                                UploadTracking.project_id == pid,
                                UploadTracking.status.in_(("pending", "failed")))
                        .scalar() or 0)
            recent = (db.query(func.count(UploadTracking.id))
                        .filter(UploadTracking.account_id == account.id,
                                UploadTracking.project_id == pid,
                                UploadTracking.uploaded_at.isnot(None),
                                UploadTracking.uploaded_at >= cutoff)
                        .scalar() or 0)
            if queued and not recent:
                waiting.append((pid, queued))
            if recent:
                served.append((pid, recent))

        # Only interesting when one project is being served and another is
        # not. Both idle is a quiet week; both busy is working correctly.
        if waiting and served:
            for pid, queued in waiting:
                name = scope.label(pid) or f"project {pid}"
                other = ", ".join(f"{scope.label(p) or p} ({n})" for p, n in served)
                rows.append(Finding(
                    f"{account.name}: {name} has {queued} upload(s) waiting "
                    f"and none sent in 7 days",
                    f"the same account uploaded for {other} in that time",
                    "/admin/pipeline/settings#upload"))

    return _result(
        "upload_project_starved", "One niche not uploading through a shared account",
        "This account serves more than one project. One of them has work "
        "queued and has sent nothing for a week, while another has been "
        "uploading through the same account. That is what it looks like "
        "when the rotation always picks the same project — the queued work "
        "is never wrong, it is simply never reached.",
        "error", rows,
    )


def check_unclassified_ledger_rows(db: Session, scope: Scope) -> CheckResult:
    """
    Money rows we stored but could not name.

    ════════════════════════════════════════════════════════════════════════
    WHY
    ════════════════════════════════════════════════════════════════════════
    `classify()` maps a marketplace's Type column onto sale / payment /
    refund, and anything it does not recognise becomes 'other'. Keeping the
    row was the right call — an unknown row we can see beats a wrong one we
    cannot — but everything downstream selects BY TYPE, so an 'other' row
    was counted nowhere.

    MEASURED 2026-08-27: one row did exactly that. FineArtAmerica recorded a
    $6.00 debit against "Highlander - 1986 A - T-Shirt - Navy - Medium" with
    a word `classify()` does not know. It fell out of the balance, the
    Earnings page reported GoldenR T as $6.00 short, and it said rows were
    MISSING and to press READ NOW — a diagnosis that was wrong, for a row
    that was already stored.

    The arithmetic is fixed: totals now sum credit minus debit across every
    row, so an unnameable row still lands in the right place. What is still
    lost is the LABEL — such a row is absent from REFUNDED and from WHAT
    SOLD, and nothing would say so.

    That is what this reports. It is not an error, it is a gap in our
    vocabulary, and the fix is one word added to `classify()` — which needs
    `raw_type`, now stored for exactly this reason.
    """
    rows = []
    try:
        entries = (db.query(LedgerEntry)
                     .filter(LedgerEntry.entry_type == "other")
                     .order_by(LedgerEntry.occurred_at.desc())
                     .limit(50).all())
    except Exception as e:
        return _result(
            "unclassified_ledger", "Money rows we could not name",
            "This check could not run.", "warn",
            [Finding("The check itself failed", str(e), "/admin/earnings")])

    for r in entries:
        amount = (r.debit or "0").strip() or "0"
        if amount in ("0", "0.0", "0.00"):
            amount = (r.credit or "0").strip() or "0"
        if amount in ("0", "0.0", "0.00"):
            continue                      # a zero row costs nothing either way
        rows.append(Finding(
            f"{r.marketplace}: ${amount} on {r.occurred_at:%Y-%m-%d} "
            f"is of a kind we do not recognise",
            (f"they called it {r.raw_type!r} · " if getattr(r, "raw_type", None)
             else "their word for it was not recorded · ")
            + (r.description or "")[:70],
            "/admin/earnings"))

    return _result(
        "unclassified_ledger", "Money rows we could not name",
        "The amount is counted correctly in the balance — totals add up "
        "credits minus debits across every row, whatever it is called. But "
        "a row we cannot name is left out of REFUNDED and WHAT SOLD, "
        "because those pick rows by type. Tell Claude the word the "
        "marketplace uses and it becomes one line in classify().",
        "warn", rows,
    )


def check_earnings_retry_gave_up(db: Session, scope: Scope) -> CheckResult:
    """
    Accounts today's read never managed to read, after retrying stopped.

    ════════════════════════════════════════════════════════════════════════
    WHY A MECHANISM THAT GIVES UP QUIETLY NEEDS A WATCHER
    ════════════════════════════════════════════════════════════════════════
    The retry is deliberately bounded: a few hours after the scheduled read
    it stops trying until tomorrow, so a marketplace that is refusing us is
    not knocked on all night. That is the right behaviour and it has one
    cost — when it gives up, nothing says so. The screen looks the same as a
    day where every account reported zero.

    For a snapshot marketplace that is not a cosmetic difference. TeePublic
    publishes a running total and nothing else, so a day with no reading is
    merged into the next one and the two can never be separated afterwards.
    A week of silent give-ups is a week of figures that cannot be rebuilt
    from anything.

    Only fires once the window has closed, so an account still waiting for
    its cooldown is not reported as a failure while it is working normally.
    """
    from .earnings import service as earnings

    try:
        state = earnings.retry_state(db)
    except Exception as e:
        # A watcher that throws is a watcher that is not watching. Say so
        # rather than letting the whole Diagnostics page fail.
        return _result(
            "earnings_retry_gave_up", "Accounts today's earnings read never got",
            "This check could not run, so nothing is watching the earnings "
            "retry right now.", "error",
            [Finding("The check itself failed", str(e), "/admin/earnings")])

    rows = [
        Finding(f"{e['name']} ({e['site']}) was not read today",
                e["reason"] or "no reason recorded",
                "/admin/earnings")
        for e in state.get("gave_up", [])
    ]

    return _result(
        "earnings_retry_gave_up", "Accounts today's earnings read never got",
        "The nightly read tried these and gave up for the day. On a site "
        "that only publishes a running total, today's earnings are now "
        "merged into tomorrow's figure and cannot be separated again. Press "
        "READ NOW if the cause has passed, or open the account's Chrome "
        "profile and sign in by hand if it says signed out.",
        "warn", rows,
    )


def check_orphaned_upload_rows(db: Session, scope: Scope) -> CheckResult:
    """
    Queued uploads whose marketplace account no longer exists.

    Work is only ever handed out by walking the list of live accounts, so a
    row pointing at a deleted account can never be claimed by anything. The
    design sits at 'uploading' on the funnel and waits forever.

    Deleting an account now releases these, so this should only ever find
    rows created before that fix. It stays because the state is cheap to
    detect and impossible to notice by eye.
    """
    live = {i for (i,) in db.query(UploadAccount.id).all()}
    q = (
        db.query(UploadTracking, MasterTitle.title)
          .join(SavedPoster, UploadTracking.saved_poster_id == SavedPoster.id)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(UploadTracking.status.in_(("pending", "uploading", "failed")))
    )
    if scope.project_id:
        q = q.filter(UploadTracking.project_id == scope.project_id)

    rows = [
        Finding(title or f"poster {t.saved_poster_id}",
                f"queued against account #{t.account_id}, which no longer exists",
                "/admin/pipeline/settings#upload",
                project=scope.label(t.project_id))
        for t, title in q.all() if t.account_id not in live
    ]

    return _result(
        "orphaned_upload_rows", "Uploads queued against a deleted account",
        "These images were waiting to go to a marketplace account that has "
        "since been deleted, so nothing will ever pick them up. Add the "
        "replacement account and press REQUEUE BACK CATALOGUE on the Upload "
        "tab to put them back in the queue.",
        "error", rows,
    )


def check_open_revisions_on_deleted(db: Session, scope: Scope) -> CheckResult:
    """Change requests still open against a poster that's already gone."""
    q = (
        db.query(Revision, SavedPoster)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .filter(Revision.status.in_(("open", "awaiting_approval")),
                  SavedPoster.deleted_at.isnot(None),
                  scope.posters)
    )
    total = q.count()
    rows = [
        Finding(f"change request #{rev.id} on deleted #{sp.id}",
                rev.comment or "", "/admin/revisions",
                project=scope.label(_project_of(db, sp)))
        for rev, sp in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "revisions_on_deleted", "Change requests on deleted work",
        "The worker can never resolve these — the file is gone. They also "
        "block payment for it indefinitely. Close them from the Changes "
        "Requested page.",
        "error", rows, total,
    )


def check_duplicate_hashes(db: Session, scope: Scope) -> CheckResult:
    """The identical file saved more than once."""
    dupes = (
        db.query(SavedPoster.content_hash, func.count(SavedPoster.id))
          .filter(SavedPoster.content_hash.isnot(None),
                  SavedPoster.deleted_at.is_(None),
                  scope.posters)
          .group_by(SavedPoster.content_hash)
          .having(func.count(SavedPoster.id) > 1)
          .limit(MAX_ROWS)
          .all()
    )
    rows = []
    for h, n in dupes:
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.content_hash == h,
                      SavedPoster.deleted_at.is_(None),
                      scope.posters)
              .limit(6)
              .all()
        )
        rows.append(Finding(
            f"{n} identical copies",
            " · ".join(f"#{p.id} {p.username}/{p.filename}" for p in posters),
        ))
    return _result(
        "duplicate_hashes", "Byte-identical files saved more than once",
        "The same image saved twice — sometimes legitimately (two titles "
        "sharing artwork), sometimes a worker saving the same file twice and "
        "being paid twice. Worth a look when the count is high.",
        "info", rows,
    )




# ════════════════════════════════════════════════════════════════════════════
#  LISTING HEALTH — INVARIANTS
# ════════════════════════════════════════════════════════════════════════════
#
# These assert about STATE, not about flow, and that is the whole point.
#
# A design switched off and never switched back on is a live listing earning
# nothing. It happened for real on 2026-08-24: a deactivate stage ended when
# the FIRST of two accounts reported, the run moved on, and 178 designs were
# left off with nothing on any screen saying so. It was noticed by eye,
# because a number on the page was going up instead of down.
#
# The bug is fixed. These checks exist because the NEXT one of that shape
# will be different, and none of them require anyone to have imagined it:
# they simply ask whether something that must be true still is.
#
# Marketplace-level, so they ignore `scope` — a design belongs to an ACCOUNT,
# and an account may serve several projects or none.

def check_unpayable_but_counted(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a poster shown as UNPAID must be one that can actually be paid.

    ════════════════════════════════════════════════════════════════════════
    THE INCIDENT, 2026-08-25
    ════════════════════════════════════════════════════════════════════════
    The Payments screen counted "21 unpaid posters from previous days" and
    one of them — saved 2026-05-18 — could never be paid, because the OWNER
    had added it himself and admin-added posters are deliberately excluded
    from pay. Clicking it totalled nothing. It had been doing that for
    months and the only symptom was a number that would not go away.

    Two queries carried their own copies of "what counts", and one had an
    exclusion the other lacked. `payments.payable_criteria` is now the single
    definition, which makes the mismatch impossible rather than detectable.

    This check remains because the failure is about STATE — a poster in one
    list and not the other — so it holds for any future divergence, including
    one that arrives by a route nobody has thought of. And because the thing
    it protects is money owed to a person, which is the last place to rely on
    somebody remembering a rule.
    """
    from .payments import payable_criteria
    from .models import Revision

    rows = []
    for worker in db.query(User).filter(User.role == "worker").all():
        payable = {r[0] for r in db.query(SavedPoster.id).filter(
            *payable_criteria(worker.id)).all()}
        # Everything the OLD, looser rule would have counted.
        counted = {r[0] for r in db.query(SavedPoster.id).filter(
            SavedPoster.user_id == worker.id,
            SavedPoster.deleted_at.is_(None)).all()}
        stuck = counted - payable
        if not stuck:
            continue
        rows.append(Finding(
            f"{worker.username}: {len(stuck)} poster(s) can never be paid",
            "added by an admin, so excluded from pay — they must not appear "
            "in any unpaid total",
            "/admin/payments",
        ))

    return _result(
        "unpayable_but_counted", "Posters counted as unpaid that cannot be paid",
        "These posters were added by an admin rather than saved by the "
        "worker, so the payment run will never include them. If a screen is "
        "showing them as owed, that number can never reach zero.",
        "warn", rows,
    )


def check_listing_sweep_believable(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a listing check that found almost nothing is a broken check.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS AN INVARIANT AND NOT A NICETY
    ════════════════════════════════════════════════════════════════════════
    Every listing address is built from an artist name typed in by hand. One
    wrong character and EVERY listing on that account returns 404 — and the
    screen would report thousands of copyright takedowns, confidently, with
    nothing to suggest it was nonsense. The owner cannot read the database;
    a confidently wrong screen is the most expensive thing this system can
    produce.

    So the claim "most of an account's catalogue has vanished" has to earn
    itself. Stated about STATE — what the rows now say — rather than about
    the sweep that produced them, so it holds however they got that way,
    including a route nobody has thought of.

    It deliberately does not assert which explanation is right. An account
    really can lose everything: that is what a ban looks like. Opening one
    address settles it in ten seconds, so the finding says to do that.

    The sweep itself already stops when this trips, so a finding here means
    either that guard was bypassed or the rows were left behind by an older
    sweep. Both are worth seeing.
    """
    from . import listing_check as LC
    from .models import UploadTracking

    rows = []
    for account in LC.ready(db)[0]:
        checked = (db.query(UploadTracking)
                     .filter(UploadTracking.account_id == account.id,
                             UploadTracking.listing_checked_at.isnot(None))
                     .all())
        if len(checked) < 20:
            continue
        # NO_PAGE, not gone. A 410 means the listing really was removed and
        # every one of those is true — an account emptied by a ban would be
        # all 410s. A 404 means no page ever existed at that address, and a
        # whole account of those means we are building addresses nobody
        # could have visited.
        gone = sum(1 for r in checked if r.listing_status == "no_page")
        if gone / len(checked) < 0.5:
            continue
        rows.append(Finding(
            f"{account.name}: {gone} of {len(checked)} addresses have NO PAGE",
            f"artist name on file is '{account.artist_name}'",
            "/admin/listings",
        ))

    return _result(
        "listing_sweep_believable", "A listing check nobody should believe",
        "The marketplace says no page has EVER existed at these addresses — "
        "which is different from saying the listings were removed, and it "
        "distinguishes the two. So this is almost certainly the artist name "
        "being spelled differently there than it is here. Fix it on the "
        "Listing check tab and sweep again before treating any of it as "
        "missing.",
        "warn", rows,
    )


def expected_agent_version() -> Optional[str]:
    """
    What version of the worker agent this release ships.

    READ OUT OF `worker_service/agent.py` rather than copied into a constant
    here. Two copies of one fact are two chances to drift, and the copy that
    breaks is always the newer one, silently — which is precisely the defect
    this whole check exists to catch, so having it in two places would be
    absurd.
    """
    try:
        source = (BASE_DIR / "worker_service" / "agent.py").read_text(
            encoding="utf-8")
    except OSError:
        return None
    found = re.search(r'^AGENT_VERSION\s*=\s*"([^"]+)"', source, re.M)
    return found.group(1) if found else None


def check_worker_agent_current(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: the worker machine runs the agent this release ships.

    ════════════════════════════════════════════════════════════════════════
    A FOLDER COPY HAS NO CONFIRMATION
    ════════════════════════════════════════════════════════════════════════
    Deploying updates the server. The worker machine is updated by copying
    `worker_service\\` across by hand, and nothing anywhere says whether that
    happened. So the machine can quietly keep running last week's code while
    the site shows a fresh version number, and the symptom arrives days
    later as a job failing for a reason that was fixed.

    It bit twice in one week: the folder went two releases without its
    version being bumped, so the number that was supposed to answer this
    question read the same either way.

    The version is what the MACHINE says about itself on every handshake,
    against what this release ships. Same shape as every other check worth
    having — one number from outside our own assumptions.
    """
    expected = expected_agent_version()
    nodes = db.query(WorkerNode).filter(WorkerNode.is_enabled == 1).all()

    if expected is None:
        return _result("worker_agent_current",
                       "Cannot tell which agent version this release ships",
                       "worker_service/agent.py is not readable from the "
                       "server, so there is nothing to compare against.",
                       "warn", [], 0)
    if not nodes:
        return _result("worker_agent_current", "No worker machine is enabled",
                       "Nothing to check. Processing and every upload happen "
                       "on that machine, so this is worth knowing on its "
                       "own.", "warn", [], 0)

    stale = [n for n in nodes if (n.agent_version or "") != expected]
    rows = [Finding(n.name,
                    f"reports {n.agent_version or 'nothing yet'}, "
                    f"this release ships {expected}",
                    "/admin/pipeline")
            for n in stale]
    return _result(
        "worker_agent_current",
        f"{len(stale)} worker machine(s) are running an older agent"
        if stale else f"The worker machine is running agent {expected}",
        "Copy the worker_service folder across again and restart the agent. "
        "Until then that machine is running older code, and a fix you "
        "deployed has not reached the half of the system that does the "
        "Photoshop, the uploading and the marketplace reading.",
        "error" if stale else "ok", rows, len(stale),
    )


def check_two_current_images(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a poster has at most ONE current processed image.

    `is_current` is what the uploader reads to decide which file to send. If
    two rows claim it, which image reaches the marketplace depends on the
    order a query happens to return — so the same poster could list
    differently on two accounts, and nothing would ever say so.

    Cheap to check and it holds against every path that writes one, not
    just the one that made it necessary: reprocessing after a script change,
    the pipeline's own upload, and now the archive index reading the storage
    box. Those last two can race if two walks are started at once, which is
    why only one is allowed to run — this is the net under that.
    """
    rows_q = (db.query(ProcessedImage.saved_poster_id,
                       func.count(ProcessedImage.id).label("n"))
                .filter(ProcessedImage.is_current == 1)
                .group_by(ProcessedImage.saved_poster_id)
                .having(func.count(ProcessedImage.id) > 1))
    found = rows_q.limit(MAX_ROWS).all()
    total = len(rows_q.all())

    rows = [Finding(f"poster #{poster_id}",
                    f"{n} images all marked current", "/admin/pipeline")
            for poster_id, n in found]
    return _result(
        "two_current_images",
        f"{total} poster(s) have more than one current image"
        if total else "Every poster has at most one current image",
        "The uploader picks whichever the database hands it first, so the "
        "same poster could go to two marketplaces looking different. Tell "
        "whoever built the step that created the second one — this state is "
        "supposed to be impossible.",
        "error" if total else "ok", rows, total,
    )


def check_generations_share_a_file(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: two generations of one poster must not point at one file.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS NOT OBVIOUS
    ════════════════════════════════════════════════════════════════════════
    Rerunning a poster kept the old ProcessedImage ROW and set
    `is_current = 0`, with a comment in two places saying the rejected
    picture was "superseded, never deleted". The filename it pointed at had
    no generation number in it, so the rerun wrote the new picture straight
    over the old one. The row survived; the picture did not. Nothing
    disagreed with anything, because our records only ever described
    themselves — the two rows are perfectly consistent and both correct
    about a file that holds one image.

    It only became visible when the owner asked to CHOOSE between
    generations (2026-09-09), at which point picking "v1" would have shown
    him v2. Fixed at the source: `storage_path_for` now puts the generation
    in the name, so v2 lands beside v1 instead of on top of it.

    This is the net under that fix. It is stated about STATE — two rows, one
    path — so it holds whatever future code writes an image, including code
    nobody has thought of yet.

    Rows already in the archive from before the fix will show up here, and
    that reading is honest: those older pictures really are gone.
    """
    dup_q = (db.query(ProcessedImage.saved_poster_id,
                      ProcessedImage.storage_path,
                      func.count(ProcessedImage.id).label("n"))
               # Rows whose print file has not been BUILT yet all carry an
               # empty path, and empty is not a collision — it is the absence
               # of one. Without this every poster waiting for approval would
               # be reported as sharing a file with its own siblings.
               .filter(ProcessedImage.storage_path.isnot(None),
                       ProcessedImage.storage_path != "")
               .group_by(ProcessedImage.saved_poster_id,
                         ProcessedImage.storage_path)
               .having(func.count(ProcessedImage.id) > 1))
    found = dup_q.limit(MAX_ROWS).all()
    total = len(dup_q.all())

    rows = [Finding(f"poster #{poster_id}",
                    f"{n} generations all stored at {path}",
                    "/admin/pipeline/review")
            for poster_id, path, n in found]
    return _result(
        "generations_share_a_file",
        f"{total} poster(s) have generations sharing one file"
        if total else "Every generation has its own file",
        "Each of these posters was generated more than once and every "
        "attempt was written to the same place, so only the newest picture "
        "still exists — the older rows point at it too. Anything from "
        "before 2026-09-09 is history and cannot be recovered; a NEW one "
        "means something started writing images without a generation "
        "number again, which is supposed to be impossible.",
        "warn" if total else "ok", rows, total,
    )


def check_approved_without_a_print_file(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: an APPROVED image must have a print file to upload.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS BECAME POSSIBLE, AND WHY IT IS THE EXPENSIVE ONE
    ════════════════════════════════════════════════════════════════════════
    Until 2026-09-09 the 4000-pixel print file was made the moment a poster
    was painted, so an approved row always had one. Now, for a project with
    a review gate, it is built at APPROVAL — with the colour and the
    signature in a single encode, which is less total work and better
    quality (see `_build_print_file`).

    The cost of that trade is a state that could not exist before: a row
    marked approved, with upload work created for it, and `storage_path`
    still empty because the build failed. The uploader would then hand
    FineArtAmerica a path with nothing behind it.

    The approval endpoint refuses loudly and rolls back if the build fails,
    so this should be impossible. That is a statement about today's code;
    this check holds whatever tomorrow's does, needs no idea of how the
    failure happens, and fires the moment the state exists rather than when
    an upload goes wrong.
    """
    rows_q = (db.query(ProcessedImage)
                .filter(ProcessedImage.review_status == "approved",
                        or_(ProcessedImage.storage_path.is_(None),
                            ProcessedImage.storage_path == "")))
    found = rows_q.limit(MAX_ROWS).all()
    total = len(rows_q.all())

    rows = [Finding(f"poster #{p.saved_poster_id}",
                    f"generation {p.attempt or 1} is approved with no print "
                    f"file recorded", "/admin/pipeline/review")
            for p in found]
    return _result(
        "approved_without_print_file",
        f"{total} approved image(s) have no print file"
        if total else "Every approved image has a print file",
        "These were released for upload but the big file was never built, so "
        "the uploader has nothing to send. Approve them again on the Approve "
        "Artwork screen — that is what builds the file — and tell me if it "
        "fails, because approving is supposed to refuse rather than leave "
        "this behind.",
        "error" if total else "ok", rows, total,
    )


def check_current_image_was_discarded(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: the generation about to be UPLOADED must still have its file.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS THE ONE THAT COSTS MONEY
    ════════════════════════════════════════════════════════════════════════
    Approving an artwork now deletes the pictures of the generations that
    were not chosen (2026-09-09, at the owner's request — four print files
    per poster is not worth the space). `is_current` is what the uploader
    reads to decide which file to send. If those two ever point at the same
    row, the marketplace is handed a picture that has been deleted.

    Nothing in the approve code can do that today: the chosen row is marked
    current and the others discarded, in one place. But that is a statement
    about the code as it is now, and the whole point of an invariant is that
    it holds whatever future code does. This one is cheap, needs no
    knowledge of how a bug would happen, and goes red the moment the state
    exists rather than when an upload fails.

    The companion mechanism — a listing finding the owner has settled — gets
    no check here on purpose. It cannot go stale: the note is stored against
    the observation it answered, so a different answer brings the row back
    by itself. There is no state to drift.
    """
    rows_q = (db.query(ProcessedImage)
                .filter(ProcessedImage.is_current == 1,
                        ProcessedImage.review_status == "discarded"))
    found = rows_q.limit(MAX_ROWS).all()
    total = len(rows_q.all())

    rows = [Finding(f"poster #{p.saved_poster_id}",
                    f"generation {p.attempt or 1} is the current one and its "
                    f"file was deleted ({p.storage_path})",
                    "/admin/pipeline/review")
            for p in found]
    return _result(
        "current_image_was_discarded",
        f"{total} poster(s) point at a picture that was deleted"
        if total else "Every current picture still has its file",
        "The image the uploader would send has had its file removed, which "
        "happens to the generations you did NOT choose. Something has marked "
        "the wrong one as current. Do not upload these until it is sorted — "
        "tell whoever built the step that did it, because this state is "
        "supposed to be impossible.",
        "error" if total else "ok", rows, total,
    )


def check_upload_gap_is_holding(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: with the gap switched on, batches really are that far apart.

    ════════════════════════════════════════════════════════════════════════
    WHY A SWITCHED-ON GUARD NEEDS WATCHING AT ALL
    ════════════════════════════════════════════════════════════════════════
    `brave_daily_query_cap` sat on the Settings page for months describing
    itself as a safety net against a looping bug, and no code anywhere read
    it. It was removed in v172 rather than fixed. The lesson was that a
    control which LOOKS like protection and is not is worse than none, so
    the upload gap ships with something that can tell the owner whether it
    is actually holding.

    ════════════════════════════════════════════════════════════════════════
    IT COMPARES BATCHES, NOT DESIGNS
    ════════════════════════════════════════════════════════════════════════
    Inside one batch the uploads are seconds apart by design, so an
    invariant over individual uploads would fire constantly and be switched
    off within a day. What the gap governs is when a NEW batch may begin, so
    the uploads are first grouped into runs — a fresh run starts wherever
    there is more than half an hour of silence — and consecutive runs are
    what get measured.

    Half an hour is a judgement, not a measurement: an upload takes tens of
    seconds, so a half-hour hole is far longer than any within-batch pause
    and far shorter than the smallest sensible gap. If the owner ever sets
    the gap below an hour this would start reporting nonsense, so it simply
    declines to look in that case rather than inventing findings.
    """
    from .pipeline import get_setting

    rows, total = [], 0
    try:
        on = bool(get_setting(db, "upload_gap_enabled"))
        hours = float(get_setting(db, "upload_gap_hours") or 0)
    except Exception:      # noqa: BLE001
        on, hours = False, 0.0

    # Nothing to assert when the gap is off, and nothing trustworthy to
    # assert when it is shorter than the run-grouping window.
    if on and hours >= 1:
        RUN_GAP = timedelta(minutes=30)
        need = timedelta(hours=hours)
        for account in db.query(UploadAccount).all():
            stamps = [
                t for (t,) in db.query(UploadTracking.uploaded_at)
                .filter(UploadTracking.account_id == account.id,
                        UploadTracking.status == "uploaded",
                        UploadTracking.uploaded_at.isnot(None))
                .order_by(UploadTracking.uploaded_at.asc()).all()
            ]
            starts = [s for i, s in enumerate(stamps)
                      if i == 0 or (s - stamps[i - 1]) > RUN_GAP]
            for i in range(1, len(starts)):
                apart = starts[i] - starts[i - 1]
                if apart < need:
                    total += 1
                    if len(rows) < MAX_ROWS:
                        rows.append(Finding(
                            account.name,
                            f"two batches {round(apart.total_seconds() / 3600, 1)} "
                            f"hours apart on {starts[i]:%Y-%m-%d %H:%M}, with the "
                            f"gap set to {hours}",
                            "/admin/pipeline#upload"))

    return _result(
        "upload_gap_holding",
        f"{total} batch(es) started sooner than the gap allows"
        if total else "The gap between upload batches is holding",
        "You asked for a wait between upload batches, and these went out "
        "closer together than that. Either the wait is not being applied, or "
        "the gap was changed after those uploads happened. The second is "
        "harmless; the first means the marketplace may be seeing more from "
        "you in a day than you intended.",
        "warn", rows, total,
    )


def check_failure_evidence_is_pruned(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: the evidence folders never hold more than the cap.

    Pruning happens when a NEW failure arrives, which is the cheapest place
    for it and the reason it needs watching: if the prune ever stops working
    — a permission problem on the folder, a setting read that throws — the
    folders simply grow again and the only symptom is the page getting
    longer, which is exactly the thing the owner asked to stop.

    Deliberately allows a small overshoot. A prune runs after the newest file
    is written, so the count sits at the cap and never above it; a couple over
    would mean a race rather than a fault, and a check that fires on a race
    trains you to ignore it.
    """
    from .pipeline import get_setting

    try:
        keep = int(get_setting(db, "failure_evidence_keep") or 0)
    except Exception:      # noqa: BLE001
        keep = 0

    rows, total = [], 0
    base = (WORKSPACE_DIR / "_pipeline_artifacts")
    if keep > 0 and base.is_dir():
        for kind_dir in base.iterdir():
            if not kind_dir.is_dir():
                continue
            try:
                n = sum(1 for f in kind_dir.iterdir() if f.is_file())
            except OSError:
                continue
            if n > keep + 2:
                total += 1
                rows.append(Finding(
                    f"{kind_dir.name}: {n} files",
                    f"the cap is {keep}, so the oldest should have been "
                    f"deleted when the newest arrived",
                    "/admin/pipeline#attention"))

    return _result(
        "failure_evidence_pruned",
        f"{total} evidence folder(s) are over the cap"
        if total else "Failure evidence is being tidied up",
        "Old failure screenshots are supposed to delete themselves when a "
        "new one arrives, so the Failure Evidence panel stops growing. These "
        "folders are over the limit, which means the tidying has stopped "
        "working — most likely the server cannot delete in that folder. "
        "Nothing is broken by it; the page just gets longer for ever.",
        "warn", rows, total,
    )


def check_chosen_colour_was_painted(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: once released, the colour CHOSEN is the colour PAINTED.

    ════════════════════════════════════════════════════════════════════════
    WHY THESE ARE TWO COLUMNS, AND WHY THAT NEEDS WATCHING
    ════════════════════════════════════════════════════════════════════════
    `background_chosen` is what the admin picked on the Approve Artwork
    screen and has not released yet. `background_color` is what is actually
    flattened into the print file. They are different facts on purpose:
    `_build_print_file` compares the wanted colour against the painted one to
    decide whether it has any work to do, so writing a mere preference into
    that column would tell the builder the job was already done and the old
    colour would ship in silence.

    Before approval the two are supposed to differ — that is the whole point.
    AFTER approval they must agree, because approving is what paints the
    chosen colour in. A released poster where they still disagree means the
    build skipped the colour, and the file on the marketplace is not the one
    on the screen.

    Nothing in today's code can leave that behind. An invariant is what holds
    whatever tomorrow's code does, and this one costs a single query.
    """
    rows_q = (db.query(ProcessedImage)
                .filter(ProcessedImage.review_status == "approved",
                        ProcessedImage.background_chosen.isnot(None),
                        ProcessedImage.background_chosen != "",
                        func.lower(func.coalesce(ProcessedImage.background_color, ""))
                        != func.lower(ProcessedImage.background_chosen)))
    found = rows_q.limit(MAX_ROWS).all()
    total = rows_q.count()

    rows = [Finding(f"poster #{p.saved_poster_id}",
                    f"you chose {p.background_chosen} and the file was built "
                    f"with {p.background_color or 'nothing'}",
                    "/admin/pipeline/review")
            for p in found]
    return _result(
        "chosen_colour_was_painted",
        f"{total} released image(s) were built with the wrong colour"
        if total else "Every released image was built with the colour you chose",
        "The background you picked was not the one painted into the print "
        "file, so what is on the marketplace does not match what you "
        "approved. Send these titles back to the start and approve them "
        "again, which rebuilds the file — and tell me, because this state is "
        "supposed to be impossible.",
        "error" if total else "ok", rows, total,
    )


def check_recalled_poster_still_painted(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a poster that is not in the pipeline must have no paintings.

    ════════════════════════════════════════════════════════════════════════
    WHAT THIS WATCHES, AND WHY IT NEEDS WATCHING
    ════════════════════════════════════════════════════════════════════════
    SEND TITLES BACK TO THE START (2026-09-09) is the one deliberately
    destructive button in the system. It clears a poster's pipeline status
    back to nothing, deletes every generation's row, and deletes the files
    behind them, so the machine paints the poster again from the worker's
    original photograph.

    Those are three separate deletions and they are not one atomic thing on
    the storage side: the database work rolls back together, but files
    already removed from the archive do not come back. So the state to watch
    for is a poster left looking un-painted while painted rows survive.

    Why that costs something rather than merely looking untidy: greenlighting
    such a poster paints it again and writes generation 1 over a file another
    row still claims — which is the same "two records, one path" family as
    `generations_share_a_file`, arriving by a different door. The version
    picker on Approve Artwork would then offer an old generation and show a
    new picture.

    Nothing in the recall code can leave this behind today. That is a
    statement about today's code, and an invariant is what holds whatever
    tomorrow's does.
    """
    rows_q = (db.query(SavedPoster)
                .filter(SavedPoster.deleted_at.is_(None),
                        SavedPoster.pipeline_status.is_(None),
                        scope.posters,
                        SavedPoster.id.in_(
                            db.query(ProcessedImage.saved_poster_id))))
    found = rows_q.limit(MAX_ROWS).all()
    total = rows_q.count()

    rows = [Finding(f"poster #{p.id}",
                    f"{p.filename} is back at the start but still has "
                    f"painted version(s) recorded",
                    "/admin/pipeline#greenlight")
            for p in found]
    return _result(
        "recalled_poster_still_painted",
        f"{total} poster(s) were sent back but kept their paintings"
        if total else "Every poster sent back to the start was fully cleared",
        "SEND TITLES BACK TO THE START is supposed to delete the painted "
        "versions along with the pipeline status. These kept theirs, so "
        "painting them again would write over a picture another record still "
        "points at. Send the same titles back once more, which redoes the "
        "deletion, and tell me if they stay on this list.",
        "error" if total else "ok", rows, total,
    )


def check_titles_collide_after_folding(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: no two titles in a project fold to the same marketplace name.

    ════════════════════════════════════════════════════════════════════════
    WHY THE COMPARISON IS ON THE FOLDED FORM, NOT THE STORED TEXT
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica silently rewrites titles on save (measured 2026-08-13):
    accents fold to ASCII, punctuation is deleted, length is capped. So
    "Los Angeles" and "Los Ángeles" are DIFFERENT rows here and the SAME
    title there — and a title the account already holds is not refused, it
    is renumbered to "... #2" with no error (measured 2026-09-03). A
    renumbered listing lives at an address we never computed, so every
    listing-check sweep reads it as a 404 for as long as the pair exists.

    The sheet was deduplicated on the RAW string before import. Nothing
    anywhere has ever checked the FOLDED string, and an admin retitling a
    held upload can also create a collision after import. This check is the
    unattended watcher for both doors.

    It reads every title in the project and folds each one in Python, which
    on 88,970 rows costs a few seconds. Diagnostics runs on demand, never on
    a page load, so that is an acceptable price for a question nothing else
    asks.
    """
    from .pipeline import clean_for_marketplace, tidy_separators

    q = db.query(MasterTitle.id, MasterTitle.external_id,
                 MasterTitle.title, MasterTitle.marketplace_title)
    q = q.filter(scope.titles)

    groups: dict[str, list] = {}
    for tid, ext, title, mkt in q.all():
        name = (mkt or title or "").strip()
        folded = tidy_separators(clean_for_marketplace(name))
        if not folded:
            continue          # empty folds are validate_marketplace_title's job
        groups.setdefault(folded.lower(), []).append((ext, name))

    clashes = {k: v for k, v in groups.items() if len(v) > 1}
    rows = []
    for folded, members in sorted(clashes.items())[:MAX_ROWS]:
        listed = " · ".join(f"#{ext} {name!r}" for ext, name in members[:6])
        rows.append(Finding(
            f'all list as "{folded}"',
            f"{len(members)} titles become the same name on the marketplace: {listed}",
            "/admin/titles"))
    return _result(
        "titles_collide_after_folding",
        f"{len(clashes)} marketplace name(s) are shared by more than one title"
        if clashes else
        "Every title still has its own name after the marketplace's rewriting",
        "FineArtAmerica rewrites titles when they are saved, so two titles "
        "that look different here can come out identical there. The second "
        "one to upload gets '#2' stuck on its name with no error, and after "
        "that the listing checker can never find it. Rename one of each "
        "pair before these titles are released into the pipeline.",
        "error" if clashes else "ok", rows, len(clashes),
    )


def check_year_is_a_year_or_nothing(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a title's year is four digits, or there is no year at all.

    ════════════════════════════════════════════════════════════════════════
    WHAT THIS WATCHES, AND WHY IT NEEDS WATCHING
    ════════════════════════════════════════════════════════════════════════
    The `year` column used to be NOT NULL with a default of the text "N/A",
    so every travel title arrived carrying two letters instead of nothing.
    That string is TRUTHY, which is the whole problem: every screen guards
    with `year ? draw(year) : draw(nothing)`, and every one of those guards
    passed. The owner saw "(N/A)" beside titles that have no year and never
    could have one (2026-09-09).

    The column is nullable now and nothing writes a word into it, and
    preflight fails on any source line that tries. Both of those are
    statements about TODAY'S CODE. This check is a statement about the DATA,
    so it holds whatever an import, a hand edit or tomorrow's code does.

    It deliberately says nothing about which years are plausible. A year is
    four digits or it is absent; anything else is a word that will be drawn
    on a screen as though it were a fact.
    """
    rows_q = (db.query(MasterTitle)
                .filter(MasterTitle.year.isnot(None),
                        MasterTitle.year != "",
                        scope.titles,
                        ~MasterTitle.year.op("GLOB")("[0-9][0-9][0-9][0-9]")))
    found = rows_q.limit(MAX_ROWS).all()
    total = rows_q.count()

    rows = [Finding(f"title #{t.external_id}",
                    f"{t.title} has the year {t.year!r}, which is not a year",
                    "/admin/titles")
            for t in found]
    return _result(
        "year_is_a_year_or_nothing",
        f"{total} title(s) hold a word in the year column"
        if total else "Every title either has a real year or none at all",
        "A year has to be four digits. Anything else is a word standing in "
        "for 'we do not know', and every screen draws it beside the title "
        "because a word counts as an answer. The fix is to empty those "
        "years rather than to tidy up what is shown.",
        "error" if total else "ok", rows, total,
    )


def _account_names(db: Session) -> dict[int, str]:
    """
    id -> name for every marketplace account, fetched once.

    A helper rather than a lookup inside each row, because these lists can
    be long and a query per row is a query per row. Preflight now warns
    about that shape, which is how this got written this way.
    """
    return {a_id: name for a_id, name in
            db.query(UploadAccount.id, UploadAccount.name).all()}


CHECKS: list[Callable[[Session, "Scope"], CheckResult]] = [
    check_approved_without_a_print_file,
    check_current_image_was_discarded,
    check_generations_share_a_file,
    check_chosen_colour_was_painted,
    check_upload_gap_is_holding,
    check_failure_evidence_is_pruned,
    check_recalled_poster_still_painted,
    check_year_is_a_year_or_nothing,
    check_titles_collide_after_folding,
    check_missing_files,
    check_posters_without_title,
    check_orphan_files,
    check_complete_without_posters,
    check_open_revisions_on_deleted,
    check_stale_claims,
    check_projects_match_registry,
    check_sheet_columns_all_or_nothing,
    check_approved_images_have_a_background,
    check_search_phrasings_name_a_place,
    check_prompt_matches_style_toggle,
    check_claims_by_inactive,
    check_greenlit_not_complete,
    check_uploaded_without_processed,
    check_upload_accounts,
    check_duplicate_accounts,
    check_account_never_read,
    check_upload_project_starved,
    check_unclassified_ledger_rows,
    check_earnings_retry_gave_up,
    check_orphaned_bans,
    # ── Listing-health invariants (the TeePublic store checks left with
    #    that mechanism, 2026-09-06 — these are FineArtAmerica's) ───────
    check_unpayable_but_counted,
    check_listing_sweep_believable,
    check_two_current_images,
    check_worker_agent_current,
    check_orphaned_upload_rows,
    check_unassigned_titles,
    check_duplicate_hashes,
]


def run_all(db: Session, only: Optional[list[str]] = None,
            project_id: Optional[int] = None) -> dict:
    """
    Run every check (or just the named ones) and return a serialisable report.

    `project_id` narrows the whole run to one niche. Without it every check
    looks across the install and each finding says which project it came
    from — which is the useful default, because the question this page
    answers is usually "is anything wrong anywhere".

    A check that raises is reported as a failed check rather than taking the
    whole page down — the diagnostic tool being unavailable is exactly the
    wrong outcome when something is already wrong.
    """
    _PROJECT_OF_CACHE.clear()
    scope = Scope(db, project_id)
    results = []
    for fn in CHECKS:
        try:
            res = fn(db, scope)
        except Exception as e:
            res = CheckResult(
                key=getattr(fn, "__name__", "check"),
                title=getattr(fn, "__name__", "check").replace("_", " ").title(),
                explain="This check could not run.",
                severity="warn", error=f"{type(e).__name__}: {e}",
            )
        if only and res.key not in only:
            continue
        results.append(res)

    return {
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "project": {"id": scope.project_id, "name": scope.name,
                    "all": scope.all_projects},
        "totals": {
            "errors": sum(r.count for r in results if r.severity == "error"),
            "warnings": sum(r.count for r in results if r.severity == "warn"),
            "info": sum(r.count for r in results if r.severity == "info"),
        },
        "checks": [r.as_dict() for r in results],
    }
