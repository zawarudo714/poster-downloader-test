<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## What this is

A print-on-demand operation. Two halves:

1. **Sourcing** — a FastAPI + SQLite + vanilla-JS app where paid workers
   claim titles from a master list, find high-quality images for them, and
   save them into an audit-trailed folder tree. The admin reviews quality,
   flags bad images, approves completions, and pays weekly.

2. **Post-production** — process each image, archive it, upload it to the
   marketplace. HOW it is processed is a property of the project. See
   `PIPELINE.md`.

Live at `178.105.34.144` (Hetzner, Docker). The owner is a solo operator, not
a developer — he reads code but does not want to edit it to change behaviour.

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
| **Master** | Dashboard (project cards), Payments, Chat, Projects, Users, Backups, Email, Activity Log, Stats, Diagnostics, Listing check |
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
  `projects.scope_titles()` (admin), `worker._worker_project()` (worker) or
  `pipeline_admin._title_scope()`. **Nothing filters on `project_id` by hand.**

  This file used to name `scope_titles_multi()` as the worker helper. That
  was the UNION-of-all-their-projects version, which is the bug described two
  bullets down — it was replaced by the one-active-project rule and then sat
  unused for weeks while these notes still recommended it. Deleted
  2026-08-27. **A helper that these notes name is a helper a future session
  will reach for, so an obsolete one left in place is a trap, not just dead
  weight.**
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

