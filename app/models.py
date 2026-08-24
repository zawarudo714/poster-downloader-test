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
    #   unusable   — the AI cannot render this image acceptably, ever. Set by
    #                the admin from the review gate after seeing repeated bad
    #                output. NOT a deletion: the file, the poster row, the
    #                worker's pay and the whole history stay. It is simply out
    #                of the workflow, with `unusable_reason` recording why so
    #                that finding it in three years answers its own question.
    pipeline_status    = Column(String(24), nullable=True, index=True)
    # Why an image was taken out of the pipeline permanently. Free text from
    # the admin — the reasons are judgements ("hands come out wrong every
    # time", "AI keeps adding a second person") and a fixed list would only
    # push the real reason into a note nobody reads.
    unusable_reason    = Column(Text, nullable=True)
    unusable_at        = Column(DateTime, nullable=True)
    unusable_by        = Column(String(64), nullable=True)
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


class SearchCache(Base):
    """
    One worker's search results for one title, held for the life of their claim.

    ════════════════════════════════════════════════════════════════════════
    WHY CACHE AT ALL
    ════════════════════════════════════════════════════════════════════════
    A worker toggles between the Pinterest-scoped results and the deep search
    while deciding. Without a cache each toggle is another paid query for
    results we already had. Cached, they flip freely and we pay once.

    Keyed on (title, variant) and scoped to the claim rather than a clock:
    a worker holds a title until they finish it, so that is exactly how long
    the results stay relevant. The 24h ceiling is a backstop for a claim left
    open overnight — by then Brave's thumbnail URLs may have expired anyway,
    so re-querying is the right answer rather than serving dead links.
    """
    __tablename__ = "search_cache"

    id              = Column(Integer, primary_key=True)
    master_title_id = Column(Integer, ForeignKey("master_titles.id"), nullable=False, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    variant         = Column(String(16), nullable=False)   # 'normal' | 'deep'
    payload_json    = Column(Text, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("master_title_id", "user_id", "variant",
                         name="uq_search_cache_title_user_variant"),
    )


class ApiSpend(Base):
    """
    One row per paid external API call.

    ════════════════════════════════════════════════════════════════════════
    WHY WE METER OURSELVES
    ════════════════════════════════════════════════════════════════════════
    OpenAI's image endpoint returns the ACTUAL token usage for the call, so
    the cost here is arithmetic on measured data, not an estimate. That gives
    an instant per-image figure, drives the monthly cap, and answers "what did
    today cost" without leaving the dashboard.

    It is reconciled nightly against OpenAI's own Costs API when an admin key
    is configured. The two can differ slightly — rounding, promotional
    credits, and anything spent outside this pipeline — so the reconciliation
    reports the gap rather than overwriting either number.

    Brave returns no usage data, so its rows are query-count x configured rate.
    That IS an estimate and is labelled as one.
    """
    __tablename__ = "api_spend"

    id           = Column(Integer, primary_key=True)
    service      = Column(String(24), nullable=False, index=True)   # 'openai' | 'brave'
    operation    = Column(String(48), nullable=True)                # 'image_edit' | 'search'
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    # What it was for, so a cost can be traced back to a specific image.
    saved_poster_id = Column(Integer, ForeignKey("saved_posters.id"), nullable=True, index=True)

    units        = Column(Integer, nullable=False, default=1)       # queries, or 1 image
    input_tokens  = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    # Stored as a string to avoid float drift on money.
    cost_usd     = Column(String(24), nullable=False, default="0")
    estimated    = Column(Integer, nullable=False, default=0)       # 1 = not from real usage
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_spend_service_day", "service", "created_at"),
    )


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

    # ── What this project HAS ────────────────────────────────────────────
    # The UI renders from these instead of branching on slug. `if slug ==
    # 'musik': hide the year column` is a rewrite waiting for project three;
    # a project that describes itself costs nothing to add a fourth to.
    #
    # 'photoshop' — Real Paint FX on the Windows node (movies)
    # 'gpt'       — OpenAI image edit on the Linux server (MUSIK)
    # Decides which processing settings panel the Pipeline page shows, and
    # which dispatcher picks the work up.
    processor        = Column(String(24), nullable=False, default="photoshop")

    # Whether the master sheet carries these at all. MUSIK's sheet is one
    # column of artist names, so a YEAR column and a TYPE filter are dead
    # controls that only add noise.
    has_year         = Column(Integer, nullable=False, default=1)
    has_content_type = Column(Integer, nullable=False, default=1)

    # Whether processed images wait for admin approval before uploading.
    # Adds the "Review Images" nav entry for projects that use it. Movies
    # go straight from Photoshop to upload and always have.
    has_review_gate  = Column(Integer, nullable=False, default=0)

    # Where the worker finds source images.
    #   'external' — a link out to TMDB/whatever; the worker pastes a URL back
    #   'inpage'   — the search grid inside the site (Brave)
    # Declared rather than inferred from whether some setting happens to be
    # blank: a project must never end up showing BOTH an "Open TMDB" button
    # and a search grid, or neither.
    search_mode      = Column(String(16), nullable=False, default="external")

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


class AccountProject(Base):
    """
    Which projects an upload account serves. An account exists ONCE.

    ════════════════════════════════════════════════════════════════════════
    WHY A LINK TABLE AND NOT A COLUMN
    ════════════════════════════════════════════════════════════════════════
    One FineArtAmerica account carries the movie posters AND the MUSIK
    artwork. With a single `project_id` the only way to do that was to
    create the same account twice — two rows, two Chrome profiles, two sets
    of credentials to keep in step, and a daily upload limit that the
    marketplace applies to ONE account being counted as though it were two.
    That last one is the dangerous part: two rows each believing they had
    100 uploads a day would quietly go over the real limit.

    ════════════════════════════════════════════════════════════════════════
    NO ROWS MEANS NO PROJECTS — NOT ALL OF THEM
    ════════════════════════════════════════════════════════════════════════
    This is the OPPOSITE of `user_projects`, where a worker with no rows is
    unrestricted. Read that convention across to here and every earn-only
    account would silently become an upload target for every project.

    An account with no rows is one nothing is uploaded to — the TeePublic
    accounts that just sit there earning. It still appears on Earnings,
    because reading revenue and uploading are different capabilities of the
    same account.
    """
    __tablename__ = "account_projects"

    account_id  = Column(Integer, ForeignKey("upload_accounts.id"), primary_key=True)
    project_id  = Column(Integer, ForeignKey("projects.id"), primary_key=True)
    attached_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    attached_by = Column(String(64), nullable=True)


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
    # LEGACY. Which projects this account serves now lives in
    # `account_projects`, because one account can carry several. This column
    # is kept only so old rows survive the upgrade — it is backfilled into
    # the link table on startup and NOTHING should scope a query by it.
    # Use pipeline.accounts_for_project() / project_ids_for_account().
    project_id        = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    name              = Column(String(64), nullable=False)
    target_site       = Column(String(32), nullable=False, default="fineartamerica")
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

    # ── The name the MARKETPLACE prints on the listing ──────────────────
    # Not the account name, not the login, and NOT derivable from
    # `profile_url`: one real account's profile is /profiles/elton-odhiambo
    # while its listings live at .../the-killing-2011-c-GOLDEN-REEL.html.
    #
    # FAA fills this field from the account itself, so the uploader has never
    # set it and we have never recorded it — yet the public address of every
    # listing is built from it, which is what the reconciliation sweep needs.
    # It has to be typed in once, exactly as the marketplace holds it.
    #
    # One wrong character makes every listing 404. See
    # listing_check.artist_name_suspect() — a sweep that reports everything
    # gone is treated as a broken sweep, not as thousands of takedowns.
    artist_name       = Column(String(128), nullable=True)

    is_enabled        = Column(Integer, nullable=False, default=1)
    timing_json       = Column(Text, nullable=True)
    selectors_json    = Column(Text, nullable=True)
    # Set when an UPLOAD run hits bot-detection / login failure. While in the
    # future, the dispatcher refuses to hand this account any upload work.
    paused_until      = Column(DateTime, nullable=True)
    pause_reason      = Column(Text, nullable=True)
    last_run_at       = Column(DateTime, nullable=True)

    # ── The same idea, for READING money ────────────────────────────────
    # Separate columns, deliberately, because uploading and reading earnings
    # are two capabilities of ONE account and they fail independently. A
    # Cloudflare challenge while reading TeePublic says nothing about whether
    # uploading works; a rejected upload password says nothing about whether
    # the balance can be read. Sharing one pause meant either failure
    # silenced the other, and the screen would have offered no clue which had
    # actually happened.
    #
    # Cleared by a SUCCESSFUL read, not only by the clock — otherwise an
    # account you have just fixed by hand keeps being skipped by the
    # scheduler until the timer runs out, while READ NOW works perfectly.
    # That combination is unreadable from outside.
    earnings_paused_until = Column(DateTime, nullable=True)
    earnings_pause_reason = Column(Text, nullable=True)

    # ── Banned ──────────────────────────────────────────────────────────
    # A pause is temporary and self-clearing; a ban is neither. When a
    # marketplace closes an account its listings go with it, so this is not
    # just "stop uploading here" — it means everything this account ever put
    # live is gone from the internet and has to be rebuilt somewhere else.
    #
    # Kept as its own state rather than reusing is_enabled=0, because
    # "switched off" and "destroyed, and its work needs re-listing" call for
    # completely different actions, and conflating them would make the
    # difference invisible a year later.
    #
    # The row is never deleted. It is the only record of where several
    # thousand listings used to live, and the reconciliation scanner will
    # need it to explain what it finds on the marketplace.
    banned_at         = Column(DateTime, nullable=True)
    banned_reason     = Column(Text, nullable=True)
    # Which account took over its catalogue, if any. Answers "where did this
    # artist's listing go" in one hop.
    replaced_by_id    = Column(Integer, ForeignKey("upload_accounts.id"), nullable=True)
    # When the earnings reader last got through to this account. Kept on the
    # account rather than in settings because "which one is stale" is a
    # per-account question — one bad password must be visible as one bad
    # account, not as a silent gap in the totals.
    last_earnings_read_at = Column(DateTime, nullable=True)
    # What the marketplace itself says it owes, as text (money is never a
    # float here). This is a FACT where every figure we compute is a
    # derivation — it is both the honest "next payout" number and the
    # checksum on our own arithmetic: our sales minus our payouts must land
    # on it, and when it does not we have missed rows.
    marketplace_balance   = Column(String(24), nullable=True)
    # How many uploads in a row have failed for a reason that MIGHT be
    # systemic. Reset to zero by any success, so it measures a run of
    # failures rather than a total. See pipeline.report_upload_failure for
    # why a single one is no longer enough to park the account.
    consecutive_failures  = Column(Integer, nullable=False, default=0)
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

    # ── Admin review of AI output ────────────────────────────────────────
    # Only meaningful for projects with has_review_gate. Photoshop output is
    # deterministic and has always gone straight to upload.
    #   NULL       — no gate, or not yet looked at
    #   'pending'  — waiting for the admin
    #   'approved' — released for upload
    #   'rerun'    — rejected; a fresh generation is queued
    review_status   = Column(String(16), nullable=True, index=True)
    reviewed_at     = Column(DateTime, nullable=True)
    reviewed_by     = Column(String(64), nullable=True)
    # Web-sized copy for the review screens. Serving the 4000px print file
    # would be ~6 MB per screen; this is ~120 KB. Relative to storage_root
    # like storage_path.
    preview_path    = Column(String(768), nullable=True)
    # Which generation this was, for an image that has been rerun.
    attempt         = Column(Integer, nullable=False, default=1)

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
    target_site        = Column(String(32), nullable=False, default="fineartamerica")

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

    # ── What the marketplace ITSELF says about this listing ─────────────
    #
    # Deliberately separate from `status` above, and the distinction is the
    # whole point of the reconciliation sweep: `status` is what WE believe,
    # these are what we OBSERVED. Writing an observation straight into
    # `status` would destroy the disagreement, which is the only interesting
    # thing here — "we think it is up and it is not" is a finding, and a
    # finding needs both halves to still exist.
    #
    # `status` is only changed when a PERSON explains a finding, through the
    # existing removed/removed_reason columns.
    #
    #   live    — the listing's page loads
    #   gone    — a real HTTP 404
    #   unknown — we were blocked or the site had a moment. NOT evidence.
    listing_status     = Column(String(16), nullable=True, index=True)
    listing_http       = Column(Integer, nullable=True)
    listing_checked_at = Column(DateTime, nullable=True, index=True)

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


# ════════════════════════════════════════════════════════════════════════════
#  EARNINGS
# ════════════════════════════════════════════════════════════════════════════
# Read-only mirror of what a marketplace says it owes and has paid. Nothing
# here is produced by this app — every row is copied from a page on their
# site — so nothing here is ever authoritative about our own pipeline. It
# answers one question the pipeline cannot: is any of this making money.

class MarketplaceSnapshot(Base):
    """
    One reading of a marketplace that publishes TOTALS instead of events.

    ════════════════════════════════════════════════════════════════════════
    WHY A SECOND SHAPE EXISTS AT ALL
    ════════════════════════════════════════════════════════════════════════
    `LedgerEntry` is one row per thing that happened, which is the better
    model and the one to prefer. TeePublic makes it impossible: its account
    page publishes four running totals and no list of sales, so there are no
    events to store.

    So this table stores what they DO publish, once a day, absolutely — and
    "earned on Tuesday" becomes Tuesday's `total_earned` minus Monday's.
    `total_earned` is lifetime and only ever climbs, which is what makes that
    subtraction trustworthy where `month_to_date` (zeroed on the 1st) would
    not be.

    Still ABSOLUTE, never deltas, for exactly the reason the ledger is: a
    stored delta cannot survive the month boundary, and cannot be recomputed
    afterwards if it turns out to be wrong.

    ════════════════════════════════════════════════════════════════════════
    ONE ROW PER ACCOUNT PER LOCAL DAY
    ════════════════════════════════════════════════════════════════════════
    Re-reading the same day overwrites rather than appends, so pressing READ
    NOW five times leaves five better readings of one day, not five days.

    `covers_days` is how many days of earning the difference from the
    PREVIOUS row actually represents. Normally 1. If the worker machine was
    off for three nights it is 4, and the total is right while the daily
    breakdown for those days is gone for good — an honest gap rather than an
    invented average. Anything drawing a graph must read this.
    """
    __tablename__ = "marketplace_snapshots"

    id            = Column(Integer, primary_key=True)
    account_id    = Column(Integer, ForeignKey("upload_accounts.id"),
                           nullable=False, index=True)
    marketplace   = Column(String(32), nullable=False, index=True)
    # The LOCAL date this reading belongs to, which is what the owner thinks
    # in. Stored as a date, not a timestamp, because it is the key.
    taken_on      = Column(Date, nullable=False, index=True)
    taken_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Money as TEXT, like every other amount in this app. Floats accumulate
    # error and these exist to be subtracted from one another.
    owed          = Column(String(24), nullable=True)   # their own "unpaid" figure
    next_payment  = Column(String(24), nullable=True)
    next_payment_period = Column(String(32), nullable=True)
    month_to_date = Column(String(24), nullable=True)
    month_to_date_period = Column(String(32), nullable=True)
    total_earned  = Column(String(24), nullable=True)   # lifetime, monotonic
    items_sold    = Column(Integer, nullable=True)      # lifetime, monotonic

    # Earned since the previous snapshot, and how many days that spans.
    # Derived on write because it needs the previous row, and re-derivable at
    # any time from the absolute figures if it is ever wrong.
    earned_since  = Column(String(24), nullable=True)
    covers_days   = Column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("account_id", "taken_on", name="uq_snapshot_account_day"),
        Index("ix_snapshot_account_day", "account_id", "taken_on"),
    )


class LedgerEntry(Base):
    """
    One line from a marketplace's account ledger.

    ════════════════════════════════════════════════════════════════════════
    WHY A LEDGER AND NOT A SALES TABLE
    ════════════════════════════════════════════════════════════════════════
    FineArtAmerica's Balance page is a running ledger: sales credit it,
    payouts debit it, and every row carries the balance afterwards. Copying
    that shape rather than inventing our own gives three things for free:

      · payouts are events, so "what have I actually been paid" is answerable
      · a refund arrives as its own row rather than a sale silently vanishing,
        which is the difference between knowing and inferring
      · the running balance is a checksum — our arithmetic must land on their
        figure, and if it does not we have missed something

    ════════════════════════════════════════════════════════════════════════
    MONEY IS TEXT
    ════════════════════════════════════════════════════════════════════════
    Amounts are stored as strings and converted to Decimal when summed, the
    same rule ApiSpend follows. Floats accumulate error, and this table
    exists precisely to be totalled.
    """
    __tablename__ = "ledger_entries"

    id             = Column(Integer, primary_key=True)
    account_id     = Column(Integer, ForeignKey("upload_accounts.id"),
                            nullable=False, index=True)
    # Denormalised from the account so a marketplace filter never needs a
    # join, and so the row still reads correctly if an account is renamed.
    marketplace    = Column(String(32), nullable=False, index=True)

    occurred_at    = Column(DateTime, nullable=False, index=True)
    # 'sale' | 'payment' | 'refund' | 'other'. Anything we do not recognise
    # is stored as 'other' with its raw description rather than being forced
    # into a bucket — an unknown row we can see beats a wrong one we cannot.
    entry_type     = Column(String(16), nullable=False, index=True)

    remote_order_id = Column(String(64), nullable=True, index=True)
    description     = Column(Text, nullable=True)

    # What was sold, split out of the description. Kept raw as well, because
    # the split is a guess about their formatting and the raw text is not.
    artwork_name   = Column(String(255), nullable=True, index=True)
    product        = Column(String(160), nullable=True)

    credit         = Column(String(24), nullable=False, default="0")
    debit          = Column(String(24), nullable=False, default="0")
    balance_after  = Column(String(24), nullable=True)

    # ── From the order's Details panel, for sales only ──────────────────
    # The ledger gives the money; these give the shape of the sale. Fetched
    # once, when the row is first seen, because it is a request per order.
    website        = Column(String(64), nullable=True)   # fineartamerica | pixels
    quantity       = Column(Integer, nullable=True)
    gross_price    = Column(String(24), nullable=True)   # before their discount
    discount       = Column(String(24), nullable=True)
    buyer_location = Column(String(255), nullable=True)
    details_read   = Column(Integer, nullable=False, default=0)   # 0/1

    # ── Attribution ─────────────────────────────────────────────────────
    # Which of OUR designs this was. NULL means unmatched, which is a state
    # to work through rather than an error — the sale still counts toward
    # account totals either way.
    master_title_id = Column(Integer, ForeignKey("master_titles.id"),
                             nullable=True, index=True)
    # 'exact' | 'alias' | 'suffix' | 'name' — recorded so a bad rule can be
    # found later by looking at how its matches were made.
    match_method    = Column(String(16), nullable=True)

    # Natural key for "have we seen this row already". Sales use the order
    # id; payouts have none, so they use type+timestamp+amount. Unique per
    # account, which is what makes re-reading a page free of duplicates.
    dedupe_key     = Column(String(128), nullable=False)

    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "dedupe_key", name="uq_ledger_account_key"),
        Index("ix_ledger_account_time", "account_id", "occurred_at"),
        Index("ix_ledger_type_time", "entry_type", "occurred_at"),
    )


class TitleAlias(Base):
    """
    A marketplace product name, permanently pointed at one of our designs.

    Exists because matching cannot be perfect and guessing is worse than
    asking. A listing title can differ from what we stored for three
    reasons — the marketplace strips characters, it truncates at 100, and
    the oldest listings were uploaded by hand before any of these rules
    existed. Fuzzy matching would eventually attribute a sale to the wrong
    design, which is worse than attributing it to none, because you would
    act on it.

    So: match what can be matched exactly, show the rest, and let one
    correction fix every past and future sale of that design at once.
    """
    __tablename__ = "title_aliases"

    id              = Column(Integer, primary_key=True)
    marketplace     = Column(String(32), nullable=False, index=True)
    # Exactly as the marketplace writes it, before any normalisation. That
    # is what future rows will be compared against.
    artwork_name    = Column(String(255), nullable=False)
    master_title_id = Column(Integer, ForeignKey("master_titles.id"),
                             nullable=False, index=True)
    created_by      = Column(String(64), nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("marketplace", "artwork_name", name="uq_alias_market_name"),
    )


class WallPath(Base):
    """
    One recorded mouse movement that ends on a marketplace's dismiss control.

    ════════════════════════════════════════════════════════════════════════
    WHY A RECORDING AND NOT A SELECTOR
    ════════════════════════════════════════════════════════════════════════
    TeePublic serves a full-page interstitial whose "No Thanks" checkbox sits
    inside a CLOSED shadow root. Closed means sealed: Selenium cannot find the
    element, and JavaScript running on the page cannot reach it either. There
    is no selector to write, and no amount of waiting produces one.

    A real mouse click does not need one. It aims at a POSITION, and the
    browser works out what is underneath — which is how a person clicking it
    succeeds. So the position is what we store, and the owner supplies it by
    recording himself moving to it.

    ════════════════════════════════════════════════════════════════════════
    PAGE COORDINATES, NEVER SCREEN COORDINATES
    ════════════════════════════════════════════════════════════════════════
    `points_json` is [[x, y, ms], ...] measured from the TOP-LEFT OF THE WEB
    PAGE, not of the monitor. The recorder converts as it captures, by asking
    Chrome where the page currently sits.

    That distinction is the whole reason this survives contact with reality.
    Screen coordinates break when the window moves, when it is resized, when
    Chrome grows a bookmarks bar, or when the desktop resolution changes —
    and they break SILENTLY, clicking empty space and reporting success.

    ════════════════════════════════════════════════════════════════════════
    PER MARKETPLACE, NOT PER PROJECT
    ════════════════════════════════════════════════════════════════════════
    The wall belongs to TeePublic. Both niches meet the same one, and a third
    niche would too, so these hang off the marketplace name exactly as
    selectors and capabilities do. A recording is a fact about a WEBSITE.
    """
    __tablename__ = "wall_paths"

    id           = Column(Integer, primary_key=True)
    marketplace  = Column(String(32), nullable=False, index=True)
    label        = Column(String(64), nullable=True)

    # [[x, y, ms_since_start], ...] in page coordinates. A few hundred points
    # for a typical path; stored as text because nothing ever queries inside
    # it — it is replayed whole or not at all.
    points_json  = Column(Text, nullable=False)
    duration_ms  = Column(Integer, nullable=False, default=0)

    # The page size it was recorded at. Kept so a recording made at a
    # different window size can be SPOTTED rather than silently replayed
    # against a layout it never saw.
    page_width   = Column(Integer, nullable=True)
    page_height  = Column(Integer, nullable=True)

    # Outcome history. Never used to pick a path — the rotation is strictly
    # sequential — only to let the admin see WHICH recording is the bad one
    # when some work and some do not.
    use_count    = Column(Integer, nullable=False, default=0)
    fail_count   = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)

    is_enabled   = Column(Integer, nullable=False, default=1)
    created_by   = Column(String(64), nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_wall_paths_market", "marketplace", "is_enabled"),
    )


class StoreScanRun(Base):
    """
    One visibility sweep of a marketplace: scan, deactivate, reactivate.

    ════════════════════════════════════════════════════════════════════════
    WHY A RUN AND NOT THREE BUTTONS
    ════════════════════════════════════════════════════════════════════════
    The three stages are not independent. Reactivation must put back EXACTLY
    what deactivation took down — not "whatever is on the inactive page",
    which on one real account is 379 designs the owner turned off himself,
    for his own reasons, over months. A run is what carries that list from
    one stage to the next.

    It is also what holds the rest of the pipeline still. The hold belongs to
    the RUN, not to a switch: a run that finishes, fails or is abandoned
    releases it on the way out, so there is no second edge to be lost and no
    state that can be left in the wrong position. That lesson came from the
    quiet window, which was built as a window for the same reason.

    ════════════════════════════════════════════════════════════════════════
    IT WAITS FOR A PERSON, TWICE, ON PURPOSE
    ════════════════════════════════════════════════════════════════════════
    `reviewing` and `confirming` are stages where nothing happens until the
    admin presses a button. Deactivating a live listing costs money if it is
    wrong, and a scan that misread the site would otherwise take the whole
    catalogue down unattended.
    """
    __tablename__ = "store_scan_runs"

    id          = Column(Integer, primary_key=True)
    marketplace = Column(String(32), nullable=False, index=True)

    #   scanning     — reading every design, hours
    #   reviewing    — waiting for the admin to approve the missing list
    #   deactivating — turning the missing ones off
    #   confirming   — waiting for the admin again
    #   reactivating — turning back on exactly what we turned off
    #   done | failed | abandoned
    status      = Column(String(24), nullable=False, default="scanning", index=True)
    stage_note  = Column(Text, nullable=True)

    started_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_by  = Column(String(64), nullable=True)
    finished_at = Column(DateTime, nullable=True)

    # Which stages have been dispatched to the node. Not a status: a stage can
    # be running while the run's status still says so, and this is what stops
    # a second dispatch of the same stage.
    scan_started_at        = Column(DateTime, nullable=True)
    deactivate_started_at  = Column(DateTime, nullable=True)
    reactivate_started_at  = Column(DateTime, nullable=True)

    # ── Automatic mode ──────────────────────────────────────────────────
    # `auto` means the stages hand over to each other with no button in
    # between. The two gates exist because deactivating a live listing costs
    # money if the scan misread the site — so this is opt-in, and once you
    # trust a few manual sweeps it saves an evening of waiting.
    auto        = Column(Integer, nullable=False, default=0)

    # PAUSE is not a stop. A paused run keeps everything it has found and
    # holds its place in the sequence; the pipeline is RELEASED while it
    # waits, which is the whole point — you pause precisely because you want
    # Photoshop and the daily uploads to have the machine for a while.
    paused_at   = Column(DateTime, nullable=True)
    paused_by   = Column(String(64), nullable=True)

    # ── Waiting out a transient failure ─────────────────────────────────
    # The wall, a maintenance page, a timeout: the far side having a moment.
    # Rather than throwing away the night's work, the run sleeps until
    # `retry_at` and tries again, with the gap growing each time. It does NOT
    # hold the pipeline while it waits — an hour of holding Photoshop for
    # nothing would be the opposite of helpful.
    # ── HOW MANY ACCOUNTS THIS STAGE IS WAITING ON ──────────────────────
    # Deactivation and reactivation work ONE ACCOUNT AT A TIME, and each
    # account reports when it finishes. Without counting them, the FIRST
    # account to finish told the server the whole stage was done: the run
    # advanced to reactivating while a second account was still switching
    # designs OFF, and those designs were never on the reactivate list. Live
    # listings left switched off, with nothing on screen saying so.
    #
    # These two are for the SCREEN — "account 3 of 5". They are deliberately
    # not what ends the stage. The stage ends when there is no account left
    # with work in it, which is derived from the catalogue every time and so
    # cannot drift out of step with reality the way a counter can.
    stage_jobs_total = Column(Integer, nullable=False, default=0)
    stage_jobs_done  = Column(Integer, nullable=False, default=0)

    # ── THE ONE ACCOUNT CURRENTLY BEING WORKED ──────────────────────────
    # The first version created every account's job UP FRONT. Stopping the
    # run then did nothing to them: the node calmly claimed the next queued
    # job and carried on switching designs off for another two hours while
    # the screen said "abandoned". One at a time means there is only ever one
    # thing to stop — and the node is serial anyway, so nothing is lost.
    stage_account_id = Column(Integer, nullable=True)
    # Re-dispatches of the CURRENT account after its job died without
    # reporting. Reset the moment an account completes, so this is "how many
    # times has this one account failed to start", not a lifetime total.
    stage_attempts   = Column(Integer, nullable=False, default=0)

    retry_at    = Column(DateTime, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    retry_note  = Column(Text, nullable=True)

    #   full | missing_only | continue
    # A recheck after reactivation only needs the designs that were missing,
    # which turns a six-hour sweep into a few minutes.
    scan_mode   = Column(String(16), nullable=False, default="full")


class StoreDesign(Base):
    """
    One design in one run, keyed on the marketplace's own numeric ID.

    ════════════════════════════════════════════════════════════════════════
    THE ID IS THE KEY, AND THAT IS NOT A DETAIL
    ════════════════════════════════════════════════════════════════════════
    It comes free: TeePublic puts it in the design's own address
    (`/t-shirt/86734220-tomb-raider`), which the store listing already gives
    us. Nothing extra is fetched to get it.

    Everything downstream matches on it — is this design in the search
    results, which tile do we deactivate, which page do we republish. The
    previous tool compared URLs instead, and a design that was sitting on
    page one of the results read MISSING because the store's copy of the link
    carried `?store_id=4129428` and the search result's copy did not. A
    number cannot differ by a query parameter, a relative path, a renamed
    slug or a trailing slash.
    """
    __tablename__ = "store_designs"

    id          = Column(Integer, primary_key=True)
    run_id      = Column(Integer, ForeignKey("store_scan_runs.id"),
                         nullable=False, index=True)
    account_id  = Column(Integer, ForeignKey("upload_accounts.id"),
                         nullable=False, index=True)

    design_id   = Column(String(32), nullable=False, index=True)   # "86734220"
    url         = Column(Text, nullable=True)
    title       = Column(String(255), nullable=True)
    search_tag  = Column(String(255), nullable=True)   # what we search for

    #   pending | visible | missing | under_review | error
    status      = Column(String(16), nullable=False, default="pending", index=True)
    error       = Column(Text, nullable=True)
    checked_at  = Column(DateTime, nullable=True)

    # Set only when WE did it. Reactivation reads these, never the
    # marketplace's inactive list — see the run's docstring.
    deactivated_at   = Column(DateTime, nullable=True)
    reactivated_at   = Column(DateTime, nullable=True)
    action_error     = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "design_id", name="uq_store_design_run"),
        Index("ix_store_design_run_status", "run_id", "status"),
    )


class StoreListing(Base):
    """
    Our catalogue of a marketplace account's designs, kept between runs.

    ════════════════════════════════════════════════════════════════════════
    WHY THIS IS NOT PER RUN
    ════════════════════════════════════════════════════════════════════════
    `StoreDesign` (above, now superseded) held one row per design PER RUN, so
    every sweep started from nothing and the interesting questions could not
    be asked at all: has this design been missing for three sweeps running?
    Did the account gain designs since last week? Did one disappear?

    A catalogue answers those. A scan now UPDATES it rather than rebuilding
    it, which also means the first sweep is what creates it and every later
    sweep only records what changed.

    ════════════════════════════════════════════════════════════════════════
    "MISSING" IS NOT ALWAYS BROKEN — HENCE THE COUNTERS
    ════════════════════════════════════════════════════════════════════════
    The visibility check searches a design's own primary tag and pages 25
    deep. For a specific tag that is conclusive. For a tag like "Queen",
    which has tens of thousands of results, a perfectly healthy design sits
    far beyond page 25 and reads MISSING every single time — and would be
    deactivated and reactivated forever, achieving nothing.

    So `fix_attempts` counts how many times we have already tried the
    deactivate/reactivate cure on this design. A design still missing after
    several is far more likely to have a vague tag than a broken listing, and
    the screen says so instead of quietly looping.

    `excluded` is the owner's answer to that: designs he has checked by hand
    and does not want scanned. Reversible, because a tag can be edited.
    """
    __tablename__ = "store_listings"

    id          = Column(Integer, primary_key=True)
    account_id  = Column(Integer, ForeignKey("upload_accounts.id"),
                         nullable=False, index=True)
    marketplace = Column(String(32), nullable=False, index=True)
    design_id   = Column(String(32), nullable=False, index=True)

    url         = Column(Text, nullable=True)
    title       = Column(String(255), nullable=True)
    search_tag  = Column(String(255), nullable=True)

    # ── What the catalogue is for ───────────────────────────────────────
    # first_seen/last_seen answer "what did this account gain or lose".
    # A design in our catalogue that stops appearing in the store listing
    # has been deleted at the marketplace — kept as a row with `removed_at`
    # set, never deleted here, because the history is the point.
    first_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    removed_at    = Column(DateTime, nullable=True)

    #   unknown | visible | missing | error
    status        = Column(String(16), nullable=False, default="unknown",
                           index=True)
    status_error  = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)

    # Consecutive scans reporting missing. Reset to 0 the moment it is seen,
    # so this is "how long has it been broken", not "how often ever".
    consecutive_missing = Column(Integer, nullable=False, default=0)
    # Completed deactivate+reactivate cycles. The number that separates "a
    # listing that fell out of the index" from "a tag too vague to search".
    fix_attempts  = Column(Integer, nullable=False, default=0)
    last_fixed_at = Column(DateTime, nullable=True)

    # ── In-flight state for the current cure ────────────────────────────
    # Set when WE turn it off, cleared when we turn it back on. Reactivation
    # works from these and never from the marketplace's inactive list, which
    # on one real account holds 379 designs the owner turned off himself.
    deactivated_at = Column(DateTime, nullable=True)
    action_error   = Column(Text, nullable=True)
    # WHEN that failure happened, and it is load-bearing rather than
    # decorative. The stage now ends when no account has any work left, and
    # work is derived from the catalogue — so a design that cannot be
    # switched off would be handed out again, fail again, and loop forever.
    # Comparing this against the run's start date is what makes a failure
    # count as "already tried this run" while still allowing a fresh attempt
    # next week.
    action_error_at = Column(DateTime, nullable=True)

    # ── The owner's override ────────────────────────────────────────────
    excluded       = Column(Integer, nullable=False, default=0, index=True)
    exclude_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("account_id", "design_id", name="uq_listing_account_design"),
        Index("ix_listing_account_status", "account_id", "status"),
    )


class ListingSweep(Base):
    """
    One pass over a marketplace asking "is each listing we believe in still
    there?". Manual only — the owner starts it when he wants it.

    ════════════════════════════════════════════════════════════════════════
    THIS IS NOT A StoreScanRun, AND INHERITING ONE WOULD BE WRONG
    ════════════════════════════════════════════════════════════════════════
    The TeePublic run carries stages, a review gate, a confirm gate, a
    deactivate/reactivate cure, per-account jobs and a pipeline hold. Every
    one of those is DEAD here, and copying them would have meant carrying
    machinery that can only ever be switched off:

      · There are no stages. It reads and reports; nothing is changed on the
        marketplace, so there is nothing to approve before it happens.
      · There is no cure. A FineArtAmerica listing is live or deleted — no
        hidden state, so nothing to switch off and back on.
      · It does not hold the pipeline. HEAD requests cost nothing and change
        nothing; making Photoshop wait an hour for them would be pure loss.

    What is left is genuinely small: a start time, a status, and a note.

    ════════════════════════════════════════════════════════════════════════
    EVERY FIGURE IS DERIVED, NOTHING IS COUNTED
    ════════════════════════════════════════════════════════════════════════
    There is no `checked` column and no `total` column on purpose. Progress
    is "how many upload rows carry a `listing_checked_at` at or after
    `started_at`", asked of the table each time.

    That is the lesson from the deactivation stage, where a counter kept by
    hand went out of step and 178 live listings were left switched off while
    the screen showed everything as fine. A count has to be maintained by
    every path that touches it; a query is simply true.
    """
    __tablename__ = "listing_sweeps"

    id          = Column(Integer, primary_key=True)
    marketplace = Column(String(32), nullable=False, index=True,
                         default="fineartamerica")

    #   running | done | failed | abandoned
    status      = Column(String(16), nullable=False, default="running",
                         index=True)
    note        = Column(Text, nullable=True)

    started_at  = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_by  = Column(String(64), nullable=True)
    finished_at = Column(DateTime, nullable=True)

    # Chunks dispatched to the worker machine that never reported. Lets a
    # stalled sweep retry a few times and then give up saying so, rather
    # than sitting "running" for ever with nothing working on it.
    attempts    = Column(Integer, nullable=False, default=0)
