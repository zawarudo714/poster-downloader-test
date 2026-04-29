"""
Database models — reworked for the queue/claim workflow.

Key design decisions (recap):
- The MASTER TABLE is the source of truth for the work queue. Each row is a title.
  Workers don't paste lists; they "claim" master rows (pull next N, or manual select),
  which atomically flips status pending → in_progress and assigns claimed_by.
- A title's folder path on disk is decided ONCE on the first save, then frozen on
  the MasterTitle row (`title_folder_path`, `original_save_date`). All subsequent
  saves — including revisions days later — go to that same folder. Today's calendar
  date never enters the path computation after first save.
- SavedPoster is the per-poster record. Filesystem is a cache of what SavedPoster says
  exists. Soft-deletes via deleted_at so audit history survives.
- Revision links to a SavedPoster id, which is stable across rename/replace.
- ActivityLog captures every user action for audit/forensics.

Old models removed: WorkerSession (claimed-queue replaces it), DownloadedUrl
(SavedPoster + ActivityLog cover its roles).
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Date, Float,
    UniqueConstraint, Index, ForeignKey,
)
from sqlalchemy.orm import relationship

from .db import Base


# ── Users ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True)
    username      = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(String(16), nullable=False, default="worker")  # 'admin' | 'worker'
    is_active     = Column(Integer, default=1, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Which environment this user operates in. "live" = the production env;
    # any other value is a test-env name. For workers, this is auto-applied
    # by the middleware on every request — they have no way to switch envs.
    # For admins, this acts only as a default; admins can switch any time.
    env           = Column(String(48), nullable=False, default="live")

    # Active title for this user — points to a MasterTitle currently locked for work.
    locked_master_id = Column(Integer, ForeignKey("master_titles.id"), nullable=True)

    # Last "pull next N" size remembered between sessions.
    last_pull_size   = Column(Integer, nullable=True)


# ── Master title sheet ───────────────────────────────────────────────────────

class MasterTitle(Base):
    """
    The work queue. Imported from CSV/XLSX, ordered by `external_id` ascending.
    Workers claim rows top-to-bottom. status drives display + filtering.
    `needs_revision` is an orthogonal admin flag that overrides display tinting.
    """
    __tablename__ = "master_titles"

    id            = Column(Integer, primary_key=True)
    external_id   = Column(Integer, nullable=True, index=True)   # the "0" column from upstream CSV
    title         = Column(String(512), nullable=False)
    year          = Column(String(16), nullable=False, default="N/A")
    content_type  = Column(String(32), nullable=True)            # 'movie' | 'tvSeries' | None
    votes         = Column(Integer, nullable=True)
    rating        = Column(Float, nullable=True)
    description   = Column(Text, nullable=True)

    # Workflow state
    status            = Column(String(32), nullable=False, default="pending", index=True)
    # 'pending' | 'in_progress' | 'complete' | 'skipped'
    needs_revision    = Column(Integer, nullable=False, default=0, index=True)  # 0/1
    skip_reason       = Column(Text, nullable=True)
    complete_comment  = Column(Text, nullable=True)   # optional note from worker on complete
    admin_note        = Column(Text, nullable=True)   # admin's note when sending a skipped title back

    # Claim — set when a user pulls/selects this row, cleared on release.
    claimed_by_id     = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    claimed_by_name   = Column(String(64), nullable=True)  # denormalized for display
    claimed_at        = Column(DateTime, nullable=True)

    # Immutable once first save lands.
    started_at        = Column(DateTime, nullable=True)
    completed_at      = Column(DateTime, nullable=True)
    original_save_date = Column(Date, nullable=True)
    title_folder_path  = Column(String(512), nullable=True)

    # Bookkeeping
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    saved_posters = relationship("SavedPoster", back_populates="master_title")

    __table_args__ = (
        Index("ix_master_status_extid", "status", "external_id"),
        Index("ix_master_claim_status", "claimed_by_id", "status"),
    )


# ── Saved posters ────────────────────────────────────────────────────────────

class SavedPoster(Base):
    """
    One row per poster ever saved. Soft-deleted via deleted_at.
    Path on disk = WORKSPACE_DIR / user.username / original_save_date / title_folder_path / filename.
    `original_save_date` and `title_folder_path` are also denormalized here for fast lookup,
    but the canonical copies live on MasterTitle (set once at first-save).
    """
    __tablename__ = "saved_posters"

    id                 = Column(Integer, primary_key=True)
    master_title_id    = Column(Integer, ForeignKey("master_titles.id"), nullable=False, index=True)
    user_id            = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    username           = Column(String(64), nullable=False, index=True)  # denormalized for fast filtering
    original_save_date = Column(Date, nullable=False, index=True)
    title_folder_path  = Column(String(512), nullable=False)
    filename           = Column(String(512), nullable=False)
    source_url         = Column(Text, nullable=False)
    file_size          = Column(Integer, nullable=True)
    content_hash       = Column(String(64), nullable=True, index=True)
    # Quality flags surfaced to admin's gallery view:
    low_quality_url    = Column(Integer, nullable=False, default=0)  # 1 = LQ warning was bypassed
    image_width        = Column(Integer, nullable=True)              # actual pixel width (sub-800 highlight)
    image_height       = Column(Integer, nullable=True)
    # Worker's reason if this poster was deleted from a revision context.
    delete_note        = Column(Text, nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    deleted_at         = Column(DateTime, nullable=True, index=True)

    master_title = relationship("MasterTitle", back_populates="saved_posters")

    __table_args__ = (
        # Fast "live posters by user" lookup (excludes deleted via where deleted_at IS NULL).
        Index("ix_poster_user_alive", "user_id", "deleted_at"),
        Index("ix_poster_master_alive", "master_title_id", "deleted_at"),
    )


# ── Revisions ────────────────────────────────────────────────────────────────

class Revision(Base):
    """
    Admin flags a specific SavedPoster for redo. Stable through filename changes
    because it links by saved_poster_id, not by filesystem path.

    Status flow:
        open                — admin flagged, worker hasn't acted
        awaiting_approval   — worker replaced or marked-fixed; admin needs to confirm
        resolved            — admin approved; flag clears from worker
        (admin can also reject from awaiting_approval, sending it back to 'open'
         with a fresh comment.)
    """
    __tablename__ = "revisions"

    id              = Column(Integer, primary_key=True)
    saved_poster_id = Column(Integer, ForeignKey("saved_posters.id"), nullable=False, index=True)
    comment         = Column(Text, nullable=True)         # admin's flag comment (latest)
    flagged_by      = Column(String(64), nullable=False)
    status          = Column(String(20), nullable=False, default="open", index=True)
    # 'open' | 'awaiting_approval' | 'resolved'
    # 'simple' = single-poster flag; 'similar' = "these are too alike", worker picks one to redo.
    revision_type        = Column(String(16), nullable=False, default="simple")
    # JSON list of additional saved_poster ids (used by 'similar' type).
    related_poster_ids   = Column(Text, nullable=True)
    worker_note     = Column(Text, nullable=True)         # worker's note when sending for approval
    admin_verdict   = Column(Text, nullable=True)         # admin's note when approving/rejecting
    submitted_at    = Column(DateTime, nullable=True)     # when worker sent for approval
    resolved_by     = Column(String(64), nullable=True)
    # When admin has reviewed a deletion (clicked Acknowledge or Send Back).
    # Used to filter the "Recent Deletions" panel so resolved deletions don't
    # keep reappearing after the admin has already dealt with them.
    admin_acked_at  = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at     = Column(DateTime, nullable=True)

    saved_poster = relationship("SavedPoster")


# ── Activity log (immutable audit trail) ─────────────────────────────────────

class ActivityLog(Base):
    """
    Append-only. Every meaningful user action writes one row.
    Actions: claimed, released, locked, unlocked, saved, deleted, replaced,
             flagged, unflagged, resolved, completed, skipped, reopened,
             imported, bulk_status, user_created, user_toggled, password_reset.
    target_type: master_title | saved_poster | revision | user | import_job | bulk
    """
    __tablename__ = "activity_log"

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # nullable for system actions
    username    = Column(String(64), nullable=True, index=True)  # denormalized
    action      = Column(String(32), nullable=False, index=True)
    target_type = Column(String(32), nullable=True)
    target_id   = Column(Integer, nullable=True)
    details     = Column(Text, nullable=True)  # JSON string
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ── Import jobs (background CSV/XLSX import) ─────────────────────────────────

class ImportJob(Base):
    """Tracks a master-sheet import running in a background thread."""
    __tablename__ = "import_jobs"

    id          = Column(Integer, primary_key=True)
    started_by  = Column(String(64), nullable=False)
    state       = Column(String(16), nullable=False, default="pending")  # pending | running | done | error
    total_rows  = Column(Integer, nullable=False, default=0)
    done_rows   = Column(Integer, nullable=False, default=0)
    error       = Column(Text, nullable=True)
    replaced    = Column(Integer, nullable=False, default=0)  # 0/1 — was --replace passed?
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
