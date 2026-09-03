# Not yet deployed

**The strip-down to a single Travel Locations project.** Written and green,
but intended for a FRESH EMPTY DATABASE on main, not as an upgrade over the
existing one. Do not deploy this on top of the old data.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## What is waiting

* **One project.** `DEFAULT_PROJECT_SLUG` is `travel`; `PROJECT_DEFS` holds
  only Travel Locations — Brave in-page -> GPT Image 2 -> FineArtAmerica,
  review gate on, no year, no content type.
* **Global defaults made niche-neutral**, because a global default is what
  the next project inherits before anyone configures it. `title_template`
  lost `{year}`, `keywords_static` is blank, `source_search_url` is blank,
  the Brave queries are generic, and the GPT prompt no longer says "zoom in
  to the upper body areas".
* **`build_queries()` accepts `{title}` as well as `{artist}`.** The old
  placeholder was hardcoded and is a music word.
* **Deleted, all dead with the movie catalogue:** `archive_index` and
  everything behind it (two admin endpoints, two node endpoints, the READ THE
  STORAGE BOX panel, its setting, its tests) · `scripts/migrate_pipeline.py`
  (920 lines; startup already does create_all -> migrate_schema ->
  sync_projects) · `backfill_upload_status.py` ·
  `fix_crossproject_processing.py` · `tools/migrate_gui.py` ·
  the rehearsal db and note · `fineartamerica-bulk-delete.md`.
* **Photoshop is DORMANT, not deleted.** Hidden by `processor`. The test
  panels no longer fall back to assuming Photoshop when there is no active
  project — no project now means no tests.
* **New preflight check:** a reference in CLAUDE.md to a document that does
  not exist now fails. Sabotage-tested — it goes red.
* Dev seed data is travel locations, keeping the awkward-character coverage.

## Found by the owner running it — 2026-09-01

Two real defects the static checks could not see. Both fixed here.

* **`sync_projects()` never removed a project deleted from the registry.**
  It only created and updated, so stripping `PROJECT_DEFS` did nothing to a
  database that already had the old rows — the movie project kept its card,
  its place in the switcher, and workers could still stand in it. It is now
  switched OFF (never deleted; titles, posters and payment history point at
  that row). The asymmetry is deliberate: sync only ever turns a project
  off, never on, so the standing rule that a deploy must not re-enable
  something you switched off by hand still holds.
* **The worker's empty-state hint said "click the Open TMDB link to find a
  poster"** — hardcoded, shown in every project. It now reads from
  `PD.searchMode`, `PD.sourceLabel` and `PD.noun`, so an in-page project is
  told to search and tap.

**New Diagnostics invariant: `check_projects_match_registry`.** An active
project the code no longer declares is now reported against the live
database. Nothing mechanical could have caught the first defect — preflight
proves nothing is DISCONNECTED, and registry-versus-database is a fact about
data.

## Verified

**By running:** preflight green on all 20 checks · 42 behaviour tests pass ·
all 10 sabotages still caught · the new document check goes red when broken ·
everything compiles.

**NOT verified: the site has never been run against a database.** No FastAPI
in the sandbox to boot it. `DEV_SETUP.bat` is the first real proof and only
the owner can run it.

## Owner still to specify

`images_per_title` (2 is a placeholder), the Brave query, the FAA keywords,
the GPT prompt, the listing title scheme, and the master sheet itself.

## Node — THE FOLDER MUST BE COPIED

`worker_service/` HAS CHANGED. `archive_index.py` is deleted there and
`agent.py` no longer imports, builds or dispatches that stage.

`AGENT_VERSION` bumped **1.28.0 -> 1.29.0**. That number on the Nodes tab is
the only thing that can tell the owner from the website whether the copy
actually happened.
