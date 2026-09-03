"""
Additive schema migrations, applied automatically at startup.

════════════════════════════════════════════════════════════════════════════
WHY THIS RUNS ITSELF
════════════════════════════════════════════════════════════════════════════
`Base.metadata.create_all()` creates new TABLES but never ALTERs existing
ones, so a release that adds a column needs an explicit migration. In a Docker
deployment that creates an ordering trap with no good answer:

  * Migrate BEFORE rebuilding and you run the OLD script — the whole repo is
    baked into the image at build time, so `docker compose exec` executes
    whatever the running container was built with. New columns are silently
    skipped and it cheerfully reports "nothing to add".
  * Migrate AFTER rebuilding and the app serves 500s in the window between the
    two commands, because the new code queries columns that don't exist yet.

Either way it depends on remembering the right order, and getting it wrong
produces an Internal Server Error with no obvious cause. So the app applies its
own additive schema changes on startup, before it accepts a request. Deploying
becomes `git pull && docker compose up -d --build` with nothing to remember.

════════════════════════════════════════════════════════════════════════════
WHAT BELONGS HERE — AND WHAT DOESN'T
════════════════════════════════════════════════════════════════════════════
ONLY changes that are safe to apply unattended, to any database, in any order:

    ADD COLUMN (nullable, or with a default)
    CREATE INDEX IF NOT EXISTS

Every entry must be idempotent and fast. SQLite's ADD COLUMN doesn't rewrite
the table, so this stays quick even on the 100k-row master list.

DO NOT put data migrations here — backfills, imports, anything that rewrites
rows or could take minutes. Those belong in a script under scripts/ that a
human runs deliberately, can dry-run first, and can take a backup before.
A destructive step that runs itself on every boot is a very bad day.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from .db import engine


# (table, column, DDL). Append new entries; never edit or reorder existing ones.
NEW_COLUMNS: list[tuple[str, str, str]] = [
    # ── Post-production pipeline ────────────────────────────────────────
    ("master_titles", "project_id",       "INTEGER"),
    ("master_titles", "greenlit_at",      "DATETIME"),
    ("master_titles", "greenlit_by",      "VARCHAR(64)"),
    ("master_titles", "pipeline_status",  "VARCHAR(24)"),
    ("saved_posters", "pipeline_status",  "VARCHAR(24)"),
    ("saved_posters", "process_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("saved_posters", "process_error",    "TEXT"),
    ("saved_posters", "claimed_at",       "DATETIME"),
    ("saved_posters", "claimed_by",       "VARCHAR(64)"),
    # ── The two names a sheet can supply that no rule can rebuild ───────
    # See MasterTitle.search_query. Nullable on purpose: a sheet that does
    # not carry them leaves every caller on its existing path.
    ("master_titles", "search_query",      "TEXT"),
    ("master_titles", "marketplace_title", "VARCHAR(512)"),
    # ── Fair sharing between projects / rotation between accounts ───────
    ("projects",        "process_weight", "INTEGER NOT NULL DEFAULT 1"),
    ("upload_accounts", "rotation_order", "INTEGER NOT NULL DEFAULT 100"),
    ("upload_accounts", "rotation_size",  "INTEGER"),
    # ── Master/project split, worker scoping, provenance ────────────────
    ("projects",      "target_site",     "VARCHAR(64) NOT NULL DEFAULT 'fineartamerica'"),
    ("users",         "last_project_id", "INTEGER"),
    ("master_titles", "greenlit_source", "VARCHAR(32)"),
    ("payment_runs",  "by_project_json", "TEXT"),
    # ── Per-project vocabulary, upload rotation, workspace split ────────
    ("projects",      "upload_turn_size",  "INTEGER"),
    ("projects",      "item_noun",         "VARCHAR(32) NOT NULL DEFAULT 'poster'"),
    ("projects",      "item_noun_plural",  "VARCHAR(32) NOT NULL DEFAULT 'posters'"),
    ("saved_posters", "project_folder",    "VARCHAR(64)"),
    # ── What a project HAS, so the UI can render per project ────────────
    ("projects",      "processor",         "VARCHAR(24) NOT NULL DEFAULT 'photoshop'"),
    ("projects",      "has_year",          "INTEGER NOT NULL DEFAULT 1"),
    ("projects",      "has_content_type",  "INTEGER NOT NULL DEFAULT 1"),
    ("projects",      "has_review_gate",   "INTEGER NOT NULL DEFAULT 0"),
    ("projects",      "search_mode",       "VARCHAR(16) NOT NULL DEFAULT 'external'"),
    # ── AI review gate + spend metering ─────────────────────────────────
    ("ledger_entries",   "raw_type",     "VARCHAR(64)"),
    ("processed_images", "review_status", "VARCHAR(16)"),
    ("processed_images", "reviewed_at",   "DATETIME"),
    ("processed_images", "reviewed_by",   "VARCHAR(64)"),
    ("processed_images", "preview_path",  "VARCHAR(768)"),
    ("processed_images", "attempt",       "INTEGER NOT NULL DEFAULT 1"),
    ("saved_posters",    "unusable_reason", "TEXT"),
    ("saved_posters",    "unusable_at",     "DATETIME"),
    ("saved_posters",    "unusable_by",     "VARCHAR(64)"),
    # ── Ban recovery ────────────────────────────────────────────────────
    # A banned account is not a disabled one: its listings are gone from the
    # marketplace and have to be rebuilt elsewhere. `replaced_by_id` records
    # where they went.
    ("upload_accounts",  "banned_at",       "DATETIME"),
    ("upload_accounts",  "banned_reason",   "TEXT"),
    ("upload_accounts",  "replaced_by_id",  "INTEGER"),
    # ── Earnings ────────────────────────────────────────────────────────
    ("upload_accounts",  "last_earnings_read_at", "DATETIME"),
    ("upload_accounts",  "consecutive_failures",  "INTEGER NOT NULL DEFAULT 0"),
    ("upload_accounts",  "marketplace_balance",   "VARCHAR(24)"),
    # Reading money and uploading are two capabilities of one account, so they
    # get one pause each. Sharing `paused_until` meant a bot wall during a
    # read stopped uploading too — and vice versa.
    ("upload_accounts",  "earnings_paused_until", "DATETIME"),
    ("upload_accounts",  "earnings_pause_reason", "TEXT"),
    # Listing-health runs: automatic mode, pause, and the scan mode. Added
    # after the first working sweeps, so the table already exists.
    ("store_scan_runs",  "auto",       "INTEGER NOT NULL DEFAULT 0"),
    ("store_scan_runs",  "paused_at",  "DATETIME"),
    ("store_scan_runs",  "paused_by",  "VARCHAR(64)"),
    ("store_scan_runs",  "scan_mode",  "VARCHAR(16) NOT NULL DEFAULT 'full'"),
    ("store_scan_runs",  "retry_at",    "DATETIME"),
    ("store_scan_runs",  "retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("store_scan_runs",  "retry_note",  "TEXT"),
    ("store_scan_runs",  "stage_jobs_total", "INTEGER NOT NULL DEFAULT 0"),
    ("store_scan_runs",  "stage_jobs_done",  "INTEGER NOT NULL DEFAULT 0"),
    # One account at a time through the action stages, so there is only ever
    # one job to cancel when the run is stopped.
    ("store_scan_runs",  "stage_account_id", "INTEGER"),
    ("store_scan_runs",  "stage_attempts",   "INTEGER NOT NULL DEFAULT 0"),
    # Stops a design that cannot be switched off from being handed out
    # forever now that the stage's end is derived from the catalogue.
    ("store_listings",   "action_error_at",  "DATETIME"),
    # ── Listing reconciliation ──────────────────────────────────────────
    # The name the marketplace prints on a listing. Not the account name and
    # not derivable from the profile URL, but the public address is built
    # from it, so it has to be typed in once per account.
    ("upload_accounts",  "artist_name",      "VARCHAR(128)"),
    # What the marketplace itself said, kept APART from `status` — which is
    # what we believe. The disagreement between the two is the finding.
    ("upload_tracking",  "listing_status",     "VARCHAR(16)"),
    ("upload_tracking",  "listing_http",       "INTEGER"),
    ("upload_tracking",  "listing_checked_at", "DATETIME"),
    # Liveness for long jobs. `started_at` cannot serve: a switching stage
    # legitimately runs for an hour and was being declared dead at 45
    # minutes while it was reporting every sixteen seconds.
    ("pipeline_jobs",    "last_report_at",   "DATETIME"),
    # WHICH action failed. One error field served both directions, so a
    # design that could not be switched OFF was then skipped when the run
    # tried to switch it back ON — and stayed hidden.
    ("store_listings",   "action_error_kind", "VARCHAR(16)"),
    # How many runs in a row have failed to switch this one design on a page
    # that was plainly ours. Retrying a genuine failure for ever is how a
    # broken design quietly costs an hour of every sweep.
    ("store_listings",   "action_fail_count", "INTEGER NOT NULL DEFAULT 0"),
    # The marketplace's OWN count of switched-off designs, and when it was
    # read. The only figure in the system that does not come from our own
    # records, which is exactly why it can catch us being wrong.
    ("upload_accounts",  "inactive_count",      "INTEGER"),
    ("upload_accounts",  "inactive_checked_at", "DATETIME"),
]

# ════════════════════════════════════════════════════════════════════════════
#  COLUMNS THAT MUST BECOME NULLABLE
# ════════════════════════════════════════════════════════════════════════════
# SQLite cannot ALTER a column's nullability. The table has to be rebuilt,
# which is a real migration rather than an additive one — so it is listed
# separately and run only when the current schema actually says NOT NULL.
#
# upload_accounts.project_id: an account may now belong to no project. The
# TeePublic accounts earn passively with nothing uploaded to them, and an
# account added from the Earnings tab has no project until you attach one.
RELAX_NOT_NULL: list[tuple[str, str]] = [
    ("upload_accounts", "project_id"),
]

NEW_INDEXES: list[tuple[str, str, str]] = [
    ("ix_master_titles_project_id",      "master_titles", "project_id"),
    ("ix_master_titles_greenlit_at",     "master_titles", "greenlit_at"),
    ("ix_master_titles_pipeline_status", "master_titles", "pipeline_status"),
    ("ix_saved_posters_pipeline_status", "saved_posters", "pipeline_status"),
    ("ix_poster_pipeline",               "saved_posters", "pipeline_status, deleted_at"),
    ("ix_upload_rotation",               "upload_accounts", "last_run_at, rotation_order"),
    ("ix_master_greenlit_source",        "master_titles", "greenlit_source"),
    ("ix_poster_project_folder",         "saved_posters", "project_folder"),
    ("ix_processed_review",              "processed_images", "review_status"),
    # The master dashboard groups every title by (project_id, status) on
    # each load. `ix_master_titles_project_id` covers only the first column,
    # so SQLite scanned that index and then built a TEMP B-TREE to do the
    # grouping — a scratch sort structure thrown away every time.
    #
    # MEASURED 2026-08-27 on 201,133 rows: 0.16s before, and the plan says
    # "SCAN master_titles USING INDEX ix_master_titles_project_id" plus
    # "USE TEMP B-TREE FOR GROUP BY". With both columns covered the grouping
    # is read straight off the index.
    #
    # Worth stating plainly: this is NOT why the site felt slow. That was
    # the network link, measured the same day at 12 KB/s to the owner while
    # the server itself pulled at 130 MB/s. A sixth of a second cannot be
    # felt. This is a tidy-up that was hiding behind a wrong diagnosis, and
    # it matters more as the table grows — celebrity portraits are coming.
    ("ix_master_project_status",         "master_titles", "project_id, status"),
]


def _create_table_sql(conn, table: str) -> str:
    """The CREATE TABLE statement SQLite is currently using for this table."""
    row = conn.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:t"
    ), {"t": table}).first()
    if not row or not row[0]:
        raise RuntimeError(f"Could not read the schema for {table}")
    return row[0]


def migrate_schema(*, dry_run: bool = False) -> dict:
    """
    Add any missing columns and indexes. Safe to call repeatedly.

    Existing columns are detected and skipped, so this is a no-op on an
    up-to-date database — which is the normal case on every restart.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    added: list[str] = []
    skipped: list[str] = []

    with engine.begin() as conn:
        for table, column, ddl in NEW_COLUMNS:
            if table not in existing_tables:
                # Table doesn't exist yet — create_all() will build it with
                # this column already present, so there's nothing to add.
                skipped.append(f"{table}.{column} (table not created yet)")
                continue

            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                skipped.append(f"{table}.{column}")
                continue

            if dry_run:
                added.append(f"{table}.{column} (would add)")
                continue

            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            added.append(f"{table}.{column}")

        # ── Relax NOT NULL where the model now allows NULL ──────────────
        # SQLite has no ALTER COLUMN, so this rebuilds the table: create a
        # copy with the corrected definition, move the rows, swap it in.
        # Guarded by an actual check of the current schema, so it runs once
        # and is a no-op every time afterwards.
        for table, column in RELAX_NOT_NULL:
            if table not in existing_tables:
                continue
            cols = {c["name"]: c for c in inspector.get_columns(table)}
            if column not in cols or cols[column]["nullable"]:
                skipped.append(f"{table}.{column} (already nullable)")
                continue
            if dry_run:
                added.append(f"{table}.{column} (would become nullable)")
                continue

            # PRAGMA writable_schema is the surgical option, but editing the
            # schema text by hand is exactly the kind of clever that breaks
            # quietly. Rebuilding is slower and obviously correct.
            names = ", ".join(f'"{c}"' for c in cols)
            ddl = _create_table_sql(conn, table).replace(
                f"{column} INTEGER NOT NULL", f"{column} INTEGER"
            ).replace(
                f'"{column}" INTEGER NOT NULL', f'"{column}" INTEGER'
            ).replace(f"{table}", f"{table}__new", 1)

            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text(ddl))
            conn.execute(text(
                f'INSERT INTO {table}__new ({names}) SELECT {names} FROM {table}'))
            conn.execute(text(f"DROP TABLE {table}"))
            conn.execute(text(f"ALTER TABLE {table}__new RENAME TO {table}"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            added.append(f"{table}.{column} (now nullable)")

        if not dry_run:
            for name, table, columns in NEW_INDEXES:
                if table not in existing_tables:
                    continue
                try:
                    conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})"
                    ))
                except Exception:
                    # An index is an optimisation, never a correctness
                    # requirement. Never fail a boot over one.
                    pass

    return {"added": added, "skipped": skipped}
