# Project brief for AI sessions

Read this first — including **"HOW TO WORK HERE"** near the bottom, which
sets out what is expected of you before, during and after any change. Then:

- **`tools/DEPLOY_LOG.md` — what is actually LIVE on the server.** One line
  per deploy, newest first, written by the deploy tool only when the server
  was confirmed to be running what was pushed. Read that file rather than
  running `git log`, `git diff` or `git status` to work it out: those cost
  far more to read and answer a different question (what is committed, not
  what shipped). If the repo contains work newer than the top line, that
  work has not been deployed.

- **`MULTIPROJECT.md` — read it before writing ANY code.** This app runs
  several niches side by side. Nearly every bug it has had came from code
  assuming there was only one. That file is the contract: how to say the
  right words, how to scope a query, which stage claims what, and the traps
  already hit in production.
- `PIPELINE.md` if the work touches post-production.

**The single most important thing to understand:** this is a MULTI-PROJECT
system. Two niches exist today (movie/series posters, MUSIK music artists)
and more are planned. When the owner asks for a new feature or a new niche,
it must not inherit mechanisms from another project that do nothing for it,
and it must not inherit that project's vocabulary either. Ask which pieces
would be dead for it and say so explicitly, rather than copying an existing
project's definition.

---

## What this is

A print-on-demand operation. Two halves:

1. **Sourcing** (built, in production) — a FastAPI + SQLite + vanilla-JS
   app where paid workers claim titles from a master list, find high-quality
   images for them, and save them into an audit-trailed folder tree. The
   admin reviews quality, flags bad images, approves completions, and pays
   weekly. Where the images come from depends on the project: the movie
   project sends workers to TMDB in another tab; MUSIK searches Brave inside
   the page.

2. **Post-production** (built, in production) — automates what the owner used
   to do by hand: process each image, archive it, and upload it to the
   marketplace. HOW it is processed is a property of the project — the movie
   project runs a Photoshop painterly effect on a Windows node; MUSIK
   generates a new image with OpenAI on the server itself. See `PIPELINE.md`.

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
  `projects.scope_titles()` (admin), `scope_titles_multi()` (worker) or
  `pipeline_admin._title_scope()`. **Nothing filters on `project_id` by hand.**
  Three endpoints once hand-rolled `or_(project_id == X, project_id IS NULL)`,
  which reads as "this project plus anything unassigned" and is only correct
  for the DEFAULT project — MUSIK inherited all 101,605 NULL movie rows.
- Every pipeline API endpoint resolves its project through
  `pipeline_admin._project()`, which falls back to the ACTIVE project. They
  used to call `P.resolve_project(db, project_id)` with a query parameter the
  dashboard never sent, so every one of them silently operated on the movie
  project — the Greenlight tab inside MUSIK listed Inception, and pressing
  the button there would have promoted movie posters.
- **`project_id IS NULL` means the DEFAULT project, not "any project".** The
  101,605 imported rows are all NULL. `pipeline.project_scope()` takes a
  `default_project_id` for exactly this — omitting it made every project
  inherit the movie backlog.
- Workers are scoped by the `user_projects` table. **No rows = no restriction**,
  because that's the state every existing worker is in.
- **A worker's queue scopes to the ONE project they are standing in**, via
  `worker._worker_project()` — never to the union of their projects. The first
  version used the union and, the moment a worker had two projects, GET pulled
  a mixture, Browse All Titles listed 201,133 rows, and RETURN UNWORKED handed
  back titles from a project they weren't looking at. `user_projects` says what
  they MAY touch; the active project says what they ARE touching.
- **Projects declare their UI**, they are not sniffed. `search_mode`
  ('external' vs 'inpage'), `processor`, `has_year`, `has_content_type`,
  `has_review_gate`, `item_noun`. An earlier version decided "does this
  project search in-page" by checking whether a settings row happened to be
  blank, which meant a project could end up showing both an Open TMDB button
  and a search grid, or neither.
- `sync_projects()` applies a spec's `settings` overrides on the SAME deploy
  that creates the project. It used to `continue` after creating the row, so
  overrides only landed on the second deploy — if at all.
- **A new project is a conversation, not a copy.** Do not clone the movie or
  MUSIK definition and hope. Ask what the niche actually needs, and say
  explicitly which existing pieces would be DEAD for it — a project that
  doesn't use Photoshop shouldn't inherit the JSX editor and the sharpen
  radius, one whose sheet has no year shouldn't inherit the YEAR column.
  `Project.processor` / `has_year` / `has_content_type` / `has_review_gate`
  exist so the answer is data, not a template edit.
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

## HOW TO WORK HERE — the standing rules

These are not optional and they are not something the owner should have to
ask for. He is not a coder; his only way to check your work is to click
around afterwards, which means every miss costs him a deploy cycle and an
evening. So the burden is on you to make your method visible BEFORE it is
expensive, and to speak in plain terms rather than implementation detail.

### 1. Audit before you build

Anything that touches a screen, or a mechanism more than one thing uses,
starts with an inventory — not with code:

> screen → control → what it does → **does it do anything in THIS project?**

For plumbing: what reads this, what writes it, what breaks if it is wrong.

Why this and not a search: searching finds WORDS. Only enumerating forces
the question "should this exist here at all". A real failure caused by
skipping it: MUSIK's flag card had an "Open source" button that had been
correctly RENAMED and still linked to TMDB — a project that never touches
TMDB. Renaming made it worse, because now it looked right.

**Walk it as the user, including states you only reach by acting.** Claim a
title, save an image, tap the saved image, flag it, then look at the worker's
view. Most dead mechanisms are not on the page at load; they appear after a
click, which is exactly where a text search cannot go.

### 2. When you implement something, revise everything it touches

Not just the thing asked for. Anything that even remotely interacts with it:

- Fix bad interactions yourself, and **tell him plainly which ones you
  fixed** — one line each, in normal language.
- Only stop to ask when it is a genuine CHOICE (two defensible answers, or
  it changes what he sees or pays). Do not ask about things that have one
  correct answer; just do them and report.
- If a mechanism has become obsolete for this project, REMOVE it. Do not
  rename it, hide it, or leave it disabled. A control that cannot do
  anything useful here is not a label problem.

### 3. Say what you verified — and what you did not

State it without being asked, every time:

- what you checked by READING the code
- what you checked by RUNNING something
- what you did not check at all

"It compiles" is not verification. `py_compile` does not catch an undefined
name; a missing import once 500'd a whole page after being called "verified".
If you claim something is verified, name the test.

### 3b. Ask what a mechanism SERVES, not where it appears

The most expensive misses in this codebase have all been the same shape:
reasoning about a thing by the screen it is displayed on, rather than by
what actually depends on it.

A real one: the "no worker machine is running" warning was written as a
per-project check gated on `processor == 'photoshop'`. Standing inside MUSIK
— whose processor is 'gpt' — it said nothing at all, while the very node
MUSIK's UPLOADS run through was dead. The node does two jobs for two
different reasons; the check only knew about one of them.

So for anything you touch, ask in this order:

1. **Who depends on this?** List them. Not "which page shows it".
2. **Is it shared?** Nodes, marketplace accounts, storage, selectors and
   timings are account-wide or marketplace-wide, NOT per project. A shared
   fact displayed inside one project is invisible to every other one — put
   it at master level.
3. **Does it serve more than one purpose?** The node processes AND uploads.
   A check covering one of those silently passes while the other is broken.

The general form: **a per-project screen may only report a per-project
fact.** If the underlying thing is shared, the alarm belongs on the master
dashboard, and the per-project view explains what it means locally.

### 3c. Check the FILE, not your intention

Every deletion and every move is a structural edit, and the thing you must
check is the file that now exists — not the change you meant to make. These
are not code review opinions, they are mechanical checks a script can do in
seconds, and skipping them has already cost a deploy cycle.

Whenever you cut, move or reorder a block, run the check that matches the
file type before saying you are done:

| You edited | Prove |
|---|---|
| a Jinja template | `<div>`/`<section>`/`<form>` opens == closes, and the template still parses |
| a JS file | it parses (`node --check`) and every `data-` hook it queries still exists in the template |
| Python | no undefined names (AST or `pyflakes`), not just `py_compile` |
| a settings key | it is read somewhere, and something writes it |

**Deleting is the dangerous edit, not adding.** Removing a panel means
removing its opening tag, its body AND its closing tags — a slice that starts
at the right place and ends one tag early leaves markup that still renders,
still parses as a template, and quietly reparents everything below it.
Browsers do not report this; they "repair" it by closing containers early,
so the visible symptom lands somewhere unrelated to the edit. That is exactly
how removing finished instructions from the NODES tab made the ADD ACCOUNT
box on the UPLOAD tab stop appearing.

**A page that loads is not a page that works.** There is no compiler for HTML.
The only honest check is to parse the tree and assert the thing you care about
is where you think it is — in particular that no modal, panel or control has
ended up inside a `hidden` ancestor, because unhiding a child of a hidden
parent does nothing and produces the perfect silent failure: a button that
responds to every click by doing nothing at all.

**When the owner reports "X does nothing", suspect the last structural edit to
that page BEFORE suspecting X's logic.** The handler is usually fine. Check
what shipped most recently against `tools/DEPLOY_LOG.md` and diff the region
you touched.

### 3d. A symptom on one screen is not a fault in that screen's code

"The uploads stopped" is a statement about a PIPELINE, and a pipeline runs
across machines. Before reading the upload code, establish where the work
physically stops:

1. **Which machine performs this step?** Processing is per project — MUSIK
   generates on the Linux server, movies run Photoshop on the Windows node.
   **Uploading is ALWAYS the Windows node, for every project.**
2. **Is that machine alive?** A project whose processing happens on the server
   will sail through processing and stop dead at upload the moment the node is
   down, which reads as "the upload feature is broken" and is not.
3. **Only then, the code.**

Ask "what does this design need next, and who provides it" before you ask
"what is wrong with the uploader". Say which of the three you checked.

### 4. Declare the blast radius

Before building: what else touches this, and what could break that he would
not think to test? "Nothing" is a claim he can hold you to. A list tells him
what to click after deploying.

### 5. Prefer impossible over detectable

1. **Impossible** — the wrong state cannot be represented. Best.
2. **Loud** — it fails immediately, at the right moment, saying why.
3. **Detectable** — Diagnostics or Needs Attention finds it later.

Every check on tier 3 is an admission that 1 and 2 were skipped. He would
rather have well-formed flows than a growing list of warnings. Before adding
a diagnostic, ask: why can this state not simply be impossible? Sometimes the
honest answer is "the marketplace is outside our control" — that is fine.
Often it is laziness.

### 6. The third-project test

For every change: **would this still be right for a third project?** If the
answer needs "well, for MUSIK…", it is not finished. See `MULTIPROJECT.md`.

### 7. Every bug becomes a rule — this file is the memory

**Standing instruction from the owner: whenever a bug is found — whether he
reports it or you trip over it yourself — you update this file before you
finish.** Not a note about that one bug: the GENERAL shape of it, written so
that the next session catches the next instance of that shape while building,
instead of after a deploy.

A future session starts with no memory of this one. The only thing that
carries forward is what is written down, so a fix that leaves no rule behind
guarantees the same class of defect comes back.

Write the rule at the right altitude. Too specific and it never fires again:
*"check the marketplace account modal"* is worthless. Too vague and it is
noise: *"be careful"* is worthless too. The test is whether the rule, applied
blind to a DIFFERENT part of the system, would have caught this. Each rule
above earned its place that way — 3c came from a deleted panel breaking an
unrelated button, 3d from reading upload code when a whole machine was the
problem.

**The rule goes in the section it belongs to, and it names the real incident
in one line.** The incident is what makes it believable enough to obey. A
rule with no story behind it reads like generic advice and gets skipped.

### 8. Claimed work must always end in a reported state

Anything that takes work off a queue owns it until it says what happened. The
pipeline is a chain of claims — a node claims images, marks them 'processing'
or 'uploading', and the server waits. Silence is not a state.

So for any claim/report pair, check all THREE exits, not just the happy one:

1. it succeeded → reported
2. it failed in a way you anticipated → reported
3. **it threw somewhere you did not wrap** → reported, or the item is
   stranded and nothing will ever say why

Number 3 is the one that keeps happening, and it has a signature: work that
is claimed BEFORE the risky setup step. `run_batch` claimed a whole batch,
then called `start()` and `login()` outside any handler. Chrome failing to
launch left every item at 'uploading' with the reason written only to a
console on an unattended VPS. The reaper released them, the next cycle
claimed them, and it stranded them again — forever, looking alive the whole
time.

Concretely: **an exception must never be able to escape past the point where
the work was claimed.** If setup can fail, either claim after it, or catch it
and report the claim as failed with the real error text. And an error that is
only written to the node's local log does not exist — the owner cannot read
that machine.

---

## Adding a new project — what is expected of you

The owner will describe a workflow, roughly like:

> worker >> gettyimages.com >> save >> admin greenlight >> photoshop >> upload

That is the whole specification of what the project DOES. Your job is to
work out what it therefore does NOT do.

**Inherit nothing by default.** Start from the stated flow and add only what
it needs. Then go through the other projects' mechanisms one at a time and
say out loud which are DEAD for this one — before building. Examples of the
reasoning expected:

- Its images come from an external site, so it needs the paste-a-URL box and
  an "Open <source>" link — and NOT the in-page search grid, the Brave keys,
  or the deep-search button.
- It uses Photoshop, so it needs the JSX editor, the sharpen settings and a
  Windows node — and NOT the OpenAI key, the prompt box, the style reference
  or the spend cap.
- It has an admin greenlight but no mention of judging output, so no review
  gate — Photoshop is deterministic and there is nothing to approve.

The converse trap is just as real: MUSIK searches in-page and saves by
tapping, so a "paste replacement URL" field on a saved image is obsolete
there — the worker would delete and re-pick, never paste. It survived
because it was inherited rather than justified. Apply that judgement.

**Ask about the gaps.** The owner normally states the specifics, but where
his description implies a step without pinning it down, ask — openly, not
with a leading guess. If he describes a worker "getting images" without
saying from where, ask how the worker finds them: another tab at a named
site, a search grid inside the page, an upload from their own machine? The
answer changes the source field, the search mode, half the worker UI and
which settings are dead. Better one question now than a project that
inherits the wrong half of another one.

---

## Working with this owner

- He wants to understand the system, not just receive it. Explain trade-offs.
- He will ask "what about X" about the thing you glossed over. Cover failure
  modes, recovery and debugging up front.
- He is cost-conscious and picks cheap, simple, mainstream hosting.
- **Anything he might want to tweak belongs in the dashboard.** He has said
  this more than once. Treat a hardcoded value as a defect.
- He is not a coder. Explain in plain language what a thing DOES and what it
  costs him, not how it is implemented — unless he asks, and he often will.
- He notices the thing you glossed over. Say the uncomfortable part first.
