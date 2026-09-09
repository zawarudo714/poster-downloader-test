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

    # ── Two names the sheet supplies, because no rule can rebuild them ──
    # A title is one string here and THREE in practice: what the worker
    # reads, what gets searched for, and what the marketplace lists. For the
    # movie niche the last two were derivable — the search was the title and
    # the listing was the title plus a template — so one column was enough.
    #
    # Travel broke that. "Niagara Falls" is searched for as "Niagara Falls
    # USA" and "Taj Mahal" as "Taj Mahal Agra India": one gained a country,
    # the other a city AND a country, decided by different rules over the
    # whole catalogue. No single pattern reproduces both, so the answer has
    # to travel with the row rather than be recomputed from it.
    #
    # BOTH ARE OPTIONAL AND BOTH FALL BACK. A sheet that does not supply
    # them leaves them NULL and every caller behaves exactly as it did
    # before — which is what keeps the movie-shaped path, and any future
    # niche that does not need them, working untouched.
    search_query      = Column(Text, nullable=True)
    # What to type into the marketplace. NOT the same as
    # UploadTracking.remote_title: that one records what was actually SENT,
    # after the template and FAA's own folding, and is what the listing
    # checker compares against. This is only the base name we intend to
    # send. Keep the distinction — one is a record, the other an intention.
    marketplace_title = Column(String(512), nullable=True)

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
    # 'normal' · 'deep' · 'p:<12 hex>' for one of the owner's own search
    # phrasings. The phrasing variant is a HASH OF THE WORDS rather than the
    # button's position, so editing a phrasing misses the cache by itself and
    # nobody has to remember to clear anything. 14 characters — the column has
    # 16, so lengthening that hash needs this column widened first.
    variant         = Column(String(16), nullable=False)
    payload_json    = Column(Text, nullable=False)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("master_title_id", "user_id", "variant",
                         name="uq_search_cache_title_user_variant"),
    )


# The ApiSpend table was REMOVED in v172, with all the spend metering.
# OpenAI's own usage page is where the owner reads what generation costs;
# our copy only ever duplicated it less accurately, and the nightly
# comparison against their Costs API reported the whole ACCOUNT rather
# than this app, so it flagged his unrelated usage as a discrepancy every
# month. On an existing install the `api_spend` table stays behind
# unused — create_all() never drops anything — and a fresh database
# simply never makes it.


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
#  4. THE DATABASE IS THE ONLY SOURCE OF TRUTH. Do not reintroduce sidecar
#     state files. An earlier incarnation of this system kept its state in
#     JSON files beside the images, and every one of them became a second
#     record that could disagree with the first.
# ═════════════════════════════════════════════════════════════════════════════


class Project(Base):
    """
    A niche / workflow. One row per (content vertical + processing style).

    Project 1 is seeded from the registry in `pipeline.PROJECT_DEFS` (
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
    source_site     = Column(String(64), nullable=True)       # 'brave' | 'pinterest' | ...
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
    #   'external' — a link out to another site; the worker pastes a URL back
    #   'inpage'   — the search grid inside the site (Brave)
    # Declared rather than inferred from whether some setting happens to be
    # blank: a project must never end up showing BOTH an "Open source" button
    # and a search grid, or neither.
    search_mode      = Column(String(16), nullable=False, default="external")
    # ── DOES THIS PROJECT SEND THE WORKER TO AN OUTSIDE SITE? ────────────
    #
    # SEPARATE from search_mode, and that separation is the point. The code
    # used to derive this from "search_mode != 'inpage'", which made one
    # field answer two questions — fine while every project either searched
    # in-page or went outside, wrong the moment travel needed both.
    #
    # Travel searches Brave in-page AND offers a Google link, because Brave's
    # picture catalogue is the thinner of the two. One field could not say
    # that. This is the retrofit lesson in miniature: before reusing a field,
    # ask whether the thing it describes can ever be true in more than one
    # way at once.
    has_source_link  = Column(Integer, nullable=False, default=0)

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
    # ── THE MARKETPLACE'S OWN COUNT OF SWITCHED-OFF DESIGNS ─────────────
    # Read from the store page while signed in, at both ends of a switching
    # turn. Exactly the same job `marketplace_balance` does above: the one
    # number that comes from outside our own records, and therefore the only
    # one that can catch us believing something that is not so.
    #
    # NULL means "not read", never "none are off". Collapsing those two
    # would report a healthy account as evidence of a bug.
    inactive_count        = Column(Integer, nullable=True)
    inactive_checked_at   = Column(DateTime, nullable=True)
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
    #   NULL         — no gate, or not yet looked at
    #   'pending'    — waiting for the admin
    #   'approved'   — released for upload
    #   'rerun'      — rejected; a fresh generation is queued
    #   'unusable'   — this poster can never be used
    #   'discarded'  — another generation was approved instead, and THIS
    #                  one's files have been deleted from the archive to
    #                  stop four print files piling up per poster. The row
    #                  stays as the record that the generation happened and
    #                  what it cost; only the pictures are gone. Kept apart
    #                  from 'superseded' because the version picker has to
    #                  know which ones it can still show.
    #   'superseded' — another generation of the same poster was chosen.
    #                  Added 2026-09-09 with the version picker. It exists so
    #                  no row can be left on 'pending' with nobody waiting on
    #                  it: the queue selects on 'pending', so a forgotten
    #                  sibling would come back round as work that has already
    #                  been decided.
    review_status   = Column(String(16), nullable=True, index=True)
    reviewed_at     = Column(DateTime, nullable=True)

    # ── The transparent master, and the colour flattened onto it ─────────
    #
    # gpt-image-2 in `background: transparent` mode renders differently, and
    # BETTER for this niche — MEASURED by the owner 2026-09-05, and the whole
    # reason the setting is on. The transparency is a side effect he flattens
    # away, not the point.
    #
    # `master_path` keeps that transparent original untouched and un-enlarged.
    # `storage_path` holds the finished print file: flattened onto
    # `background_color`, then upscaled.
    #
    # TWO FILES RATHER THAN ONE, on purpose. Most posters look right on black
    # and never need a second thought, but some do — a semi-transparent sky
    # goes muddy on black and correct on its own blue. Keeping the master
    # means changing that decision costs a local re-render instead of paying
    # OpenAI for the picture again.
    #
    # FLATTEN BEFORE UPSCALING. With the alpha already gone there is nothing
    # for the resize to average the hidden colour into. Chosen for safety
    # rather than from an observed fault — see imagefetch.flatten_onto().
    master_path      = Column(String(768), nullable=True)
    background_color = Column(String(16), nullable=True)
    reviewed_by     = Column(String(64), nullable=True)
    # Web-sized copy for the review screens. Serving the 4000px print file
    # would be ~6 MB per screen; this is ~120 KB. Relative to storage_root
    # like storage_path.
    preview_path    = Column(String(768), nullable=True)
    # Which generation this was, for an image that has been rerun.
    attempt         = Column(Integer, nullable=False, default=1)

    # ── THIS POSTER'S OWN SIGNATURE ADJUSTMENTS ─────────────────────────
    #
    # JSON, not four columns, and for the same reason PipelineJob.payload_json
    # is free-form: the second thing anybody wants to adjust — a rotation, a
    # second mark, a per-account signature — should be a new key rather than
    # a migration. NULL means "use the project's defaults", which is the
    # normal state for almost every poster.
    #
    # Keys are listed in app/signature.py, which is the only place that reads
    # them, so the shape cannot drift between the writer and the reader.
    signature_json  = Column(Text, nullable=True)
    # WHAT WAS ACTUALLY PAINTED, so a second approval of an unchanged poster
    # does not rebuild several megabytes for nothing. Same shape as
    # `background_color`: the record of a decision already carried out,
    # never a request for one. Comparing it with what the settings say NOW
    # is how the rebuild decides whether it has anything to do.
    signature_applied = Column(Text, nullable=True)

    # THE COLOUR THE ADMIN HAS CHOSEN BUT NOT YET RELEASED.
    #
    # `background_color` beside it is the colour actually FLATTENED INTO the
    # file, and `_build_print_file` compares against it to decide whether it
    # has any work to do. So the chosen colour cannot be written there early:
    # doing that would tell the builder the colour was already applied and it
    # would skip the flatten, shipping the old colour silently.
    #
    # This is the same pair as `signature_json` (chosen) beside
    # `signature_applied` (painted), and it exists for the same reason. Two
    # columns here are not two records of one fact — "what I want" and "what
    # was done" are different facts, and the whole point is to compare them.
    background_chosen = Column(String(16), nullable=True)

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

    # ── "I HAVE LOOKED AT THIS ONE. STOP TELLING ME." ───────────────────
    #
    # A finding the owner has settled by hand — he opened the address,
    # decided what it meant, and wrote down why. Without this, a listing he
    # had already explained came back on the next sweep, and every sweep
    # after that, saying the same thing. A list that reports the same
    # settled item for ever is a list nobody reads, and then a real finding
    # sits in it unnoticed.
    #
    # THE ACKNOWLEDGEMENT IS TIED TO THE OBSERVATION IT ANSWERED, which is
    # the whole design and the reason this is two columns rather than a
    # tickbox. `listing_ack_status` stores WHAT was acknowledged — "I know
    # this one reads gone". A later sweep that finds the same thing stays
    # quiet. A later sweep that finds something DIFFERENT — the page is
    # loading again, or we were suddenly blocked — is new information, so
    # the row speaks up again on its own.
    #
    # Nothing has to be un-ticked and nothing expires: same reason the quiet
    # window is a window rather than a switch. Derive the silence from the
    # data; never store "ignore me for ever".
    listing_note       = Column(Text, nullable=True)
    listing_ack_status = Column(String(16), nullable=True)
    listing_ack_at     = Column(DateTime, nullable=True)
    listing_ack_by     = Column(String(64), nullable=True)

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

    # ── THE HEARTBEAT, AND WHY started_at COULD NOT BE ONE ──────────────
    # Stamped every time the node writes a log line, which for the long
    # stages is once per design. "Is this job alive" must be asked of the
    # last thing it SAID, never of when it began.
    #
    # Both the claim reaper and the stalled-run sweeper used `started_at`
    # against a 45-minute timeout. A store_deactivate job legitimately runs
    # for an hour — measured, and written down — so at minute 45 a perfectly
    # healthy job reporting every sixteen seconds was declared abandoned. On
    # 2026-08-24 that cancelled a live job with 8 designs left, dispatched
    # those 8 again, and produced a confusing "already inactive" failure.
    last_report_at = Column(DateTime, nullable=True)
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
    Amounts are stored as strings and converted to Decimal when summed.
    Floats accumulate error, and this table exists precisely to be totalled.
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
    # The marketplace's OWN word for this row, verbatim. `entry_type` is our
    # reading of it and can be wrong; this is the evidence. Without it an
    # 'other' row cannot be diagnosed — see the $6.00 Highlander row,
    # 2026-08-27, whose Type word was lost and so could not be added to
    # classify().
    raw_type       = Column(String(64), nullable=True)

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


# The TeePublic store-health models (WallPath, StoreScanRun, StoreDesign,
# StoreListing, StoreCountCheck) were REMOVED 2026-09-06 — the owner moved
# that whole mechanism to a tool on his laptop. The database TABLES were
# deliberately left in place: they hold the only record of which designs
# were switched off, and the laptop tool will import it. Notes and the full
# code live in ../teepublic_tool/ beside this repo.


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
