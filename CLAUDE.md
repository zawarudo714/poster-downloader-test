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

   **BUILT for FineArtAmerica, 2026-08-20.** TeePublic still to come — it
   needs a reader module and an entry in `service.READERS`, nothing else.
   What actually got built, where it differs from the plan above:

     * An ACCOUNT EXISTS ONCE — held to, and it is now enforced by the
       `account_projects` link table rather than by a nullable column. The
       plan said "make project_id nullable rather than inventing a parallel
       table"; that was wrong, because one account serves SEVERAL projects,
       which a single column cannot express. `project_id` survives as a dead
       legacy column, backfilled into the link table at startup.
     * Reading does NOT happen on the server. FAA challenges it as a bot, so
       it is a node job using the account's own Chrome profile. See the
       FineArtAmerica behaviour section.
     * ABSOLUTE rows, never stored deltas — held to, and it is the single
       most important rule in `earnings/service.py`. Every figure on the
       screen is arithmetic over rows computed at read time.
     * Figures revise DOWNWARD and a drop is not a bug — held to, and it is
       said on the page so the owner is not left wondering.
     * Read-only, sibling of `diagnostics.py` — held to.
     * The "next payout" figure is NOT computable: FAA pays on the SHIP date
       and does not publish it. Their `Current Balance` is authoritative;
       anything we derive is an estimate and must be worded as one.

6. **Ban recovery / account handover. BUILT.** `ban_account()` and
   `hand_over_account()` in `pipeline.py`, MARK BANNED and HAND OVER TO… on
   the Upload tab. A banned account keeps its row and its history — it is not
   a disabled one, because its listings are gone from the marketplace and
   have to be rebuilt elsewhere. Handover reuses `requeue_for_account()`, so
   the review-gate rules apply to a rebuild exactly as they do to a first
   upload. `check_orphaned_bans` in Diagnostics catches a banned account
   whose catalogue was never moved anywhere.

   Untested against a REAL ban — nothing here has been through one.

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

**The node now does THREE jobs, not two** (added 2026-08-20):

| Job | Why it must be the node |
|---|---|
| Photoshop | needs a real desktop and the licensed app |
| Uploading | fills a form and sends a file — every project, always |
| **Reading earnings** | FAA challenges the server as a bot; the node's Chrome profile already cleared it |

That third one is easy to forget and it changes the answer to "why did X
stop". Note also that the loop is **serial** — one thing at a time — so an
earnings read cannot collide with an upload; it can only delay it.

**The quiet window** (added 2026-08-20). Each night from
`earnings_quiet_from` (default 22:00, node-local) the dispatcher stops handing
out NEW work until that night's earnings read has been dealt with. Work
already in flight finishes normally.

It is a WINDOW, not a switch, and that distinction is the whole design.
Nothing is stored and nothing is toggled: `quiet_window_state()` looks at the
clock and at whether tonight's read has been attempted, and answers fresh
every time. A switch has two edges and the second one can be lost, which
leaves uploading dead while everything looks fine. There is no second edge
here — the read finishing, the read failing, or midnight arriving all reopen
it on their own.

If work goes quiet and the page does not SAY why, that is a defect. A working
feature that looks like a fault is a bad feature.

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
- **Accounts are scoped by `account_projects`, and there NO ROWS MEANS NO
  PROJECTS — the exact opposite of `user_projects`.** Read the worker
  convention across to accounts and every earn-only account silently becomes
  an upload target for every niche. An account with no rows is one nothing is
  uploaded to: the TeePublic accounts that just sit there earning. It still
  appears on Earnings, because reading revenue and uploading are two
  capabilities of ONE account.
- **An account exists once and may serve several projects.** One FAA account
  carries both niches. Before the link table the only way to do that was to
  create it twice — two Chrome profiles, two copies of the password, and a
  daily upload limit the marketplace applies ONCE being counted as two. Never
  scope by the legacy `UploadAccount.project_id` column; use
  `pipeline.accounts_for_project()` / `project_ids_for_account()`.
- **A batch is single-project even when the account is not.** The node gets
  ONE settings blob per batch, and the title template, keywords and
  description all differ per project — so `claim_upload_batch` picks one of
  the account's projects for that turn and takes only its rows. A mixed batch
  would list MUSIK artwork under the movie template, publicly, on a real
  marketplace.
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

## FineArtAmerica behaviour, measured (do not re-derive these)

**There are TWO upload forms live at the same time.** `updateartwork.html`
and `updateartwork2025.html`, chosen per request, apparently at random. The
selector `name:artworkname` exists on the old one and not the new one, so an
upload fails whenever FAA happens to serve the 2025 page. Observed
2026-08-17: the movie project failed three times in a row while MUSIK
succeeded on the same account minutes later, which looked exactly like a
per-project bug and was not.

Consequences:

  * A missing form field is NOT proof the form changed. It is usually "this
    request got the other page". That is why `upload_pause_after_failures`
    exists — an account is only parked after a RUN of them.
  * When a selector times out, the error lists every field name on the page,
    so the fix is copy-and-paste rather than reading saved HTML.
  * The real fix, not yet built: let a selector hold alternatives, so one
    account can straddle both form versions unattended.

**FAA challenges the Linux server as a bot, but not the Windows node.**
Reading the control panel with a plain HTTP client returns "Verify Visitor —
Are you human? Please check the box in order to continue", regardless of
headers or which URL is used. The node does not see this, because it arrives
in a real Chrome carrying the account's own profile — the one that cleared
the challenge once and holds the cookie. This is why earnings reading is a
node job. Do not try to solve it with headers, a different HTTP client, or a
guessed endpoint; all three were tried and cost three deploy cycles.

**The Balance page is a running ledger and its own checksum.** 78 rows for
this account = 70 sales + 8 payouts, and gross sales $1,477.21 − payouts
$1,178.93 = $298.28, which is exactly the Current Balance FAA prints at the
top. If our arithmetic does not land on their figure, we have missed rows.

**Payouts are on the 15th, for orders SHIPPED before the 15th of the previous
month** — stated on the Balance page. Ship dates are not published, so this
file used to say the next payout could not be computed. **Measured, that was
too pessimistic.** Across his eight payouts, each one equals the balance left
standing after the PREVIOUS one, to the cent — 7 transitions out of 7. Nothing
is held back for late shipping; the whole month moves as one block, a month
late. So `due_next = their balance − everything credited since the last
payout` ($183.78 of the $298.28 balance, the rest in October).

That is a pattern in one account's history, not a rule they state, so
`_payout_rule_holds()` re-tests it on every render and the screen only makes
the claim while it still passes. `Current Balance` remains the authoritative
"what you are owed" — never replace it with arithmetic, which is how the page
once read "probably $1,477.21" against a real $298.28.

## TeePublic behaviour, measured (do not re-derive these)

**TeePublic no longer uses Cloudflare** (observed 2026-08-23, after weeks of
it). All challenge detection was removed with it — FineArtAmerica never
challenged the NODE either, only the Linux server, so nothing was left using
it. If a challenge ever returns, the symptom is three failed mouse paths and
a screenshot, not a silent stall.

**It DOES serve a full-page interstitial wall**, and its "No Thanks" control
is an `<input type="checkbox">` inside a **closed shadow root**. Closed means
sealed: Selenium cannot find it, and page JavaScript cannot reach it either.
There is no selector to write and waiting does not help. Do not try —
`querySelector`, `shadowRoot` and text search were all considered and all fail
by design.

A real click does not need a selector. It carries a POSITION and the browser
hit-tests what is under it, sealed or not. So the position comes from mouse
paths the owner records himself (`RECORD_PATHS.bat`), replayed through
Chrome's own input channel — which also means **no Remote Desktop session is
needed for playback**, only for recording. An OS-level macro recorder was the
obvious alternative and was rejected for exactly that reason: it would have
required a live RDP session forever and failed silently the moment it dropped.

**The wall is detected by what is MISSING**, never by anything on it. Its
class names are randomised (`tOHY4`, `qrvwN4`) and would break on their next
deploy while blaming something else entirely.

Two markers, because two kinds of page:

  * the ACCOUNT page — its own four labels, the same ones the parser needs
  * everything else (search, store, design, edit) — the **header logo**,
    `vc-header-logo__image` / `assets/logos/tp-full`. Matched in raw HTML
    because a logo has no text, which is the same exception the sign-in
    field name gets and for the same reason: it is structural, not a vendor
    word that might merely be loaded.

**The logo test is what separates "no search results" from "we never
looked", and getting that backwards would deactivate a healthy catalogue.**
An empty results page and the wall are both "no designs found"; only the
logo tells them apart. So the scan asks the cheap question first — were
there results? — and only consults the logo when there were none. Asking
before every page would add the settling delay to every one of several
thousand designs.

The wall is also cleared ONCE per browser when it opens, on a page we do not
care about, so the per-page check stays a rare fallback.

**A lapsed session looks identical to the wall** and must be ruled out FIRST,
by the sign-in form's field name. Otherwise three recorded paths get spent
clicking at a sign-in page and the report reads "stuck at the wall" when the
answer is two minutes with PROFILES.bat.

**TeePublic keeps you signed in for weeks and punishes knocking; FAA forgets
you in minutes and does not mind.** Hence `signin_on_read` per marketplace —
see rule 5b.

**Designs silently drop out of search.** The listing still exists and the
page still loads; it just stops being findable, earns nothing, and nothing
tells you. Deactivating and immediately reactivating usually restores it.
That is what the TeePublic tab automates.

**The numeric design ID is in the design's own address** —
`/t-shirt/86734220-tomb-raider`. It comes free with the store listing, and
EVERYTHING matches on it: search results, the deactivate form, the edit page.
Never compare URLs. The previous tool did, and a design sitting on page one
of the results read MISSING because the store's copy of the link carried
`?store_id=4129428` and the search result's copy did not.

**Deactivate is a form POST, not a link.** On the design page:
`<form class="button_to" method="post" action="/designs/<id>/deactivate">`
with a one-time `authenticity_token`. So navigating to that address returns
404 — the button must be pressed on a freshly loaded page — and the old
tool's `a[href*='/deactivate']` matches nothing there, because it is a
`<button>`. Reactivate is `/designs/<id>/edit` → tick `#terms` → press
`button.publish-and-promote-button[value='publish']`.

**NEVER reactivate from the marketplace's inactive list.** One real account
has 92 active designs and **379 inactive** ones the owner turned off himself
over months. Republishing "the first N on `/inactive`" cannot tell those
apart from the handful we just switched off. Reactivate the exact IDs we
recorded deactivating, and nothing else.

**Search results are not in the raw HTML**, so the visibility check needs a
real browser. The store listing and the individual design pages ARE plain
HTML and are fetched with `requests` — which is why most of a scan costs
almost nothing. Only the search step is expensive.

**One browser per ACCOUNT, held open — never one per design.** The owner's
original script launched and quit a whole Chrome for every design; at 1,881
designs and 3-5s per launch that was over two hours of doing nothing but
starting browsers, and it was most of why a scan took ten.

**Scope note, deliberately open:** a TeePublic project WITH uploading is
plausible later — the owner's tool already contains a working uploader. This
tab is therefore marketplace-level (accounts), not project-level, and slots
beside such a project rather than tangling with it.

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
  earnings/            Money coming IN. Sibling of diagnostics.py, NOT of the
                       pipeline — nothing here dispatches or changes a design.
    faa.py             Parsers for FineArtAmerica's Sales and Balance pages.
                       Every function takes a STRING, never a URL, which is
                       what let the fetching move to the node for free.
                       Deliberately contains no page URLs at all.
    matching.py        Attributing a sale to one of our designs. Refuses to
                       guess — see its docstring for why a wrong match is
                       worse than no match.
    service.py         Storing rows, queueing the node's reads, and the
                       figures each screen needs.
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

**These rules are a NET, not a checklist.** Each one was written after a real
defect, but the point is never the defect — it is the SHAPE. Before starting
anything, read the shapes below and ask which could apply here, including the
ones that look like they belong to a different part of the system. A rule
learned from a deleted HTML panel is what catches a broken JS hook; a rule
learned from an upload stall is what catches a stuck backup. If a rule seems
irrelevant, say why in one line rather than skipping it silently.

| # | The shape it catches | Related |
|---|---|---|
| 1 | Built without enumerating what is already on the screen | 3b |
| 2 | Fixed the asked-for thing, left its neighbours broken | 1, 6 |
| 3 | Said "verified" without naming a test | 3c |
| 3b | Reasoned from where a thing is SHOWN, not what depends on it | 3d, 6 |
| 3c | Trusted the edit you meant to make over the file that exists | 3, 5 |
| 3c-bis | Invented an external value instead of looking it up or asking | 3c-ter |
| 3c-ter | Built a second path to something that already works — or theorised about a difference instead of reading the thing that works, or retried something the far side counts | 3b, 3d |
| 3d | Debugged the screen instead of the machine doing the work | 3b, 8 |
| 4 | Shipped without saying what else could break | 2 |
| 5 | Added a warning where the state should have been impossible | 8 |
| 5b | Turned one marketplace's measured behaviour into policy for all of them | 6, 3c-ter |
| 5c | Moved a call out of a shared function and dropped it on the way | 3c, 3c-ter |
| 6 | Correct for two projects, wrong for the third | 1, 3b |
| 7 | Fixed a bug and left no rule behind | all |
| 8 | Claimed work with an exit that reports nothing — including catching an error into a counter | 3d, 5 |
| 9 | Shipped a dependency the node was never told to install | 3c, 8 |

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

**A thing with two purposes needs two of everything that can stop it.** An
account both UPLOADS and REPORTS MONEY, and those fail for unrelated reasons
— a bot wall while reading TeePublic says nothing about whether uploading
works, a rejected upload password says nothing about the balance. One shared
`paused_until` meant either failure silenced the other, and the screen could
not say which had happened. Hence `earnings_paused_until` beside it. Before
reusing a status field, ask whether the thing it describes can fail in more
than one way INDEPENDENTLY; if it can, one field is already wrong.

**A long hold on the pipeline must belong to the WORK, not to a switch.** The
TeePublic sweep stops Photoshop and uploads for hours, and it stops them
because a RUN EXISTS — finishing, failing or abandoning the run releases them
on the way out. There is nothing to remember to turn back on, which matters
most exactly when something has gone wrong. Same shape as the quiet window,
one level up. And there must always be a STOP button reachable by the owner:
with a Photoshop backlog measured in weeks, a half-finished run must never be
something only a developer can clear.

**And a cooldown must have a way to end early.** A pause set by a failure
should be cleared by the next SUCCESS, not only by the clock. An account
signed in by hand and read successfully was still skipped by the scheduler
for the rest of its twelve hours, while the READ NOW button worked perfectly
— a disagreement between two paths with no symptom you could see.

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
| a settings key | it is DECLARED in `pipeline.DEFAULTS`, not merely read and written |

**A new settings key is a schema change, not a string.** `get_setting` and
`set_setting` REFUSE any key not declared in `pipeline.DEFAULTS` — on purpose,
so a typo cannot resolve to None. The consequence is that an undeclared key is
an instant 500 on the first page that touches it, and it will pass every
static check there is: the code parses, the names are defined, the hooks
exist. The Earnings tab shipped this way and the page was blank white on the
first click. Before saying a feature is done, grep every `get_setting` /
`set_setting` call you added and confirm each key is in `DEFAULTS`. Note that
`payments.py` has its OWN `get_setting(db, key, default)` which is unrelated
and needs no declaration — check WHICH function is imported before believing
a hit.

**Deleting is the dangerous edit, not adding.** Removing a panel means
removing its opening tag, its body AND its closing tags — a slice that starts
at the right place and ends one tag early leaves markup that still renders,
still parses as a template, and quietly reparents everything below it.
Browsers do not report this; they "repair" it by closing containers early,
so the visible symptom lands somewhere unrelated to the edit. That is exactly
how removing finished instructions from the NODES tab made the ADD ACCOUNT
box on the UPLOAD tab stop appearing.

**Moving a call OUT of a shared function is a deletion, and needs the same
proof.** `open_work_tab()` was correctly taken out of `login()` so earnings
reads would stop running upload hygiene — and never added to `run_batch`. The
method still existed, still had its docstring, still compiled, and nothing
called it: every upload since ran in the login tab, the one tab the legacy
tool always abandoned because Chrome puts a dialog over it. After moving a
call, grep for it and count the CALL SITES, not the definition. Zero callers
of a public method is a defect, not a style question.

**Identify a page by what it SHOULD contain, not by what is wrong with it.**
The TeePublic wall could have been detected by its own markup — and its class
names are randomised, so that check would have worked in testing and failed
on their next deploy while pointing at something else. Detecting it as "the
labels we came for are absent" survives the wall being redesigned, renamed or
replaced, and doubles as the proof we got through afterwards. A positive test
for the thing you want beats a negative test for the thing in the way, and it
is usually the same amount of code.

**Matching a word in raw HTML is not detection.** A page's markup is full of
vendor names, class names and script sources that say nothing about what the
page is DOING. The bot-wall check searched page_source for "captcha", and
TeePublic's ordinary sign-in page mentions "recaptcha" fifteen times in a
dormant widget — so a page whose visible text read "Welcome Back!" parked the
account for three hours. Match the VISIBLE TEXT, and match a sentence the
user would actually see, not the name of a product that might merely be
loaded.

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

### 3c-bis. NEVER invent a value you could look up or ask for

A value you cannot verify is not a default, it is a guess wearing a
default's clothing — and it ships looking exactly like working code.

The incident: the earnings reader needed FineArtAmerica's sign-in page. I
wrote `LOGIN_URL = f"{BASE}/loginpost.php"` and field names `email` /
`password` / `rememberme`, none of which I had ever seen. Two deploy cycles
were spent on HTTP 403 before the owner supplied the real page. Worse, the
correct value already EXISTED in the system: `login_url`, in the selectors
map, dashboard-editable, used by the uploader to log into the very same
account every day.

So, in order, before typing any external constant — a URL, an endpoint, a
form field name, a header, an ID format:

1. **Is it already in the codebase?** Something else almost certainly talks
   to this marketplace already. `grep` the settings map before writing a
   literal. Two copies of one fact are two chances to drift, and the copy
   that breaks is always the newer one, silently.

   **The same applies to a value DERIVED in two places, and that version is
   harder to spot** because neither copy looks like a hardcoded constant.
   The Chrome profile folder was computed as "the account's setting, or this
   default" where it launched the browser, and read as "the account's
   setting" where it killed leftover browser processes. Accounts normally
   have that setting blank, so the launcher used the default while the
   cleaner saw an empty string and returned immediately — the one function
   whose entire job was clearing a stuck profile had never run for any
   account, for months. A fallback default belongs in ONE function that
   everything calls, never inlined at each use.
2. **Can I read it from the source at runtime?** Parsing the form off the
   login page beats hardcoding its action, because it survives a redesign.
3. **Can I ask the owner?** He has the account open in a browser. One
   question costs a minute; a guess costs a deploy cycle and his evening.
4. Only then, and say plainly that it is unverified.

**Never present a guessed value as if it were researched.** If it was not
verified, the sentence to write is "I guessed this, here is how to check
it", not silence.

The general form: **for anything outside this codebase — a marketplace, an
API, an OS behaviour — the code must either read the value at runtime, take
it from a setting, or be told it. Inventing it is not an option, and neither
is inferring it from a plausible-looking pattern.**

### 3c-ter. If something in this system ALREADY does it, reuse that path

The rule above says do not invent a value. This one is bigger: **do not
invent a mechanism either.** Before writing a new way to talk to anything —
a marketplace, an OS, a machine — find what already talks to it and ask why
that would not serve.

The incident, and it is the same evening as 3c-bis: the earnings reader was
written as a fresh `requests` session logging into FineArtAmerica. It got
HTTP 403 with "Verify Visitor — Are you human?". Meanwhile the uploader logs
into the SAME account successfully every single day, because it arrives in a
real browser on the node. The proven path was sitting right there. Three
deploy cycles were spent making a second, unproven path fail in new ways.

The questions, in order:

1. **What already does this successfully?** Name it. If the answer is "the
   uploader", the design is "do what the uploader does", not "do the same
   thing a different way".
2. **Why would that path not work here?** There must be a real reason —
   cost, a machine that is not running, a capability it lacks. "It felt
   heavier" is not a reason. A 150MB browser you ALREADY RUN costs nothing
   extra to reuse.
3. **What is the untested assumption?** Here it was "the pages are plain
   HTML so an HTTP client is enough", which was true of the PAGES and false
   of the LOGIN. State it, and test that specific thing first, before
   building everything downstream of it.

The general form, which links this to 3b and 3d: **a capability belongs to
whatever already has it.** 3b says a shared fact must be reported where it
is shared. 3d says a symptom belongs to the machine that does the work.
This one says a new feature must be built on the machine that can already
reach the thing — and for anything on the far side of a bot wall, a login
wall, or a browser check, that machine is the node.

**A corollary worth stating on its own: when the owner says "just copy how
X does it", that is not a shortcut, it is usually the correct architecture.**
He can see the working path from outside the code. If the answer is "that
would be awkward because…", the awkwardness is the thing to fix, not a
reason to build a second path.

**And when something ALREADY WORKS — even outside this repo — READ IT before
theorising about why ours does not.** He asked why the VPS meets Cloudflare
when his old laptop tool never had, and offered the tool. The answer was one
grep: it contains no login code at all. It opened a Chrome profile he had
signed into by hand and went straight to the work, while ours opened the
sign-in page on every single read. Everything else — datacentre address,
headless, timing — was secondary to the one behaviour we had and it did not.
A working precedent is EVIDENCE, and reading it costs a minute; reasoning
about the difference from the outside had already cost several deploys.

**Corollary, and it inverts an instinct: a retry can be the cause.** After a
failed read the account was queued again immediately — jobs #34 to #38 inside
ninety seconds — because the cooldown governed uploading only. Five sign-in
attempts a minute from one datacentre address is how a site that was merely
suspicious becomes a site that is certain. Before adding or permitting a
retry, ask whether the thing being retried is something the far side COUNTS.
Logins, searches and writes are counted; fetching a page you are entitled to
is not. Note also the fix here was NOT `account_is_available`, the obvious
reuse: it also refuses banned and disabled accounts, which is right for
uploading and backwards for reading money. Reuse the CONDITION that is
genuinely shared, not the function that happens to contain it.

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

**A destructive instruction must be checked by the thing carrying it out,
not only by the thing issuing it.** The profile cleanup names one folder,
decided server-side — and the node still refuses any path that is a
CONTAINER (the profiles folder, the scratch folder, a drive root). A test
pointed an account's own `chrome_profile_dir` at the profiles folder and the
first version cheerfully deleted every account's saved session in one go.
"The caller would never send that" is not a guard.

Every check on tier 3 is an admission that 1 and 2 were skipped. He would
rather have well-formed flows than a growing list of warnings. Before adding
a diagnostic, ask: why can this state not simply be impossible? Sometimes the
honest answer is "the marketplace is outside our control" — that is fine.
Often it is laziness.

### 5b. A lesson from one marketplace is not a rule about all of them

Rule 6 asks whether a change is right for a third PROJECT. This asks the same
about a third MARKETPLACE, and it is easier to get wrong because the lesson
usually arrives as a genuine discovery.

The incident: his old TeePublic tool proved that never opening the sign-in
page is what avoids a security check. Correct — and applied to every earnings
read, including FineArtAmerica, which drops its session almost immediately.
FAA would have failed as "signed out" on the very first read and parked
itself for twelve hours. The owner caught it in one line, before deploy:
*"this is for teepublic only yes? faa almost always requires log in."*

The untested assumption was that a behaviour measured on one site describes
"how marketplaces work". Two sites here behave OPPOSITELY: one keeps you
signed in for weeks and punishes knocking, the other forgets you in minutes
and does not mind. Either single answer is wrong somewhere.

So when a discovery changes how we talk to a marketplace:

1. **Say which site it was measured on.** One site's behaviour is one site's.
2. **Put it in that site's CAPABILITIES row**, not in the node and not in an
   if-statement. `signin_on_read` sits beside `shape` and `sales` for the
   same reason those do — the next marketplace answers by adding a line.
3. **A capability that is gone should be DELETED, not disabled.** When
   TeePublic dropped Cloudflare the whole challenge mechanism went with it,
   because nothing else was using it — FAA challenges the Linux server, never
   the node. A defence left lying around is worse than none: the next session
   reasons about protection that was never firing. (I argued to keep it "for
   FAA" and was wrong; the measurement was already written down two sections
   up. Read the file before defending code with it.)
4. **Pick the default that fails cheaply.** An unmeasured site signs in:
   signing in needlessly costs a slow page, while skipping it when it was
   needed stops the money being read. Wrong in the cheap direction.

The general form: **a fact discovered about one external system is DATA about
that system, never policy for all of them.** Same shape as rule 6, one level
out — and the same shape as `processor` and `has_year`, which exist so a
project's differences are data rather than a template edit.

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

**REVISE, do not append. This is the iron rule.** Before writing anything
down, search these files for what is already said about it. Then:

  * The topic already has a rule → **edit that rule.** A bug with a new
    wrinkle adds a sentence to the existing entry; it does not get a second
    entry of its own.
  * The instruction has CHANGED → **rewrite the old text, do not stack a
    contradiction next to it.** If he ever asks for more technical language,
    the fix is to find "Write in plain words" and change it — not to add a
    second, opposite instruction and leave the next session to guess which
    one wins.
  * Something written earlier was later fixed, or turned out wrong → **go
    back and correct it.** The "make project_id nullable rather than
    inventing a parallel table" note was confidently wrong for two weeks. A
    stale note is worse than no note, because it is trusted.
  * Genuinely new and unrelated → then, and only then, a new entry.

The test is length. This file should get SHARPER as it grows, not longer.
If an edit makes it longer without making it more useful, it is the wrong
edit. Not everything is worth writing down — the bar is "would this have
changed what a future session did".

**Three things every new rule must do**, because the owner has asked for a
net rather than a list of anecdotes:

1. **Generalise past its own domain.** Write it so it fires on a DIFFERENT
   subsystem. 3c came from HTML and its real target is any structural edit;
   3c-ter came from a marketplace login and its real target is any new path
   to anything already reachable.
2. **Link to the rules it neighbours**, and add the row to the table at the
   top of this section. Defects arrive in families — the same evening
   produced 3c-bis (invented a URL) and 3c-ter (invented a whole mechanism),
   and either rule alone would have let the other happen.
3. **Say what the untested assumption was.** Nearly every entry here is a
   case of one unexamined belief — "the panel ends here", "the form field is
   called that", "plain HTTP is enough" — carrying a large build on top of
   it. Naming the assumption is what lets the next session test it FIRST,
   in five minutes, instead of last, after three deploys.

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

**Catching an exception into a COUNTER is the same defect wearing a third
hat.** The store scan wrapped each account in try/except, added 1 to
`errors`, and then reported the stage finished regardless. A missing library
therefore produced a job that logged one red line, said "Job finished", and
advanced the run to "waiting for you" with zero designs checked — so the
screen would have said "nothing is missing" and been believed. A count is
not a report. If a thing that was supposed to happen did not, the caller
must be TOLD, and any state machine downstream must not move on.

**And a REPLY that is ignored is the same defect wearing a different hat.**
The earnings read posted each page to the server and read one key out of the
answer — "is there another page" — discarding the error and the stored count.
So a page the server could not parse produced a run that fetched, stored
nothing, and reported "Job finished". The screen stayed empty and no line
anywhere connected the two. If you asked something a question, say what it
answered.

Concretely: **an exception must never be able to escape past the point where
the work was claimed.** If setup can fail, either claim after it, or catch it
and report the claim as failed with the real error text. And an error that is
only written to the node's local log does not exist — the owner cannot read
that machine.

### 9. A dependency is not deployed by writing it down

The node is updated by COPYING A FOLDER, and copying a folder installs
nothing. `beautifulsoup4` sat in `requirements.txt` while being absent from
the box, and the only code using it had a try/except fallback — sensible
there, fatal to noticing. It surfaced hours after a deploy as a job dying
once per poll cycle with `No module named 'bs4'`.

So, whenever you add an import the node needs:

1. Add it to `worker_service/requirements.txt` **under its PIP name**, which
   is not always its import name. `bs4` is a stub package that merely depends
   on `beautifulsoup4`; installing the stub has left real machines without
   the library.
2. Add it to `REQUIRED_MODULES` in `agent.py`, so the agent refuses to start
   and prints the exact command. A check at startup is loud; a failure at
   first use is a job that fails forever in a log nobody is reading.
3. Say in the deploy note that the node needs `pip install -r`.

The general form: **for any machine updated by copying files, a new
dependency needs a startup check, because there is no install step to hook
into.** Same reasoning as `preflight()` in `scripts/dev_setup.py`, which
exists because adding `cryptography` to requirements after a venv already
existed left setup dying mid-run with a wiped database.

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
- **And it belongs where the thing it governs is SHOWN.** A setting he cannot
  find is a setting he does not have. The nightly earnings times were added
  to Upload Settings — correct by category, useless in practice, because the
  screen that displays "quiet from 22:00" is the Earnings page and that is
  where he looked. One stored value, editable from wherever it is relevant.
- He notices the thing you glossed over. Say the uncomfortable part first.

### Write in plain words. This is a standing instruction, not a preference.

His words, 2026-08-17: *"explain to me step by step using simple words you
keep using big words. From now on make it easy to understand in layman terms
everything we talk about."*

He is not a coder. Every explanation is judged on whether HE can act on it,
not on whether it is technically complete. Concretely:

- **Say what happens, in order, with times or numbers.** When he asked how
  earnings could run on a busy Monday, the answer that worked was a clock:
  "9:50pm the worker is doing Photoshop · 10:00pm the boss stops handing out
  new work · 10:35pm it finishes what it's holding · 10:36pm earnings run".
  The answer that did NOT work was the same thing described as a mechanism.
- **Name the machines by what they do**, not by their role in the code. "The
  Linux server is the boss, the Windows machine is the worker" landed;
  "dispatcher" and "node" did not.
- **Ban the shorthand.** intake, idempotent, derived state, artefact,
  scoping, dispatch, payload. If a word only means something to someone who
  has read the code, it is the wrong word. There is always a plain one.
- **Give the cost in his terms** — minutes lost, designs delayed, money —
  rather than in properties of the design.
- **When he says he does not understand, that is a defect in the
  explanation.** Rewrite it from scratch in simpler words; do not repeat it
  with more detail bolted on. He asked twice before I stopped using jargon,
  and that was two wasted rounds.

This applies to CHAT, not to code comments. Comments explain *why* to the
next engineer and stay technical.
