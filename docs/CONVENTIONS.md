<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## Conventions that already exist — follow them

- **No build step.** Vanilla JS, one file per page, `?v={{ app_version }}` for
  cache busting. Bump `APP_VERSION` in `config.py` on every deploy.
- **Immutable folder paths.** A poster's `{user}/{date}/{title-slug}/` is set
  at first save and never recomputed, even across renames, replacements and
  date rollovers. Downstream consumers depend on this.
- **Soft deletes.** `deleted_at` is set, the row stays, the file is unlinked.
  The audit trail must survive.
- **`ActivityLog` for every state change** — actor, target, timestamp, JSON
  detail.
- **`external_id` is the universal key — INSIDE ONE PROJECT.** The `0` column
  from that project's own sheet. It is the folder prefix (`50. Pulp Fiction
  (1994)`), `MasterTitle.external_id`, and the key in both legacy JSON files.

  **It is NOT unique across projects and never was.** Every sheet starts
  again at 1, so external_id 2 is `The Dark Knight` in the movie project and
  `Radiohead` in MUSIK. On 25 Aug the legacy upload import built one
  `{external_id: title}` dictionary over the whole table; the later rows won,
  every movie title silently resolved to a music artist, and it matched 0 of
  4,865 images — while printing a confident page of "renumbered" findings
  about the wrong project's data. Nothing looked broken.

  Any lookup BY external_id must be scoped, through `pipeline.project_scope`
  or one of the `scope_titles` helpers. Now enforced by the GUARDED table in
  `preflight.py`, so a new one fails before deploy. Sorting by it is fine —
  it is equality lookups that pick a row. Until v287 the row knew only
  `.external_id ==`, which matched no code at all, so `filter_by(
  external_id=…)` and `.in_()` would have passed unseen; the row now lists
  all three (`EXTERNAL_ID_LOOKUP`), and a GUARDED row whose risky line
  matches nothing FAILS — a rename used to empty a row silently — unless it
  is in `MAY_MATCH_NOTHING`, a row that forbids code nobody has written.

  **AND IT IS AN IDENTITY, NOT A WORK ORDER — `queue_priority` is the work
  order.** Added 2026-09-17, when 146 famous landmarks (Eiffel Tower,
  Colosseum…) had to join a catalogue whose worker was already 250 rows in.
  They belong at the FRONT of the queue by fame and at the BACK of the sheet
  by number, and the answer is never to renumber or to reuse a freed number:
  new rows take fresh numbers on the end and a `queue_priority` above 0, and
  `pull_next()` orders by priority first, then external_id. Watched by
  `check_claim_queue_orders_by_priority` in `preflight.py`, because dropping
  that one clause has no other symptom — the priority rows just quietly go
  to the back of an 83,000-row queue.
- **`Base.metadata.create_all()` does not ALTER existing tables, and a wipe
  that deletes ROWS keeps the old columns.** New columns need an explicit
  migration in `schema_migrations.py` (`NEW_COLUMNS`). **So does a change to
  an existing column** — and that one is easy to forget, because editing
  `models.py` makes the change look done. It only takes effect on a table
  built fresh AFTER the edit. On 2026-09-09 v174 made `year` nullable in the
  model; every box built earlier kept `year VARCHAR(16) NOT NULL`, and
  `reset_workflow.py` deletes rows rather than dropping the table, so the
  planned reset-and-reimport would have died on its first batch with "NOT
  NULL constraint failed" — the model said optional, the live column said
  required. Relaxing a column lives in `RELAX_NOT_NULL`, which REBUILDS the
  table (SQLite has no ALTER COLUMN); its rebuild matches the column's own
  type token, so it relaxes a VARCHAR as readily as an INTEGER, and it raises
  loudly rather than rebuild an identical table if it ever cannot. **The
  general shape: a schema change is only real once a migration carries it to
  tables that already exist. Editing the model is necessary and never
  sufficient.** The detector is not preflight — "the live column is NOT NULL"
  is a runtime fact this environment cannot see — it is the stage-6 reset
  rehearsal on the test box, which must be run before production.
- **Storage layout is `{site}/{project}/processed/{date}/{title_folder}/{filename}`**
  relative to `storage_root` (now `S:` rather than `S:/processed`). Project and
  site names are slugified through `pipeline._path_token()` before they touch a
  Windows path. **On an existing install `storage_root` is already stored in
  `app_settings` and will NOT pick up the new default — change it on the
  Pipeline page to `S:` or the layout ends up doubled.**
- **Anything that can differ between niches resolves through
  `pipeline.get_setting(db, key, project=...)`.** Pay rate, item noun, image
  cap, source search URL, allowed hosts, API keys — all of it. A literal in a
  template or a constant in `config.py` for such a value is a defect, because
  it means project number three needs a rewrite instead of a registry entry.
- **The workspace is `{project}/{worker}/{date}/{title folder}/{file}`.**
  `SavedPoster.project_folder` is denormalised so `saved_poster_path()` never
  joins through `master_titles` — it is called on every gallery thumbnail.
  `saved_poster_folder()` still accepts the OLD `{worker}/{date}/...` layout
  as a fallback, which is what makes the startup move in
  `app/workspace_migration.py` safe to automate. Delete the fallback once
  production scans clean in Diagnostics.
- **An address a person typed is downloaded through `imagefetch.get_public`,
  never a bare `requests.get`.** `_validate_image_url` refuses addresses on
  this server's own network, but `requests` follows redirects by itself, so
  until v271 a public page that redirected to `127.0.0.1` or
  `169.254.169.254` got fetched anyway — the check guarded the first hop
  only (site audit, 2026-10-29). `get_public` follows redirects itself and
  runs `_host_check` on each. A check on an input is only as good as the
  last place the input can change before use. Preflight's "typed addresses
  check every redirect" fails a deploy on a `requests` call without
  `allow_redirects=False` in `imagefetch.py` or `routes/worker.py`.
- **Comments explain *why*, not *what*.** The existing code does this well —
  match it. Several modules carry a design-contract header; keep those current.

---

