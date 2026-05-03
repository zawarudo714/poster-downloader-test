# Poster Downloader (web)

A FastAPI + SQLite + vanilla-JS web app for a small team to download poster
images from TMDB into a tidy, audit-trailable folder structure.

## Quick start (Windows)

```cmd
cd path\to\poster_downloader_web
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. First-run admin credentials are printed to the
console (also stored in `.first_admin.txt`).

> **Schema note** — pure additive; no DB migration needed for this round.
>
> **NOTE for round 7 (cache-busting + timezone fix)**: this round adds
> `APP_VERSION` to `app/config.py` (used as `?v=N` on every static asset
> URL — bump it on every deploy to force browsers to refetch JS/CSS), plus
> a new `app/timeutil.py` that converts stored UTC timestamps to APP_TZ
> for display. To activate timezones on the server, set `APP_TZ` and `TZ`
> env vars in `docker-compose.yml`:
> ```yaml
>     environment:
>       - TZ=Africa/Nairobi
>       - APP_TZ=Africa/Nairobi
> ```
> The `tzdata` Python package is now in requirements.txt so the slim
> Docker image has the timezone database available.
>
> **NOTE for round 9 (bug fixes + transparency + admin UX)**: this round
> adds two new columns to `payment_runs`: `by_day_json` and
> `back_pay_dates_json`. `Base.metadata.create_all()` does NOT add columns
> to existing tables, so on existing installs you need to either delete
> `data/poster.db` (loses data) or run a one-time ALTER TABLE inside the
> container *before* restarting the new image:
> ```bash
> docker exec poster-downloader-web-1 python -c "
> import sqlite3
> c = sqlite3.connect('/app/poster.db')
> c.execute('ALTER TABLE payment_runs ADD COLUMN by_day_json TEXT')
> c.execute('ALTER TABLE payment_runs ADD COLUMN back_pay_dates_json TEXT')
> c.commit()
> print('OK')
> "
> ```
> Other additions: nothing else needs migration — `is_deleted`/`deleted_at`
> on User, `last_seen_at` on User, and email config keys in `app_settings`
> were all added in previous rounds.

## What this version adds (latest round)

- **Closure bug on DONE button** is fixed. Previously, saving posters then
  clicking DONE without refreshing showed the wrong count (the click handler
  closed over a stale `t` from the initial render). The handler now reads
  `state.locked` at click time, so the count always reflects what's actually
  saved.
- **Replace toast** is now a soft hint ("if the image doesn't update, try
  refreshing") rather than a mandatory instruction. Cache-busting via
  `?v=size` usually shows the new image immediately.
- **Similar-pair revision UI**: the redundant top section (broken thumb +
  generic Replace/Delete) is gone. Only the per-poster cards remain — each
  with its own URL field and REPLACE THIS / DELETE THIS buttons.
- **Worker deletions are now visible to admin.** A new "RECENT DELETIONS —
  REVIEW" section on *Changes Requested* lists every deletion of a flagged
  poster. Each entry shows the worker's deletion reason. Two actions:
    - **ACKNOWLEDGE** — fine with it; dismiss from the panel.
    - **SEND BACK** — pushes the title back to the worker (reverts to
      in-progress if it was complete) with your note as a pinned admin-note
      banner; worker fixes by adding a replacement poster.
- **Daily auto-backup of `poster.db`** at 00:00:05 server time. Uses SQLite's
  online backup API (no service interruption). Auto-backups go to
  `backups/auto-YYYY-MM-DD.db` and are pruned after 14 days. If the server
  was offline at midnight, a catch-up backup runs at startup.
- **Manual named snapshots + restore** under the new *Backups* admin page:
    - "SAVE SNAPSHOT" — copy current DB with an optional friendly name
      (e.g. "before-csv-reimport"). Manual snapshots are never auto-pruned.
    - Per-row "RESTORE" — replaces the live `poster.db` with the chosen
      backup. A pre-restore safety snapshot is auto-created so you can
      undo the restore by restoring *that*. The SQLAlchemy connection pool
      is recycled automatically; users may need to refresh once.
    - Per-row "DELETE" — for cleanup.
- **Light mode toggle** in the top-right of the header (☾/☀ button).
  Persists in `localStorage`. Default is dark; the saved choice is applied
  synchronously in `<head>` to avoid a flash-of-wrong-theme on page load.

### Test environments

Click **Test Envs** in the header → create a sandbox with any name
(`alphanumeric / _ / -`, max 32 chars). Each test env has its own database
and workspace folder.

**Workers can be pinned to a test env at creation time.** When you
create a user via *Users → CREATE USER*, the env dropdown lets you pick:

- **`live`** (default) — production worker, sees real data.
- **`<test-env-name>`** — sandbox worker, logs in *straight into* that env
  and never sees live data. Cannot escape. If their pinned env is later
  deleted, they're auto-bumped to live on the next request.

**Usernames are unique across all envs** — you cannot create a "worker1"
in live and another "worker1" in a test env. The single live `users` table
is the global auth source; the `env` column on each user record decides
where they operate.

**Admins always start in live** but can switch into any test env via
*Test Envs → ENTER*. The header shows a loud orange banner so you can't
forget you're in a sandbox.

**At midnight**, every test env auto-resets (its DB and workspace are
wiped; the env itself stays). The same scheduler also creates the daily
auto-backup of live's `poster.db`. Test envs are intentionally NOT backed
up — they're meant to be ephemeral.

You can also RESET NOW (wipe data, keep env) or DELETE (remove the env
entirely) any time. Deleting an env displaces any workers pinned to it.

Implementation notes for the curious:
- Per-env SQLite DB + workspace folder under `data/test_envs/<name>/`.
- A `pd_env` HTTP-only cookie carries the admin's active env (workers
  ignore it — their env is forced from `User.env`).
- The DB session factory + workspace-path helpers read the active env from
  a `contextvars.ContextVar` set by middleware, so routes don't need to
  know which env they're in.
- The `users` table is always read from live (regardless of active env);
  per-env state (`locked_master_id`, `last_pull_size`) lives in each env's
  users table, seeded from the live row on first touch.
- The same nightly thread that creates DB backups also calls
  `reset_all_test_envs()` at midnight + 5 seconds.

## What this version adds

### Worker side

- **Friendlier labels.** "Pull" → **GET**, "Browse Master" → **BROWSE ALL TITLES**,
  "Release Unstarted" → **RETURN UNWORKED**, "Mark Complete" → **DONE**, etc.
- **Focus-stable polling.** The 8-second background refresh no longer
  re-renders the active panel from scratch. It only updates dynamic bits
  (counts, poster grid, status pills), so typing in the skip box or scrolling
  the page is no longer interrupted.
- **Low-quality URL warning.** If you paste a TMDB URL that looks like a
  preview (e.g. `media.themoviedb.org/.../w440_and_h660_face/...`), you'll
  get a confirm dialog reminding you to "Copy link address" (not "Copy image
  address") on the *full-size* poster page. You can still bypass for the rare
  case where only a sub-HD poster exists. The same gate now also fires on
  REPLACE.
- **Comment box on DONE**, mirroring the SKIP comment box. If you click
  DONE with fewer than 3 posters and no comment, you'll be prompted for an
  optional reason ("only 2 HD posters available", etc.). You can still leave
  it blank.
- **DELETE FILE on a revision now asks why.** Whatever you type goes back to
  the admin so they know what happened (they see it in the resolved-revisions
  table).
- **Title click no longer auto-opens TMDB.** Use the **↗ Open TMDB** button
  in the active panel — and now also on every revision card in the
  "Changes Requested" banner.
- **Replacements show a clear "refresh to see new image" toast.** Browser
  caching makes the in-place image swap unreliable; rather than fighting it,
  you now get a one-time toast telling you to refresh.
- **Loud rejection indicator.** When admin rejects your fix and sends it
  back, the revision shows a red **"⚠ ADMIN REJECTED YOUR FIX — please look
  again"** banner with the admin's verdict, plus a red "rejected — redo"
  status pill so you can't miss it.
- **Similar-pair revisions.** When admin marks two of your posters as too
  similar, you see them side-by-side in the banner with REPLACE / DELETE
  buttons on each — pick whichever one you want to redo.
- **Admin notes inline.** When an admin sends a skipped title back with a
  note, you'll see it in a blue banner at the top of the active title — along
  with your original skip reason for context.

### Admin side

- **Gallery review (replaces the one-poster-at-a-time browser).**
  *Review Posters* now shows every poster of a title side-by-side. Click any
  poster to open a lightbox where you can flag/unflag with a comment. ← / →
  arrow keys step through titles.
- **Quality highlighting in the gallery:**
    - **Red border + "NNN px wide" pill** for any poster under 800 px wide
      (the actual image dimensions are read from the file header on save —
      no extra Python deps).
    - **Orange dashed outline + "LQ URL bypassed" pill** for any poster the
      worker downloaded after dismissing the low-quality warning.
- **Mark-similar.** Toggle SELECT MODE in the gallery, click 2+ posters of
  the same title, then "MARK SELECTED AS SIMILAR" — sends a single
  similar-pair revision to the worker (you can add a note like "alternate
  covers — pick one").
- **Approval flow for revisions.** When a worker REPLACEs a flagged poster
  (or clicks SEND FOR APPROVAL), the revision goes to **awaiting_approval**
  instead of disappearing. You'll see it on a new section at the top of
  *Changes Requested* with a thumbnail of the new file:
    - **APPROVE** — clears the flag from the worker side.
    - **REJECT & SEND BACK** — sends it back to "open" with your verdict;
      the worker sees the loud rejection banner described above.
  Similar-pair revisions show *both* thumbnails and follow the same
  approve/reject flow.
- **Skipped Titles page** (new nav entry). Lists every skipped title with
  the worker's reason. Type a note and SEND BACK; the title flips back to
  in-progress and the worker sees both your note and their original skip
  reason.
- **Cache-busted thumbnails everywhere** (gallery, lightbox, revision cards),
  plus the worker-side toast described above, so a worker's REPLACE shows up
  immediately on your end (and they see the toast hint on theirs).
- **Deletion reasons surfaced.** When a worker deletes a flagged poster
  with a reason, you see "auto-resolved: file deleted: <reason>" in the
  resolved-revisions table.

## Permanent design points (carried over)

- Claim-based queue: a title is exclusively reserved to one user once they
  pull it. They can RETURN UNWORKED titles back to the pool; in-progress
  ones stay theirs.
- Immutable folder paths: a poster's `{user}/{date}/{title-slug}/` folder
  is set at first save and never changes — even if the title is renamed,
  re-flagged, replaced, or the worker's date rolls over. This keeps file
  paths stable for downstream consumers.
- Soft-delete: deleted posters keep their DB row (with `deleted_at` set),
  so the audit trail is complete; the file on disk is removed.
- Activity log captures every state change with actor, target, timestamp,
  and JSON details.

## Routes (high-level)

| URL                             | Who    | What                                  |
| ------------------------------- | ------ | ------------------------------------- |
| `/login`, `/logout`             | All    | session auth                          |
| `/`, `/api/state`               | Worker | dashboard JSON                        |
| `/pull_next`, `/release`        | Worker | take next N / return unworked         |
| `/lock/{id}`, `/unlock`         | Worker | open a claimed title                  |
| `/save_image`                   | Worker | download a URL into the title folder  |
| `/poster/{id}/replace`          | Worker | replace bytes; sends rev for approval |
| `/poster/{id}/delete`           | Worker | soft-delete + auto-resolve flags      |
| `/title/{id}/{complete,skip,reopen}` | Worker | finish, skip, or undo finish     |
| `/revisions/{id}/resolve`       | Worker | "I think this is fine" → for approval |
| `/master_browse`                | Worker | claim titles from master list         |
| `/admin`                        | Admin  | dashboard + workspace tree            |
| `/admin/browse`                 | Admin  | gallery review                        |
| `/admin/master`                 | Admin  | full title list + import/export       |
| `/admin/revisions`              | Admin  | open / awaiting / resolved sections   |
| `/admin/skipped`                | Admin  | skipped titles + send-back notes      |
| `/admin/users`                  | Admin  | user management                       |
| `/admin/audit`                  | Admin  | full activity log                     |
| `/admin/poster/{id}/{flag,unflag}` | Admin | flag / clear flag                  |
| `/admin/revisions/{id}/{approve,reject}` | Admin | approval verdicts            |
| `/admin/title/{id}/skip_revise` | Admin  | send a skipped title back with a note |
| `/admin/zip/start,status,download` | Admin | per-day zip builder                |

## Folder layout on disk

```
workspace/
└── {username}/
    └── {YYYY-MM-DD}/                    ← original_save_date, immutable
        └── {title-slug-and-year}/       ← per-title folder, immutable
            ├── {Title} 1.jpg
            ├── {Title} 2.webp
            └── {Title} 3.png
```

## Configuration

Settings live in `app/config.py`. Key knobs:

- `RESTRICT_HOSTS` and `ALLOWED_DOWNLOAD_HOSTS` — cap which hosts the
  downloader will fetch from.
- `DEFAULT_PULL_SIZE` — default N for the GET button.
- `SOFT_POSTER_LIMIT_PER_TITLE` — soft cap; worker gets a confirm dialog
  past this number.
- `MAX_DOWNLOAD_BYTES` — hard cap per file.
