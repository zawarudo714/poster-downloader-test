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

    # Which project this user was last working in. Admins land back in the
    # project they left; workers assigned to more than one resume where they
    # were rather than being dropped at a chooser every session.
    last_project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)


class UserProject(Base):
    """
    Which projects a worker may work on. Many-to-many by design: one worker can
    cover movies and celebrities, and one project has several workers.

    Before this existed a worker's GET button pulled from EVERY project's
    master list, so the day a second niche was added, celebrity titles would
    have landed in a movie worker's queue with nothing to prevent it.

    Admins are not listed here — they see everything and switch project from
    the master dashboard.
    """
    __tablename__ = "user_projects"

    user_id    = Column(Integer, ForeignKey("users.id"), primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), primary_key=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    assigned_by = Column(String(64), nullable=True)


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

    # ── Post-production pipeline ────────────────────────────────────────
    # Which niche/workflow this title belongs to. Nullable so the existing
    # 101k rows don't need a backfill before the app boots; the migration
    # sets them all to project 1 and pipeline code treats NULL as project 1.
    project_id        = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    # Admin (or auto-on-payment) approval gate. Until this is set, the
    # Photoshop stage will not touch the title's posters, even if complete.
    greenlit_at       = Column(DateTime, nullable=True, index=True)
    greenlit_by       = Column(String(64), nullable=True)
    # HOW it was greenlit, not just who by:
    #   'payment:<run_id>' — released automatically when that run was paid
    #   'manual'           — released by hand, which means it may be UNPAID
    #   'all_paid'         — bulk release of everything already covered by a run
    #   'migration'        — inferred during the legacy import
    #
    # Kept as a filterable column rather than only in the activity log because
    # the question you will actually ask is "show me everything released
    # without payment", and that has to be a query, not an audit trawl.
    greenlit_source   = Column(String(32), nullable=True, index=True)
    # Rollup of the per-poster pipeline state, recomputed by
    # pipeline.recompute_title_status(). Denormalized purely so the Pipeline
    # dashboard can page/filter thousands of titles cheaply — never trust it
    # over the saved_posters rows.
    #   NULL | greenlit | processing | processed | uploading | uploaded
    #   | partial | failed
    pipeline_status   = Column(String(24), nullable=True, index=True)

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
    # The project's folder segment, denormalised for the same reason username
    # and original_save_date are: saved_poster_path() is called on every
    # gallery thumbnail and every pipeline dispatch, and it must not need a
    # join through master_titles just to build a filename.
    #
    # NULL means a row written before the workspace was split by project —
    # those files live at the old {user}/{date}/... path. See
    # app/workspace_migration.py.
    project_folder     = Column(String(64), nullable=True, index=True)
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

    # ── Post-production pipeline (per-image state) ──────────────────────
    # The authoritative per-image stage. The title-level MasterTitle
    # .pipeline_status is just a rollup of these.
    #   NULL       — not greenlit yet
    #   greenlit   — approved, waiting for Photoshop
    #   processing — a worker node claimed it for Photoshop
    #   processed  — derivative exists in storage (see processed_images)
    #   uploading  — a worker node claimed it for marketplace upload
    #   uploaded   — live on at least one marketplace account
    #   failed_processing / failed_upload — needs attention or retry
    #   skipped    — admin excluded it from the pipeline
    pipeline_status    = Column(String(24), nullable=True, index=True)
    # Retry/backoff bookkeeping for the Photoshop stage. Upload-side attempts
    # live on upload_tracking (per account), not here.
    process_attempts   = Column(Integer, nullable=False, default=0)
    process_error      = Column(Text, nullable=True)
    # Set when a node claims this poster, cleared on completion. Lets a stale
    # claim be reaped if a node dies mid-batch.
    claimed_at         = Column(DateTime, nullable=True)
    claimed_by         = Column(String(64), nullable=True)

    master_title = relationship("MasterTitle", back_populates="saved_posters")

    __table_args__ = (
        # Fast "live posters by user" lookup (excludes deleted via where deleted_at IS NULL).
        Index("ix_poster_user_alive", "user_id", "deleted_at"),
        Index("ix_poster_master_alive", "master_title_id", "deleted_at"),
        # Drives the pipeline dispatcher's "what's next" query.
        Index("ix_poster_pipeline", "pipeline_status", "deleted_at"),
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
    # Per-project split of this run: {"tell-a-vision": 120, "celebrity": 40}.
    # A worker covering two projects is paid ONCE; this is what lets the
    # receipt show where the work came from, and lets you cost a project.
    by_project_json = Column(Text, nullable=True)
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


# ═════════════════════════════════════════════════════════════════════════════
#  POST-PRODUCTION PIPELINE
#  ------------------------
#  Everything below powers the automated Photoshop → stock-site upload
#  pipeline. Design rules (read before extending):
#
#  1. NOTHING IS HARDCODED. Scripts, CSS selectors, title formats, keyword
#     templates, timings and schedules all live in `app_settings` (see
#     app/pipeline.py `get_setting`). The dashboard edits them; the remote
#     worker fetches them at runtime. Never inline a selector or a path.
#
#  2. MULTI-PROJECT FROM DAY ONE. Every pipeline table carries `project_id`.
#     Today there is exactly one project ("Tell-A-Vision", movies/series).
#     Adding the celebrity niche, or a TeePublic target, must never require
#     a schema migration — only new rows.
#
#  3. MULTI-TARGET FROM DAY ONE. `target_site` is a free string ('faa',
#     'teepublic', ...). Upload logic is selected by that value on the
#     worker side, so a new marketplace is a new worker module plus a new
#     settings block — not a schema change.
#
#  4. THE DATABASE IS THE ONLY SOURCE OF TRUTH. The legacy JSON files
#     (faa_upload_tracking.json, faa_content_data.json, faa_config.json,
#     faa_settings.json) are imported once by scripts/migrate_pipeline.py
#     and then retired. Do not reintroduce sidecar state files.
# ═════════════════════════════════════════════════════════════════════════════


class Project(Base):
    """
    A niche / workflow. One row per (content vertical + processing style).

    Project 1 is seeded as 'tell-a-vision' (movies & series, TMDB source,
    Real Paint FX processing, FineArtAmerica target) to match the existing
    single-workflow install. A second project ('celebrity', Pinterest source,
    2 images per title) drops in as another row with no code changes.

    `settings_prefix` is what pipeline.get_setting() uses to scope
    per-project overrides in app_settings — e.g. a project-specific JSX
    script lives under `pipeline.celebrity.process_script` and falls back
    to the global `pipeline.process_script` when unset.
    """
    __tablename__ = "projects"

    id              = Column(Integer, primary_key=True)
    slug            = Column(String(64), unique=True, nullable=False, index=True)
    name            = Column(String(128), nullable=False)
    # Where source images come from — informational, drives UI copy/links.
    source_site     = Column(String(64), nullable=True)       # 'tmdb' | 'pinterest' | ...
    # Which marketplace this project publishes to. Drives the storage layout
    # (S:/{site}/{project}/processed/...) and how the project is labelled.
    # A project is one design type on one marketplace — it may have many
    # accounts there, which is why accounts live on their own table.
    target_site     = Column(String(64), nullable=False, default="fineartamerica")
    # How many images a worker is expected to save per title (soft guidance).
    images_per_title = Column(Integer, nullable=True)
    # Relative share of each Photoshop batch when several projects have work
    # waiting. Equal weights split the batch evenly; a project with weight 2
    # gets twice the slots of one with weight 1.
    #
    # Without this the dispatcher takes the globally oldest images, so a large
    # older backlog in one niche starves every newer niche completely — e.g. a
    # 3,000-image movie backlog would block a brand-new celebrity pipeline for
    # about a week.
    process_weight  = Column(Integer, nullable=False, default=1)
    # How many images this project gets per turn at the UPLOAD stage, before
    # the dispatcher moves to the next project. Absolute, not proportional —
    # "40 for movies, then 40 for MUSIK, then round again".
    #
    # This is the OUTER of two rotation levels; UploadAccount.rotation_size is
    # the inner one. Without it a project with two accounts silently gets
    # double the throughput of a project with one, purely because it has more
    # accounts — which is an accident of configuration, not a decision.
    upload_turn_size = Column(Integer, nullable=True)

    # What this project calls the thing a worker saves. Movies save "posters";
    # MUSIK saves "images"; a future niche might save "designs". Every piece
    # of worker-facing copy reads this rather than hardcoding a noun, so a new
    # niche needs no template edits.
    item_noun        = Column(String(32), nullable=False, default="poster")
    item_noun_plural = Column(String(32), nullable=False, default="posters")

    is_active       = Column(Integer, nullable=False, default=1)
    notes           = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def settings_prefix(self) -> str:
        return self.slug


class WorkerNode(Base):
    """
    A remote machine allowed to run pipeline work (the Windows VPS running
    Photoshop + Selenium). Authenticates to the pipeline API with a bearer
    token; we store only a SHA-256 hash of it.

    Several nodes can be registered — e.g. one box dedicated to Photoshop
    and another to uploads, or a second box when volume grows. Each node
    declares which capabilities it has so the dispatcher only hands it work
    it can actually do.
    """
    __tablename__ = "worker_nodes"

    id            = Column(Integer, primary_key=True)
    name          = Column(String(64), unique=True, nullable=False, index=True)
    token_hash    = Column(String(64), nullable=False)
    # Comma-separated capability list: 'process,upload'
    capabilities  = Column(String(128), nullable=False, default="process,upload")
    is_enabled    = Column(Integer, nullable=False, default=1)
    # Self-reported by the node on each poll — purely diagnostic.
    hostname      = Column(String(128), nullable=True)
    agent_version = Column(String(32), nullable=True)
    last_seen_at  = Column(DateTime, nullable=True, index=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)


class UploadAccount(Base):
    """
    A marketplace account the pipeline uploads into (replaces faa_config.json).

    `password_enc` is Fernet-encrypted with PIPELINE_SECRET (see
    app/pipeline.py). It is never returned to the browser — only to an
    authenticated worker node that needs it to log in.

    `timing_json` holds the per-account Selenium waits that used to live in
    the Tkinter Settings tab (login_wait, upload_wait, ...). `selectors_json`
    optionally overrides the project-level selector map for this one account,
    which matters when a marketplace A/B-tests its upload form.
    """
    __tablename__ = "upload_accounts"

    id                = Column(Integer, primary_key=True)
    project_id        = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    name              = Column(String(64), nullable=False)
    target_site       = Column(String(32), nullable=False, default="faa")
    email             = Column(String(255), nullable=False)
    password_enc      = Column(Text, nullable=False)
    profile_url       = Column(Text, nullable=True)
    # Directory ON THE WORKER NODE holding the persistent Chrome profile
    # (session cookies). Disposable — recreated by re-login if wiped.
    chrome_profile_dir = Column(String(512), nullable=True)
    daily_limit       = Column(Integer, nullable=False, default=100)

    # ── Rotation between accounts ───────────────────────────────────────
    # Accounts take turns rather than one being drained before the next is
    # touched. `rotation_size` is how many images this account gets per turn
    # (NULL/0 = the project's upload_batch_size), and `rotation_order` sets the
    # sequence on the first pass and breaks ties afterwards.
    #
    # So "30 to A, then 40 to B, then 10 to C, then 20 to D, then back to A" is
    # four accounts with rotation_order 1..4 and rotation_size 30/40/10/20.
    #
    # After the first pass the order is driven by last_run_at (least recently
    # served goes next), which keeps the rotation going and self-corrects when
    # an account is paused or runs out of work.
    rotation_order    = Column(Integer, nullable=False, default=100)
    rotation_size     = Column(Integer, nullable=True)

    is_enabled        = Column(Integer, nullable=False, default=1)
    timing_json       = Column(Text, nullable=True)
    selectors_json    = Column(Text, nullable=True)
    # Set when a run hits bot-detection / login failure. While in the future,
    # the dispatcher refuses to hand this account any work.
    paused_until      = Column(DateTime, nullable=True)
    pause_reason      = Column(Text, nullable=True)
    last_run_at       = Column(DateTime, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by        = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_upload_account_project_name"),
    )


class ProcessedImage(Base):
    """
    One row per (saved poster → processed output). Records that Photoshop
    produced a derivative and where it now lives in permanent storage.

    `storage_path` is relative to the configured storage root (the mounted
    Hetzner Storage Box), never an absolute local path — so remounting at a
    different drive letter or migrating providers doesn't invalidate the DB.

    `script_version` is a hash of the JSX used, so when you tweak the effect
    you can tell which images came from which revision and selectively
    reprocess.

    A poster can legitimately have several rows over time (reprocessed after
    a script change); `is_current` marks the one the uploader should use.
    """
    __tablename__ = "processed_images"

    id              = Column(Integer, primary_key=True)
    saved_poster_id = Column(Integer, ForeignKey("saved_posters.id"), nullable=False, index=True)
    project_id      = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    storage_path    = Column(String(768), nullable=False)
    filename        = Column(String(512), nullable=False)
    file_size       = Column(Integer, nullable=True)
    output_width    = Column(Integer, nullable=True)
    output_height   = Column(Integer, nullable=True)
    script_version  = Column(String(64), nullable=True)
    processed_by    = Column(String(64), nullable=True)   # worker node name
    duration_ms     = Column(Integer, nullable=True)
    is_current      = Column(Integer, nullable=False, default=1, index=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    saved_poster = relationship("SavedPoster")

    __table_args__ = (
        Index("ix_processed_poster_current", "saved_poster_id", "is_current"),
    )


class UploadTracking(Base):
    """
    One row per (image → marketplace account) upload attempt lifecycle.
    Replaces faa_upload_tracking.json.

    Keyed on saved_poster_id (NOT the processed file) because the poster is
    the stable identity across reprocessing. `processed_image_id` records
    which derivative was actually sent.

    The (saved_poster_id, account_id) pair is unique: an image is uploaded
    at most once per account. Re-uploading the same image to a *different*
    account after a ban is a new row — which is exactly how account recovery
    works without touching storage or Photoshop.

    status:
      pending    — queued, not attempted yet
      uploading  — a worker claimed it (guards against double-upload)
      uploaded   — confirmed live on the marketplace
      failed     — attempt failed; eligible for retry until attempts hits the
                   configured cap, then surfaced for admin review
      removed    — was live, then taken down (copyright/DMCA)
      skipped    — admin decided never to upload this one
    """
    __tablename__ = "upload_tracking"

    id                 = Column(Integer, primary_key=True)
    saved_poster_id    = Column(Integer, ForeignKey("saved_posters.id"), nullable=False, index=True)
    processed_image_id = Column(Integer, ForeignKey("processed_images.id"), nullable=True)
    account_id         = Column(Integer, ForeignKey("upload_accounts.id"), nullable=False, index=True)
    project_id         = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    target_site        = Column(String(32), nullable=False, default="faa")

    # The exact title submitted to the marketplace, e.g. "Pulp Fiction - 1994 A".
    # Frozen at upload time so the listing can always be found again even if
    # the title template later changes.
    remote_title       = Column(String(512), nullable=True)
    # Index of this image within its title (0→A, 1→B, ...). Drives the suffix.
    letter_index       = Column(Integer, nullable=True)
    # Marketplace-side identifier / URL once known.
    remote_id          = Column(String(128), nullable=True)

    status             = Column(String(16), nullable=False, default="pending", index=True)
    attempts           = Column(Integer, nullable=False, default=0)
    last_error         = Column(Text, nullable=True)
    # Failure artefacts saved by the worker, relative to the storage root.
    last_screenshot    = Column(String(768), nullable=True)
    claimed_at         = Column(DateTime, nullable=True)
    claimed_by         = Column(String(64), nullable=True)
    uploaded_at        = Column(DateTime, nullable=True, index=True)
    removed_at         = Column(DateTime, nullable=True)
    removed_reason     = Column(Text, nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow, nullable=False)

    saved_poster    = relationship("SavedPoster")
    processed_image = relationship("ProcessedImage")

    __table_args__ = (
        UniqueConstraint("saved_poster_id", "account_id", name="uq_upload_poster_account"),
        # Drives the "how many did this account do today" quota query.
        Index("ix_upload_account_day", "account_id", "uploaded_at"),
        Index("ix_upload_status_site", "status", "target_site"),
    )


class PipelineJob(Base):
    """
    A unit of remote work, plus its log. Covers both scheduled batch runs
    and the one-off diagnostics fired from the dashboard's Test & Debug panel.

    The point of this table is that you never have to run the whole pipeline
    to debug one stage: `kind` can be a single-image test, and its log +
    result land here for inspection within seconds.

    kind:
      process        — batch Photoshop run
      upload         — batch marketplace upload run
      test_download  — fetch one title's sources to the node
      test_process   — run the JSX on exactly one image
      test_upload    — upload exactly one image, phase by phase

    status: queued → running → done | error | cancelled
    """
    __tablename__ = "pipeline_jobs"

    id            = Column(Integer, primary_key=True)
    project_id    = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    kind          = Column(String(32), nullable=False, index=True)
    status        = Column(String(16), nullable=False, default="queued", index=True)
    # Inputs (poster ids, account id, overrides) and outputs (dimensions,
    # per-phase timings, produced paths). Free-form JSON so new job kinds
    # never need a migration.
    payload_json  = Column(Text, nullable=True)
    result_json   = Column(Text, nullable=True)
    # Appended live by the worker; streamed to the dashboard's Live Console.
    log_text      = Column(Text, nullable=True)
    error         = Column(Text, nullable=True)
    progress      = Column(Integer, nullable=False, default=0)   # 0..100
    progress_note = Column(String(255), nullable=True)
    requested_by  = Column(String(64), nullable=True)
    claimed_by    = Column(String(64), nullable=True)            # worker node name
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    started_at    = Column(DateTime, nullable=True)
    finished_at   = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_job_kind_status", "kind", "status"),
    )
