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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, true as sa_true
from sqlalchemy.orm import Session

from .config import WORKSPACE_DIR
from .models import (
    MasterTitle, ProcessedImage, Project, Revision, SavedPoster,
    UploadAccount, UploadTracking, User,
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
        "from backup where the database is older than the workspace.",
        "warn", orphans, total,
    )


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
    rows = [
        Finding(f"#{ut.saved_poster_id} → account #{ut.account_id}",
                ut.remote_title or "", "/admin/pipeline",
                project=scope.label(ut.project_id))
        for ut in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "upload_no_processed", "Live listings with no processed file on record",
        "The image is on the marketplace but the archive has no current "
        "derivative for it. Expected for the legacy images imported from the "
        "old laptop workflow. Anything recent means the storage record was "
        "lost — you would not be able to re-upload it after a ban.",
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
            "Treated as the default project everywhere. Harmless today; run "
            "the backfill in scripts/migrate_pipeline.py before a second "
            "project's titles are imported into the same table.",
            "/admin/master",
        )]
    return _result(
        "unassigned_titles", "Titles with no project",
        "The original import predates projects. Everything treats these as "
        "the default project, so nothing is broken — but the ambiguity should "
        "be resolved before a second niche shares the table.",
        "info", rows, 1 if total else 0,
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
        q = q.filter(UploadAccount.project_id == scope.project_id)

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
                                project=scope.label(acct.project_id)))
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
        q = q.filter(UploadAccount.project_id == scope.project_id)

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
            "/admin/pipeline#upload",
            project=scope.label(acct.project_id)))

    return _result(
        "orphaned_bans", "Banned accounts whose work was never re-listed",
        "These accounts were closed by the marketplace and their listings "
        "went with them. The images are still in the archive and still cost "
        "you money to make — until they are handed over to a replacement "
        "account they are earning nothing. Use HAND OVER TO… on the Upload "
        "tab.",
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


CHECKS: list[Callable[[Session, "Scope"], CheckResult]] = [
    check_missing_files,
    check_orphan_files,
    check_complete_without_posters,
    check_open_revisions_on_deleted,
    check_stale_claims,
    check_claims_by_inactive,
    check_greenlit_not_complete,
    check_uploaded_without_processed,
    check_upload_accounts,
    check_orphaned_bans,
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
