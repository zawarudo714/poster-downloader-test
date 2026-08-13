# Project brief for AI sessions

Read this first. Then read `PIPELINE.md` if the work touches post-production.

---

## What this is

A print-on-demand operation. Two halves:

1. **Poster sourcing** (built, in production) — a FastAPI + SQLite + vanilla-JS
   app where paid workers claim movie/TV titles from a 101,605-row master list,
   find HD posters on TMDB, and save them into an audit-trailed folder tree.
   The admin reviews quality, flags bad images, approves completions, and pays
   weekly.

2. **Post-production** (built, not yet deployed) — automates what the owner
   used to do by hand on his laptop: run each poster through a Photoshop
   painterly effect, archive it, and upload it to FineArtAmerica. See
   `PIPELINE.md`.

Live at `178.105.34.144` (Hetzner, Docker). The owner is a solo operator, not
a developer — he reads code but does not want to edit it to change behaviour.

---

## The one thing to understand

**This project will keep growing in scope. Do not make choices that are
painful to retrofit.**

Stated plans, in order:
1. A second niche — **celebrity portraits**, sourced from Pinterest rather than
   TMDB, a different master sheet schema, ~2 images per title, its own
   FineArtAmerica account.
2. **Merge both niches** under one master dashboard with cross-niche reporting.
3. **More marketplaces** — TeePublic named specifically — and more accounts.

Also planned, stated but not yet built:

4. **Marketplace reconciliation.** Scan a live FineArtAmerica account (most
   likely by URL structure), compare what is actually listed against what the
   database believes, and surface the disagreements for the admin to explain
   rather than guessing. The three cases worth catching:
     * processed + marked uploaded, but NOT on the site — taken down for
       copyright, or the upload silently failed and was recorded as success
     * marked uploaded but never processed — impossible state, means bad data
     * on the site but not in the database — uploaded outside the pipeline
   The admin annotates each with a reason and the database is corrected.
   `UploadTracking` already carries `remote_id`, `status='removed'` and
   `removed_reason` for exactly this, so the reconciler should write into
   those rather than inventing a parallel table. Note this is the same shape
   as `diagnostics.py` — findings + an explanation + a deliberate action — and
   should probably live beside it rather than in the pipeline module.

5. **Cross-marketplace earnings tab (master level).** NOT a pipeline. The
   owner has 9 TeePublic accounts earning passively with no uploading, and
   checks each one by hand by opening its Chrome profile and reading
   "This Month" / "Next Payment" off the My Account page. The plan is a
   read-only scraper on the node that visits each account once a day, stores
   an absolute snapshot, and a dashboard tab that shows deltas ("+$2 since
   yesterday"), filterable and totalled by site (all / TeePublic / FAA /
   Redbubble later), by account, or by any subset.

   Design points already established:
     * An ACCOUNT EXISTS ONCE. The Chrome profiles created for revenue
       reading are the same accounts a future TeePublic upload pipeline
       would log into — do not model "revenue account" and "upload account"
       separately or the owner ends up with two profiles per account.
       Uploading and revenue-reading are capabilities of one account.
     * `UploadAccount.project_id` is currently NOT NULL, which does not fit:
       these 9 accounts belong to no project and may never do. Resolve that
       before building — probably by making the column nullable rather than
       inventing a parallel table.
     * Store ABSOLUTE snapshots, never deltas. "This Month" resets to zero on
       the 1st, so a stored delta would show a large negative every month.
       Deltas are computed at read time, within a month boundary.
     * The figures are estimates that can revise DOWNWARD (refunds), and
       TeePublic states up to 48h lag after a sale. A drop is not a bug.
     * This is read-only and belongs with `diagnostics.py` / the marketplace
       reconciler, not the pipeline module.

6. **Ban recovery / account handover.** When a marketplace account is banned,
   mark it as such and use that marking to bring a replacement account online
   with as little manual work as possible — reassign everything that was on
   the dead account to the new credentials so uploading resumes on its own.
   `UploadTracking` is already keyed on (poster, account) and REQUEUE BACK
   CATALOGUE already re-queues processed images against a chosen account, so
   the pieces exist; what's missing is the "this account is dead, move its
   work to that one" action and a banned state on `UploadAccount`. Owner has
   not designed this yet — raise it when ban recovery next comes up.

7. **Reprocess the dot-truncated legacy titles.** The old JSX did
   `split('.')[0]` on the filename, so every poster in a title containing a
   dot overwrote the previous one. Measured on disk: **44 title folders**
   holding 1 file each where 2-3 were expected (`E.T.`, `Kill Bill Vol. 1`,
   `Mr. & Mrs. Smith`). The owner recalls 61-65; reconcile the figure before
   acting. These need re-processing through the pipeline once. Intended as a
   deliberate one-off trigger, not a permanent feature — build it as a
   throwaway admin action and delete it afterwards.

Concretely, this means:

- Never hardcode a niche, a source site, a marketplace, or a marketplace's DOM.
- Anything the owner might want to change goes in the **dashboard**, backed by
  `app_settings`. Not a constant, not a config file on a server, not a code
  edit. This is an explicit requirement he has repeated.
- New capability should be **new rows, not new columns**. Pipeline tables
  already carry `project_id` and `target_site` for exactly this reason.
- Prefer a generic mechanism with one caller today over a specific one you'd
  have to unpick later.

---

## Architecture

```
Linux VPS 178.105.34.144        Windows VPS (planned)         Hetzner Storage Box
─────────────────────────       ─────────────────────         ───────────────────
poster_downloader_web           worker_service/               Processed images,
= the brain                     = the muscle                  permanent archive
· worker + admin UI             · Photoshop (one image        · mounted as S:
· all state in poster.db          per invocation)             · makes account-ban
· all settings + scripts        · Selenium (sequential,         recovery free
· pipeline dispatch               one tab)
                                · stateless, disposable
```

The Windows node holds **no configuration** beyond a server URL and a token.
Script, selectors, timings, templates and credentials all arrive over the API
on every cycle. That's what lets the owner change behaviour from the browser
and rebuild the box from nothing.

---

## The master / project split (added 2026-08-04)

The admin UI now has two levels, and the nav **replaces itself** between them.

| | |
|---|---|
| **Master** | Dashboard (project cards), Payments, Chat, Projects, Users, Backups, Email, Activity Log, Stats, Diagnostics |
| **Project** | Review Posters, Title List, Changes Requested, Skipped, Pipeline, Stats, Peek |

The active project is session state — cookie `pd_project`, falling back to
`User.last_project_id`, falling back to the default project. It is **not** in
the URL: every existing admin route and every `fetch()` in the JS keeps
working and simply means "…within the project I'm in". See the module
docstring in `app/projects.py` for why that trade was made.

Rules that matter:

- A template declares its level with `{% set nav_scope = 'project' %}` at the
  top level, right after `{% extends %}`. Anything that doesn't say is master.
- Any query that can show or hand out a title goes through
  `projects.scope_titles()` (admin) or `scope_titles_multi()` (worker).
  Nothing filters on `project_id` by hand.
- **`project_id IS NULL` means the DEFAULT project, not "any project".** The
  101,605 imported rows are all NULL. `pipeline.project_scope()` takes a
  `default_project_id` for exactly this — omitting it made every project
  inherit the movie backlog.
- Workers are scoped by the `user_projects` table. **No rows = no restriction**,
  because that's the state every existing worker is in.
- **Projects are declared in code**, in `pipeline.PROJECT_DEFS`, and reconciled
  into the database by `sync_projects()` on every startup. There is no UI to
  create or rename one, by the owner's explicit instruction — he states the
  name he wants and it goes in the registry. A project is a pipeline, not a
  setting: standing one up needs a source, a script, accounts and worker
  assignments, all of which are code anyway.
- A project's **slug is immutable**. Per-project pipeline overrides live under
  `pipeline.<slug>.<key>`, so renaming it would orphan them silently and the
  project would fall back to global defaults with no error. Change `name`,
  never `slug`.
- `sync_projects()` never touches `process_weight` or `is_active` — those are
  dashboard levers, and a deploy must not re-enable a project you turned off.

## FineArtAmerica title rules (measured 2026-08-13)

FAA silently rewrites artwork titles. Verified by submitting all 165 distinct
non-ASCII characters from the celebrity database and reading back what saved:

- **Folded to ASCII**: Latin-1 letters plus `š Š ž Ž` — `é→e  ö→o  ñ→n  ç→c
  ø→o  å→a  æ→a  þ→b  ð→o  ß→Ss`. Case is usually preserved but NOT always:
  `Ë→e  È→e  Ì→i  Õ→o` come back lowercase. Encode the observed table; do not
  infer it.
- **Deleted outright**: everything else — Eastern European diacritics
  (`ł ć ş č ğ ń ż ę ř ă ą ě ś ň ő ź ď`), Turkish `ı İ`, macrons, ALL
  punctuation (apostrophes, quotes, hyphens, `… • · × ™ © £ « »`), symbols,
  arrows, superscripts.
- **Max title length is 100 characters**, truncated silently.
- **The first character is upper-cased.**
- A title that normalises to EMPTY is rejected with a page reading
  "Please use only A-Z in your artwork title" — an HTML error page, not an
  HTTP error, so it must be detected by content.

Consequences that are not optional:

- Normalisation belongs in `render_remote_title`, NOT in the uploader, so the
  stored `remote_title` equals what the listing actually shows. Otherwise the
  planned reconciliation scanner reports thousands of false mismatches.
- Titles must be validated BEFORE dispatch. Empty, under ~2 characters, or
  more than half the length lost => flag for the admin to edit and resend,
  never send to the node.
- This applies to the movie project too. `Amélie`, `Léon`, `WALL·E` have been
  silently mangled all along.

## Code map

```
app/
  models.py            All tables. Pipeline models are at the bottom with a
                       design contract comment — read it before adding one.
  projects.py          Active-project resolution + query scoping. Read its
                       docstring before touching navigation.
  diagnostics.py       Read-only consistency scanner (DB vs disk vs pipeline).
                       Never writes — see the docstring for why.
  pipeline.py          ALL post-production policy. Settings resolution,
                       greenlight, dispatch/claiming, templates, encryption.
                       DEFAULTS is the only place a pipeline literal belongs.
  payments.py          Eligibility + payment runs. Greenlight hooks in here.
  routes/
    worker.py          Worker-facing (file name is historical; URLs don't say
                       "worker")
    admin.py           Admin dashboard, review, payments, users, backups
    pipeline_api.py    Machine API,  /api/pipeline,    node bearer token
    pipeline_admin.py  Dashboard API, /admin/pipeline, admin session
  templates/           Jinja2. base.html holds the nav.
  static/js/           One file per page, vanilla JS, no build step.

worker_service/        Runs on the Windows VPS. agent.py is the loop.
scripts/
  create_admin.py
  migrate_pipeline.py  Schema + legacy import + backfill. Idempotent.
  reset_workflow.py    Wipe all WORK (posters, pipeline, payments, chat, log)
                       and reset titles to pending. Keeps users, accounts,
                       settings, projects. Backs up first; refuses on a
                       production-looking DB without --force.
  dev_setup.py         Local dev: wipes and rebuilds everything. GUI + --cli.
DEV_SETUP.bat          Double-click launcher for the above (creates .venv).
```

---

## Running it locally

Double-click `DEV_SETUP.bat` (or `python scripts/dev_setup.py`). It wipes
`poster.db`, `workspace/` and `backups/`, then rebuilds:

- `admin` / `123456` and `worker1` / `123456`
- N master titles to claim
- Completed titles with **real PNG files** on disk (so the admin gallery,
  payments and the pipeline all have something to work with)
- A payment run, a registered pipeline node (token printed), a disabled demo
  marketplace account, and the seeded work already greenlit

The GUI has START SERVER / OPEN BROWSER buttons. `--cli` does identical work
headlessly through the same code path.

**It refuses to run against a production-looking database** (>5,000 master
titles, or >3 payment runs). That check is verified against the real
101,605-row database — do not weaken it. `--force` overrides, CLI only.

**Dependencies are checked before anything is deleted** (`preflight()`), and
`DEV_SETUP.bat` re-syncs `requirements.txt` on every run. Both exist because
adding `cryptography` to requirements after a venv already existed left setup
dying mid-run with a wiped database and no users. If you add a dependency the
tool needs, add it to `REQUIRED_MODULES` in `dev_setup.py` too.

---

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
- **`external_id` is the universal key.** The `0` column from the original CSV.
  It is the folder prefix (`50. Pulp Fiction (1994)`), `MasterTitle
  .external_id`, and the key in both legacy JSON files. Everything joins on it.
- **`Base.metadata.create_all()` does not ALTER existing tables.** New columns
  need an explicit migration (see `migrate_pipeline.NEW_COLUMNS`). The README's
  per-round notes are the precedent.
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
- **Comments explain *why*, not *what*.** The existing code does this well —
  match it. Several modules carry a design-contract header; keep those current.

---

## The legacy processed archive (2026-08-04)

The 4,865 files the owner processed by hand live on his laptop at
`FineArtAmerica Tell-A-Vision\Outputs\Straight From Photoshop\` in exactly
the layout the pipeline now writes: `{date}/{external_id}. {Title} ({Year})/`.
22 date folders, 2,077 titles.

They are being copied to `S:/fineartamerica/GR(Movie&Series)/processed/` with
rclone (`UPLOAD_TO_STORAGE.bat` next to the Outputs folder), which is a plain
file copy — no database involvement.

Registering them as `ProcessedImage` rows is a SEPARATE, later step, and it
has to happen against **production**, because that is the only database that
still holds the `saved_posters` rows they belong to. The match is:

    folder prefix  -> MasterTitle.external_id      (exact)
    " N_Painted"   -> the Nth poster of that title (exact)

4,821 of 4,865 files map exactly. The other 44 are the titles containing a
dot — `E.T.`, `Kill Bill Vol. 1`, `Mr. & Mrs. Smith` — where the old JSX did
`split('.')[0]` on the filename, so every poster in those titles wrote to the
same truncated name and overwrote each other. Each of those folders now holds
exactly ONE file with no index in its name. The title is recoverable, the
per-poster mapping is not; they are cheapest to simply reprocess.

## Data state (2026-07-30)

| | |
|---|---|
| Master titles | 101,605 (98,004 still pending) |
| Completed | 3,467 titles / 7,972 live posters |
| Processed + uploaded to FAA | 4,811 images (+4 removed) |
| **Backlog awaiting processing** | **~3,161 posters ≈ 32 days at the 100/day cap** |
| Paid | 12 runs, 7,975 posters, ~39,875 KES at 5 KES/poster |
| Workers | `humphrey` (currently disabled, idle since ~24 Jun), `test1` (deleted) |

Processing stopped 24 May while the worker kept going to 24 Jun — that gap is
the backlog. The owner paused work to get this automation built.

Legacy → DB matching was verified at **98.9%**; the 54 misses are the earliest
batch, live on the marketplace but with no surviving poster rows. The migration
writes them to a report rather than guessing.

---

## Deployment

```bash
ssh root@178.105.34.144
cd /path/to/poster_downloader_web
git pull
# Back up poster.db BEFORE any schema change.
docker compose exec web python scripts/migrate_pipeline.py --schema-only
docker compose up -d --build
```

Full sequence, including the legacy import and the Windows node, is in
`PIPELINE.md` §5.

---

## Working with this owner

- He wants to understand the system, not just receive it. Explain trade-offs.
- He will ask "what about X" about the thing you glossed over. Cover failure
  modes, recovery and debugging up front.
- He is cost-conscious and picks cheap, simple, mainstream hosting.
- **Anything he might want to tweak belongs in the dashboard.** He has said
  this more than once. Treat a hardcoded value as a defect.
