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
ADDING A CHECK
════════════════════════════════════════════════════════════════════════════
Write a function taking (db) and returning a `Finding` list, then add it to
CHECKS. Keep each one bounded — LIMIT every query. This runs against 101,605
master rows and 7,972 posters today and will run against several times that;
a check that materialises the whole table will make the page unusable exactly
when the operator most needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import WORKSPACE_DIR
from .models import (
    MasterTitle, ProcessedImage, Revision, SavedPoster,
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

    def as_dict(self) -> dict:
        return {
            "key": self.key, "title": self.title, "explain": self.explain,
            "severity": self.severity, "count": self.count,
            "truncated": self.truncated, "error": self.error,
            "findings": [
                {"what": f.what, "detail": f.detail, "link": f.link}
                for f in self.findings
            ],
        }


def _result(key, title, explain, severity, rows, total=None) -> CheckResult:
    total = len(rows) if total is None else total
    return CheckResult(
        key=key, title=title, explain=explain, severity=severity,
        count=total, truncated=total > len(rows), findings=rows,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  DATABASE ↔ DISK
# ═══════════════════════════════════════════════════════════════════════════

def check_missing_files(db: Session) -> CheckResult:
    """
    Live poster rows whose file is not on disk.

    Bounded by scanning the most recent rows first — the ones most likely to
    matter, and the ones an admin can still do something about. A full scan of
    every poster ever saved is what the CLI is for.
    """
    rows = (
        db.query(SavedPoster)
          .filter(SavedPoster.deleted_at.is_(None))
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
                    detail=f"poster #{sp.id}, saved {sp.original_save_date}",
                    link=f"/admin/browse?worker={sp.username}"
                         f"&date={sp.original_save_date}",
                ))
        except Exception as e:                      # unreadable path, bad chars
            bad.append(Finding(f"poster #{sp.id}: {e}"))
        if len(bad) >= MAX_ROWS:
            break
    return _result(
        "missing_files", "Poster records with no file on disk",
        "The database says this image exists; the workspace doesn't have it. "
        "Usually a file deleted outside the app. The worker was likely paid "
        "for it and the pipeline will fail on it. Decide per poster: delete "
        "the record from the browse page, or restore the file.",
        "error", bad,
    )


def check_orphan_files(db: Session) -> CheckResult:
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
    known: set[str] = set()
    for sp in db.query(SavedPoster).all():
        try:
            known.add(saved_poster_path(sp).resolve().as_posix())
        except Exception:
            continue

    orphans: list[Finding] = []
    total = 0
    if WORKSPACE_DIR.is_dir():
        for path in WORKSPACE_DIR.rglob("*"):
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

def check_complete_without_posters(db: Session) -> CheckResult:
    """Titles marked complete that have no live posters at all."""
    sub = (
        db.query(SavedPoster.master_title_id)
          .filter(SavedPoster.deleted_at.is_(None))
          .distinct()
    )
    q = (
        db.query(MasterTitle)
          .filter(MasterTitle.status == "complete",
                  ~MasterTitle.id.in_(sub))
    )
    total = q.count()
    rows = [
        Finding(f"{t.title} ({t.year})",
                f"title #{t.id}, external id {t.external_id}",
                "/admin/master?q=" + (t.title or ""))
        for t in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "complete_empty", "Completed titles with no posters",
        "Marked done but every poster has been deleted. Nothing will reach "
        "the marketplace for these. Send the title back to the worker, or "
        "accept it as genuinely unavailable and skip it.",
        "error", rows, total,
    )


def check_stale_claims(db: Session) -> CheckResult:
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
                  SavedPoster.claimed_at < cutoff)
    )
    total = q.count()
    rows = [
        Finding(f"poster #{sp.id} · {sp.filename}",
                f"{sp.pipeline_status} on '{sp.claimed_by}' since "
                f"{sp.claimed_at:%Y-%m-%d %H:%M}",
                "/admin/pipeline")
        for sp in q.order_by(SavedPoster.claimed_at.asc()).limit(MAX_ROWS).all()
    ]
    return _result(
        "stale_claims", "Work claimed by a node that never finished",
        "A worker node took these and stopped — crashed, rebooted, or lost "
        "its connection. They are not in any queue. Requeue them from the "
        "Pipeline page once you're sure the node isn't still working.",
        "warn", rows, total,
    )


def check_greenlit_not_complete(db: Session) -> CheckResult:
    """Posters in the pipeline whose title isn't complete."""
    q = (
        db.query(SavedPoster, MasterTitle)
          .join(MasterTitle, SavedPoster.master_title_id == MasterTitle.id)
          .filter(SavedPoster.pipeline_status.isnot(None),
                  SavedPoster.pipeline_status != "skipped",
                  SavedPoster.deleted_at.is_(None),
                  MasterTitle.status != "complete")
    )
    total = q.count()
    rows = [
        Finding(f"{mt.title} ({mt.year}) · {sp.filename}",
                f"title is '{mt.status}', poster is '{sp.pipeline_status}'",
                "/admin/pipeline")
        for sp, mt in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "greenlit_incomplete", "Pipeline work on titles that aren't complete",
        "These images are being processed or are already live while their "
        "title is back in progress or was reopened. Not automatically wrong — "
        "a title can be reopened after its posters went out — but worth "
        "knowing about before you approve more work on it.",
        "info", rows, total,
    )


def check_uploaded_without_processed(db: Session) -> CheckResult:
    """Uploads recorded against a poster that has no current processed file."""
    sub = (
        db.query(ProcessedImage.saved_poster_id)
          .filter(ProcessedImage.is_current == 1)
          .distinct()
    )
    q = (
        db.query(UploadTracking)
          .filter(UploadTracking.status == "uploaded",
                  ~UploadTracking.saved_poster_id.in_(sub))
    )
    total = q.count()
    rows = [
        Finding(f"poster #{ut.saved_poster_id} → account #{ut.account_id}",
                ut.remote_title or "", "/admin/pipeline")
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


def check_unassigned_titles(db: Session) -> CheckResult:
    """Master rows that were never given a project."""
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


def check_claims_by_inactive(db: Session) -> CheckResult:
    """Titles held by a worker who can no longer log in."""
    q = (
        db.query(MasterTitle, User)
          .join(User, MasterTitle.claimed_by_id == User.id)
          .filter(MasterTitle.status == "in_progress",
                  ((User.is_active == 0) | (User.is_deleted == 1)))
    )
    total = q.count()
    rows = [
        Finding(f"{mt.title} ({mt.year})",
                f"held by {u.username} "
                f"({'deleted' if u.is_deleted else 'disabled'})",
                "/admin/users")
        for mt, u in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "claims_by_inactive", "Titles held by disabled or deleted workers",
        "Nobody can work on these and nobody else can claim them — they are "
        "stuck out of the queue. Release them from the Users page.",
        "warn", rows, total,
    )


def check_upload_accounts(db: Session) -> CheckResult:
    """Marketplace accounts that can't actually be used."""
    rows: list[Finding] = []
    for acct in db.query(UploadAccount).all():
        problems = []
        if not acct.password_enc:
            problems.append("no stored password")
        if not acct.profile_url:
            problems.append("no profile URL")
        if not acct.daily_limit:
            problems.append("daily limit is 0")
        if problems:
            rows.append(Finding(f"{acct.name} ({acct.email})",
                                ", ".join(problems), "/admin/pipeline"))
    return _result(
        "upload_accounts", "Marketplace accounts that can't be used",
        "The uploader will skip these. If an account is meant to be idle, "
        "disable it instead so it doesn't show up here every time.",
        "warn", rows,
    )


def check_open_revisions_on_deleted(db: Session) -> CheckResult:
    """Change requests still open against a poster that's already gone."""
    q = (
        db.query(Revision, SavedPoster)
          .join(SavedPoster, Revision.saved_poster_id == SavedPoster.id)
          .filter(Revision.status.in_(("open", "awaiting_approval")),
                  SavedPoster.deleted_at.isnot(None))
    )
    total = q.count()
    rows = [
        Finding(f"revision #{rev.id} on deleted poster #{sp.id}",
                rev.comment or "", "/admin/revisions")
        for rev, sp in q.limit(MAX_ROWS).all()
    ]
    return _result(
        "revisions_on_deleted", "Change requests on deleted posters",
        "The worker can never resolve these — the image is gone. They also "
        "block payment for that poster indefinitely. Close them from the "
        "Changes Requested page.",
        "error", rows, total,
    )


def check_duplicate_hashes(db: Session) -> CheckResult:
    """The identical file saved more than once."""
    dupes = (
        db.query(SavedPoster.content_hash, func.count(SavedPoster.id))
          .filter(SavedPoster.content_hash.isnot(None),
                  SavedPoster.deleted_at.is_(None))
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
                      SavedPoster.deleted_at.is_(None))
              .limit(6)
              .all()
        )
        rows.append(Finding(
            f"{n} identical copies",
            " · ".join(f"#{p.id} {p.username}/{p.filename}" for p in posters),
        ))
    return _result(
        "duplicate_hashes", "Byte-identical posters saved more than once",
        "The same image saved twice — sometimes legitimately (two titles "
        "sharing artwork), sometimes a worker saving the same file twice and "
        "being paid twice. Worth a look when the count is high.",
        "info", rows,
    )


CHECKS: list[Callable[[Session], CheckResult]] = [
    check_missing_files,
    check_orphan_files,
    check_complete_without_posters,
    check_open_revisions_on_deleted,
    check_stale_claims,
    check_claims_by_inactive,
    check_greenlit_not_complete,
    check_uploaded_without_processed,
    check_upload_accounts,
    check_unassigned_titles,
    check_duplicate_hashes,
]


def run_all(db: Session, only: Optional[list[str]] = None) -> dict:
    """
    Run every check (or just the named ones) and return a serialisable report.

    A check that raises is reported as a failed check rather than taking the
    whole page down — the diagnostic tool being unavailable is exactly the
    wrong outcome when something is already wrong.
    """
    results = []
    for fn in CHECKS:
        try:
            res = fn(db)
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
        "totals": {
            "errors": sum(r.count for r in results if r.severity == "error"),
            "warnings": sum(r.count for r in results if r.severity == "warn"),
            "info": sum(r.count for r in results if r.severity == "info"),
        },
        "checks": [r.as_dict() for r in results],
    }
