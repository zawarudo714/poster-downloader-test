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
    AccountProject, MasterTitle, ProcessedImage, Project, Revision,
    SavedPoster, StoreListing, StoreScanRun, UploadAccount, UploadTracking,
    User,
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
            "/admin/pipeline#upload",
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
                "/admin/pipeline#upload",
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

def check_designs_left_switched_off(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: nothing we switched off should still be off once no run is
    working on it.

    Switching a design off is only ever half of a cure. If the other half
    never happened — a stage that ended early, a run abandoned in the middle,
    the node dying — the listing is hidden from customers and there is no
    other symptom at all. It does not error, it does not appear in a log, it
    just stops earning.
    """
    from .earnings.store_health import active_run

    rows_q = (db.query(StoreListing, UploadAccount.name)
                .outerjoin(UploadAccount,
                           UploadAccount.id == StoreListing.account_id)
                .filter(StoreListing.deactivated_at.isnot(None))
                .order_by(StoreListing.deactivated_at)
                .limit(MAX_ROWS))
    found = rows_q.all()
    total = (db.query(func.count(StoreListing.id))
               .filter(StoreListing.deactivated_at.isnot(None)).scalar() or 0)
    if not total:
        return _result("designs_switched_off",
                       "Designs left switched off", "", "ok", [])

    # A run actively working on them is not a fault — that is the cure in
    # progress. Anything else is.
    run = active_run(db)
    working = bool(run and run.status in ("deactivating", "confirming",
                                          "reactivating")
                   and not run.paused_at)
    now = datetime.utcnow()

    rows = [
        Finding(
            f"{name or 'unknown account'} — {listing.title or listing.design_id}",
            f"off since {listing.deactivated_at:%Y-%m-%d %H:%M}"
            + (f" ({(now - listing.deactivated_at).days}d)"
               if (now - listing.deactivated_at).days else ""),
            "/admin/store",
        )
        for listing, name in found
    ]

    return _result(
        "designs_switched_off",
        f"{total} design(s) are switched off on the marketplace",
        ("These were switched off as half of the deactivate/reactivate cure "
         "and never switched back on. They are hidden from customers and "
         "earning nothing. Press SWITCH BACK ON at the top of the TeePublic "
         "tab."
         if not working else
         "A run is switching these back on right now — this clears itself. "
         "If it is still here in an hour, it did not."),
        "warn" if working else "error", rows, total,
    )


def check_stuck_listing_run(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a run in a working stage must have work queued for it.

    A run says "deactivating" because it dispatched jobs. If those jobs are
    gone — finished, failed, reaped — and the run has not moved on, then
    nothing will ever move it. It holds Photoshop and the uploads for ever
    while the screen shows it politely in progress.

    ════════════════════════════════════════════════════════════════════════
    IT MUST BE THIS RUN'S OWN JOBS
    ════════════════════════════════════════════════════════════════════════
    The first version counted every live store job in the table, not the
    ones belonging to the run being examined. Any other sweep's job — even a
    different marketplace's — therefore counted as proof that THIS run was
    fine, and the check could report all-clear over a genuinely dead run.
    Same shape as scoping a query without its project.

    The scheduler now repairs this automatically every few minutes, so a
    finding here means repair ALSO failed — which is worth seeing.
    """
    from .earnings.store_health import FINISHED, jobs_for_run

    rows = []
    active = (db.query(StoreScanRun)
                .filter(~StoreScanRun.status.in_(FINISHED)).all())
    for run in active:
        if run.status not in ("scanning", "deactivating", "reactivating"):
            continue
        if run.paused_at or (run.retry_at and run.retry_at > datetime.utcnow()):
            continue          # deliberately waiting, not stuck

        if jobs_for_run(db, run):
            continue

        rows.append(Finding(
            f"Run #{run.id} says {run.status} but nothing is queued",
            f"started {run.started_at:%Y-%m-%d %H:%M}"
            + (f" · {run.stage_jobs_done} of {run.stage_jobs_total} accounts "
               f"reported" if run.stage_jobs_total else ""),
            "/admin/store",
        ))

    return _result(
        "stuck_listing_run", "A listing run that cannot move",
        "This run is holding Photoshop and the uploads, but it has no work "
        "queued to finish with — so it will hold them for ever. Press STOP "
        "THIS RUN on the TeePublic tab, then check whether anything was left "
        "switched off.",
        "error", rows,
    )


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


def check_automatic_run_waiting(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a run set to AUTOMATIC must never be sitting at a gate.

    ════════════════════════════════════════════════════════════════════════
    THE INCIDENT, 2026-08-24
    ════════════════════════════════════════════════════════════════════════
    "Automatic" means exactly one thing: no stage waits for a person. So a
    run with `auto` set, not paused, parked at `reviewing` or `confirming`
    is a contradiction — the setting says nobody is watching and the state
    says it is waiting for somebody.

    It happened because SWITCH OFF THE KNOWN MISSING created its run without
    reading the AUTOMATIC tickbox sitting directly above the button. Three
    other buttons on that screen read it; that one dropped it. Overnight the
    result would have been several hundred live listings switched off and a
    run waiting at a gate until morning, earning nothing.

    Every existing check called that healthy. `check_designs_left_switched_
    off` treats `confirming` as "a run is working on it", which is normally
    true and was exactly wrong here.

    Stated about STATE rather than about which button was pressed, so it
    holds for any future path that forgets to carry the setting through —
    and that is the point, because the bug was not in the stage machinery at
    all. It was one argument missing at one call site.
    """
    from .earnings.store_health import WAITING

    rows = []
    for run in (db.query(StoreScanRun)
                  .filter(StoreScanRun.auto == 1,
                          StoreScanRun.status.in_(WAITING)).all()):
        if run.paused_at:
            continue          # paused deliberately stops automatic handover
        rows.append(Finding(
            f"Run #{run.id} is set to automatic but is waiting at "
            f"'{run.status}'",
            f"started {run.started_at:%Y-%m-%d %H:%M}"
            + (f" by {run.started_by}" if run.started_by else ""),
            "/admin/store",
        ))

    return _result(
        "automatic_run_waiting", "An automatic run that is waiting for you",
        "This run was told to run all the way through without stopping, and "
        "it has stopped anyway. Nothing will move it until you press the "
        "button on the TeePublic tab — and if designs are switched off right "
        "now, they are earning nothing while it waits.",
        "error", rows,
    )


def check_orphaned_listing_jobs(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: no job may still be waiting to switch designs for a run that
    has already ended.

    ════════════════════════════════════════════════════════════════════════
    THE INCIDENT, 2026-08-24
    ════════════════════════════════════════════════════════════════════════
    A stage created one job per account UP FRONT — five jobs for five
    accounts. Stopping the run ended the run and released the pipeline, and
    did nothing whatsoever to those jobs. The node calmly claimed the next
    one and carried on switching live listings off for another two hours,
    while the tab said "abandoned".

    Cancelling on the way out now makes that impossible rather than merely
    detectable, and dispatching one account at a time means there is only
    ever one job to cancel. This check is the proof that both held — and it
    is stated about STATE, so it fires whatever route left the job behind,
    including one nobody has thought of yet.

    A design switched off by an orphan job is the expensive part: it is a
    live listing earning nothing, and no run's records will ever say to put
    it back.
    """
    from .earnings.store_health import ACTION_KINDS, FINISHED, LIVE_JOB, _run_id_of
    from .models import PipelineJob

    ended = {r.id for r in db.query(StoreScanRun)
                            .filter(StoreScanRun.status.in_(FINISHED)).all()}

    rows = []
    for job in (db.query(PipelineJob)
                  .filter(PipelineJob.kind.in_(ACTION_KINDS),
                          PipelineJob.status.in_(LIVE_JOB)).all()):
        run_id = _run_id_of(job)
        # A job whose run cannot be identified is just as dangerous: nothing
        # will ever cancel it, because nothing knows who it belongs to.
        if run_id is not None and run_id not in ended:
            continue
        rows.append(Finding(
            f"Job #{job.id} ({job.kind}) is still {job.status}",
            (f"run #{run_id} has already ended" if run_id
             else "it names no run at all"),
            "/admin/pipeline",
        ))

    return _result(
        "orphaned_listing_jobs", "Switching work left over from a stopped run",
        "This job will switch designs off or on for a run that is already "
        "over, and nothing is watching the result. Cancel it on the Pipeline "
        "tab, then check the TeePublic tab for designs left switched off.",
        "error", rows,
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


def _account_names(db: Session) -> dict[int, str]:
    """
    id -> name for every marketplace account, fetched once.

    A helper rather than a lookup inside each row, because these lists can
    be long and a query per row is a query per row. Preflight now warns
    about that shape, which is how this got written this way.
    """
    return {a_id: name for a_id, name in
            db.query(UploadAccount.id, UploadAccount.name).all()}


def check_marketplace_disagrees(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: what the marketplace says it did must match what we recorded.

    ════════════════════════════════════════════════════════════════════════
    THE ONLY CHECK ON THIS PAGE THAT IS NOT US MARKING OUR OWN HOMEWORK
    ════════════════════════════════════════════════════════════════════════
    Every other invariant here compares our records with our records. That
    catches a great deal, and it is completely blind to the one thing it
    most needs to catch: a design recorded as republished which is in fact
    still switched off. Nothing internal disagrees, so nothing goes red.

    On 25 Aug a reactivation logged 80 designs switched back on and TALKING
    HEADS · LITTLE CREATURES was sitting on the inactive tab afterwards. It
    was found because the owner opened the store in a browser. Nothing in
    this system would ever have said so.

    TeePublic prints its own count of switched-off designs. Read at both
    ends of an account's turn, the CHANGE across it belongs to us, and any
    difference is proof our records are wrong.
    """
    from .models import StoreCountCheck

    rows_q = (db.query(StoreCountCheck, UploadAccount.name)
                .outerjoin(UploadAccount,
                           UploadAccount.id == StoreCountCheck.account_id)
                .filter(StoreCountCheck.verdict == "mismatch")
                .order_by(StoreCountCheck.checked_at.desc())
                .limit(MAX_ROWS))
    found = rows_q.all()
    total = (db.query(func.count(StoreCountCheck.id))
               .filter(StoreCountCheck.verdict == "mismatch").scalar() or 0)

    rows = [
        Finding(f"{name or 'unknown account'} — "
                f"{check.checked_at:%Y-%m-%d %H:%M}",
                check.note or "", "/admin/store")
        for check, name in found
    ]
    return _result(
        "marketplace_disagrees",
        f"{total} time(s) the marketplace disagreed with our records"
        if total else "The marketplace agreed with our records",
        "We said we switched a number of designs; TeePublic's own count of "
        "switched-off designs moved by a different number. The designs we "
        "believe are live may not be. Run a MISSING-ONLY sweep to find out "
        "which, then switch them again.",
        "error" if total else "ok", rows, total,
    )


def check_count_check_is_running(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: if the marketplace's own count is switched on, every finished
    switching turn must have produced a reading.

    ════════════════════════════════════════════════════════════════════════
    THIS ONE WATCHES THE WATCHER
    ════════════════════════════════════════════════════════════════════════
    The check above is only worth having while it is actually looking. If
    TeePublic moves that number, or renames the link it sits in, the reading
    silently comes back empty — and the page then reports agreement for ever,
    over nothing being compared.

    That is the exact failure this project keeps producing: a green light
    from something that was never looking. So the absence of readings is
    itself a finding.
    """
    from . import pipeline as P
    from .models import StoreCountCheck

    if not int(P.get_setting(db, "store_count_check") or 0):
        return _result("count_check_running",
                       "The marketplace's own count is switched off",
                       "Nothing is being cross-checked against TeePublic. "
                       "Turn on 'store_count_check' on the Pipeline page to "
                       "put it back.", "warn", [], 0)

    recent = (db.query(StoreCountCheck)
                .order_by(StoreCountCheck.checked_at.desc())
                .limit(MAX_ROWS).all())
    unreadable = [c for c in recent if c.verdict == "unreadable"]
    if not recent:
        return _result("count_check_running",
                       "No reading of the marketplace's own count yet",
                       "Expected — no account has finished a switching turn "
                       "since this was switched on. It matters if a sweep "
                       "has run and this is still empty.", "ok", [], 0)

    names = _account_names(db)
    rows = [
        Finding(f"{names.get(c.account_id, 'unknown account')} — "
                f"{c.checked_at:%Y-%m-%d %H:%M}",
                c.note or "", "/admin/store")
        for c in unreadable
    ]
    return _result(
        "count_check_running",
        f"{len(unreadable)} switching turn(s) could not read the "
        f"marketplace's own count" if unreadable
        else "The marketplace's own count is being read",
        "The number is read off the store page while signed in. If it "
        "cannot be found, TeePublic has probably moved it — and until it is "
        "fixed, nothing is cross-checking what we believe against what they "
        "say.",
        "warn" if unreadable else "ok", rows, len(unreadable),
    )


def check_designs_given_up_on(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: a design the system has stopped trying to fix must be named.

    Flagging exists so a listing that will never switch off stops costing a
    page load in every future sweep. The danger in that is obvious: it is
    one short step from "stopped retrying" to "quietly forgotten". So the
    moment a design is given up on it appears here, with a RETRY button on
    the tab that puts it back in the queue.
    """
    from .earnings.store_health import flagged

    found = flagged(db, "teepublic")
    names = _account_names(db)
    rows = [
        Finding(f"{names.get(r.account_id, 'unknown account')} — "
                f"{r.title or r.design_id}",
                (r.action_error or "")[:160], "/admin/store")
        for r in found[:MAX_ROWS]
    ]
    return _result(
        "designs_given_up_on",
        f"{len(found)} design(s) the sweep has stopped trying to switch off"
        if found else "No design has been given up on",
        "Each of these failed on a page that was plainly ours, several "
        "sweeps running — so the button genuinely is not there. They are "
        "still live and still earning; they are simply no longer being "
        "cured. Look at one by hand, then press RETRY on the TeePublic tab.",
        "warn" if found else "ok", rows, len(found),
    )


def check_listing_catalogue_gaps(db: Session, scope: Scope) -> CheckResult:
    """
    INVARIANT: an account we can scan should have a catalogue, and a design
    in it should have been checked at some point.

    Catches the quiet cases: an account added and never swept, a store
    address that stopped working so its designs silently stopped being read,
    a whole account dropping out of a sweep without anybody noticing which.
    """
    from .earnings.store_health import SUPPORTED

    rows = []
    for acct in (db.query(UploadAccount)
                   .filter(func.lower(UploadAccount.target_site).in_(SUPPORTED))
                   .order_by(UploadAccount.name).all()):
        if not (acct.profile_url or "").strip():
            continue          # reported on the tab itself, not a fault here

        known = (db.query(func.count(StoreListing.id))
                   .filter(StoreListing.account_id == acct.id,
                           StoreListing.removed_at.is_(None)).scalar() or 0)
        if not known:
            rows.append(Finding(
                f"{acct.name} — never scanned",
                "has a store address but no designs in the catalogue",
                "/admin/store"))
            continue

        never = (db.query(func.count(StoreListing.id))
                   .filter(StoreListing.account_id == acct.id,
                           StoreListing.removed_at.is_(None),
                           StoreListing.excluded == 0,
                           StoreListing.last_checked_at.is_(None)).scalar() or 0)
        if never:
            rows.append(Finding(
                f"{acct.name} — {never} of {known} designs never checked",
                "a sweep has not reached these yet",
                "/admin/store"))

    return _result(
        "listing_catalogue_gaps", "Accounts or designs no sweep has reached",
        "Not a fault on its own — a new account, or a sweep that has not "
        "finished. It matters when it persists: those designs could have "
        "dropped out of search weeks ago and nothing here would know.",
        "warn", rows,
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
    # ── Listing-health invariants ───────────────────────────────────────
    check_designs_left_switched_off,
    check_stuck_listing_run,
    check_unpayable_but_counted,
    check_automatic_run_waiting,
    check_orphaned_listing_jobs,
    check_listing_sweep_believable,
    check_listing_catalogue_gaps,
    check_marketplace_disagrees,
    check_count_check_is_running,
    check_designs_given_up_on,
    check_two_current_images,
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
