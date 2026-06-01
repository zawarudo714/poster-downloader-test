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

    # Active title for this user — points to a MasterTitle currently locked for work.
    locked_master_id = Column(Integer, ForeignKey("master_titles.id"), nullable=True)

    # Last "pull next N" size remembered between sessions.
    last_pull_size   = Column(Integer, nullable=True)

    # Last time we saw a request from this user. Set on every authenticated
    # request (cheap one-row update). Drives the admin's "online / away /
    # offline" indicator on the Users page.
    last_seen_at     = Column(DateTime, nullable=True, index=True)

    # Soft-delete flag. `is_deleted=1` users:
    #   - cannot log in (auth check rejects them)
    #   - don't appear in the active worker list
    #   - their saved_posters rows + chat history + payment runs are PRESERVED
    #     (deletion just hides them from active use; old data stays intact for
    #     audit + admin gallery viewing)
    # Username deletion is enforced via double-confirm in the admin UI.
    is_deleted       = Column(Integer, default=0, nullable=False, index=True)
    deleted_at       = Column(DateTime, nullable=True)


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
    # 'pending' | 'in_progress' | 'complete_pending' | 'complete' | 'skipped'
    # 'complete_pending' = worker clicked DONE while flags/changes existed;
    #                      title is held for admin approval. Admin approves
    #                      → 'complete', or rejects → back to 'in_progress'
    #                      with all revisions reopened.
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
    # If this poster was added by an admin (not the worker), stores the
    # admin's username. NULL = worker-added (normal). Non-NULL = admin
    # added it via the browse page. Admin-added posters cannot be flagged
    # and don't count toward worker payment stats.
    added_by           = Column(String(64), nullable=True)
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
    # What the worker DID to send this for approval. NULL for "open" status
    # (worker hasn't acted yet). Set when status flips to awaiting_approval:
    #   "replaced"  — worker replaced the file with a new URL
    #   "deleted"   — worker soft-deleted the file
    # Drives admin UI labelling (e.g. "Approve deletion" vs "Approve fix")
    # and lets the worker see a sensible placeholder card for deleted posters
    # instead of a broken image.
    worker_action   = Column(String(16), nullable=True)
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


# ── App settings (key-value store for admin-tunable config) ─────────────────

class AppSetting(Base):
    """
    Single-row-per-key configuration that admins can tweak via the UI without
    redeploying. Currently used for the payments feature:
        pay_rate_kes      → per-poster rate in KES (decimal-ish, stored as string for fidelity)
        week_start_day    → 0=Mon..6=Sun, default 0 (Mon→Sun week)
    Generic enough to stash other prefs later. Values are TEXT — the route layer
    decides how to parse each one.
    """
    __tablename__ = "app_settings"

    key        = Column(String(64), primary_key=True)
    value      = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String(64), nullable=True)


# ── Payment runs (one row per "I paid worker X for these days") ─────────────

class PaymentRun(Base):
    """
    Records a payment the admin marks as sent.

    Each run covers a contiguous date range [period_start, period_end] for one
    worker, with the per-poster rate frozen at the time of payment (so future
    rate changes don't retroactively rewrite history). The list of saved-poster
    IDs counted toward this run is stored as JSON for an audit trail.

    Workflow:
      1. Admin opens Payments page, picks a worker + date range.
      2. UI shows "X eligible posters × Y KES = Z KES."
         (Eligible = saved on those days, not deleted, not under any
          open / awaiting-approval revision at the moment of preview.)
      3. Admin types the actual amount sent + optional reference (M-Pesa code),
         clicks "MARK PAID". A PaymentRun row is written; the same days can't
         be paid for twice (those poster IDs become ineligible for future runs).
      4. Optional "PUSH TO WORKER" — sets pushed_at, worker sees a receipt
         banner on next state poll, can click ACKNOWLEDGE which sets ack_at.
    """
    __tablename__ = "payment_runs"

    id              = Column(Integer, primary_key=True)
    worker_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    worker_username = Column(String(64), nullable=False)  # denorm for safety if user is later renamed/deleted
    period_start    = Column(Date, nullable=False)        # inclusive
    period_end      = Column(Date, nullable=False)        # inclusive
    poster_count    = Column(Integer, nullable=False, default=0)
    rate_kes        = Column(String(32), nullable=False)  # stored as string for decimal fidelity (e.g. "12.50")
    amount_kes      = Column(String(32), nullable=False)  # final amount admin sent (may differ from count*rate)
    reference       = Column(String(128), nullable=True)  # M-Pesa code, etc.
    note            = Column(Text, nullable=True)
    poster_ids_json = Column(Text, nullable=False, default="[]")  # JSON list of paid saved_poster IDs

    # Per-day breakdown captured at run creation time, for receipt
    # transparency. Format: {"2026-04-30": 5, "2026-04-29": 2, ...}
    by_day_json     = Column(Text, nullable=True)
    # Subset of dates in by_day_json that are OUTSIDE [period_start, period_end]
    # — i.e. older "back-pay" posters admin manually included in this run
    # because they became eligible after the original period was paid.
    # JSON list of date strings: ["2026-04-23", "2026-04-22"]
    back_pay_dates_json = Column(Text, nullable=True)

    # Push-to-worker (receipt) flow — null until admin pushes.
    pushed_at       = Column(DateTime, nullable=True)
    ack_at          = Column(DateTime, nullable=True)
    # v15: Worker clicked "NOT RECEIVED" instead of "ACKNOWLEDGE".
    # Non-null = worker disputes; admin sees the timestamp + can follow up.
    not_received_at = Column(DateTime, nullable=True)

    created_by      = Column(String(64), nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ── Chat messages (admin ↔ worker, simple polling-based) ────────────────────

class ChatMessage(Base):
    """
    One row per message. Conversation is implicit between (admin, worker)
    pairs — `worker_id` identifies the conversation; sender is the user who
    typed it. Workers only ever see messages with their own worker_id; admins
    see everything and can switch between worker threads.

    Read state is tracked per-side via two timestamps so we can show unread
    badges. We don't track per-message read receipts — overkill for this use.
    Pruning is manual via `note` on the schema; no automatic deletion.
    """
    __tablename__ = "chat_messages"

    id            = Column(Integer, primary_key=True)
    worker_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    sender_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    sender_role   = Column(String(16), nullable=False)  # 'admin' | 'worker' (denorm for fast filter)
    body          = Column(Text, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        # Common query: all messages for a worker, newest first.
        Index("ix_chat_worker_time", "worker_id", "created_at"),
    )


class ChatReadState(Base):
    """
    Tracks the last-read timestamp per (worker_thread, viewer) — so the unread
    badge shows the right count for both admin and worker. Composite primary
    key: (worker_id, viewer_id).
    """
    __tablename__ = "chat_read_state"

    worker_id   = Column(Integer, ForeignKey("users.id"), primary_key=True)
    viewer_id   = Column(Integer, ForeignKey("users.id"), primary_key=True)
    last_read_at = Column(DateTime, nullable=False, default=datetime.utcnow)
