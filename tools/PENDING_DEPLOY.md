# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v134 — the dashboard grouping index** · targets
178.105.232.196 (test box)

## Two things before you deploy

**BACK UP `poster.db` FIRST.** This adds an index, which is a schema change.
It is `CREATE INDEX IF NOT EXISTS`, so safe and repeatable, and SQLite
builds it over 201k rows in a couple of seconds — but the rule is the rule.

**The node does NOT need copying.** Nothing under `worker_service/` changed
and `AGENT_VERSION` stays at 1.27.0.

## What it does

The master dashboard groups every title by `(project_id, status)` on each
load. There was an index on `project_id` alone, so SQLite scanned that and
then built a scratch sort structure — a TEMP B-TREE — to do the grouping,
and threw it away every time.

`MEASURED 2026-08-27` on a table of the same shape and size:

    BEFORE   SCAN master_titles USING INDEX ix_master_titles_project_id
             USE TEMP B-TREE FOR GROUP BY
             216.7 ms

    AFTER    SCAN master_titles USING COVERING INDEX ix_master_project_status
             23.4 ms

9x faster, and it became a COVERING index — SQLite answers the whole query
from the index without reading the table at all.

## What it is NOT

**This is not why the site felt slow.** That was the link out to Kenya,
measured the same day at 12.6 KB/s to the laptop while the server itself
pulled from Hetzner's mirror at 130 MB/s. A sixth of a second cannot be
felt.

`CLAUDE.md` planned item 9 used to blame the 201,133 titles, in the same
confident voice as the measured facts around it, and that claim had already
been repeated to the owner as fact. It is corrected there now. This index is
the small real thing that was hiding behind the wrong diagnosis, and it
matters more as the table grows — celebrity portraits are coming.

### Files

`app/schema_migrations.py`, `app/config.py` (133 → 134).

**Verified:** preflight green, and the index measured against a real 201,133
-row table before and after. **Not verified:** it has not run against the
actual database — the migration runs on startup, so the first proof is the
dashboard loading after deploy.
