# Sitewide audit — 2026-08-17

Every control on every screen, walked as each role in each project,
**including states only reachable by clicking** (tap a saved image, raise a
flag, open the catalogue). The question asked of each one is not "does it
work" but **"does it do anything in THIS project?"**

Legend: **DEAD** = present but meaningless here · **WRONG** = does something
incorrect · **OK** = earns its place.

---

## Worker · active title panel

| Control | What it does | MUSIK (in-page search) | Movie (external source) |
|---|---|---|---|
| `↗ Open source` | Opens the source site in a tab | OK — hidden by `search_mode` | OK |
| Paste image URL + SAVE | Downloads from a pasted URL | OK — hidden by `search_mode` | OK |
| SEARCH / DEEP SEARCH | In-page Brave grid | OK | OK — hidden |
| CLEAR / SAVE SELECTED | Acts on grid selection | OK | OK — hidden |
| `↑ DONE PICKING` | Scrolls to the actions | OK | OK — no grid, never shows |
| DONE / SKIP / REOPEN | Title state | OK | OK |

## Worker · saved image, after tapping it

| Control | What it does | MUSIK | Movie |
|---|---|---|---|
| Thumbnail | Opens full size | OK | OK |
| **Paste replacement URL** | Replaces the file from a URL | **DEAD** — there is no URL to paste; images come from the grid | OK |
| **REPLACE** | Submits that URL | **DEAD** — same | OK |
| DELETE | Removes it | OK — this is how you re-pick | OK |

## Worker · change-request (flag) card

| Control | What it does | MUSIK | Movie |
|---|---|---|---|
| **`↗ Open source`** | Links to `source_search_url` | **WRONG** — opens TMDB. MUSIK has no override, so it inherits the global default | OK |
| VIEW ALL | Catalogue of the title | OK | OK |
| GO TO TITLE | Opens and scrolls to it | OK | OK |
| **Paste replacement image URL** | Replace the flagged file | **DEAD** — same reason as above | OK |
| **REPLACE FILE** | Submits it | **DEAD** | OK |
| DELETE FILE | Deletes it | OK | OK |
| SEND FOR APPROVAL | Marks the fix submitted | OK | OK |

## Worker · Browse all titles

| Control | MUSIK | Movie |
|---|---|---|
| **Year column** | **DEAD** — always blank. The admin list hides it via `no-year`; this page never got the same treatment | OK |
| Type column | DEAD for MUSIK, same cause | OK |
| Search / status filter / claim | OK | OK |

## Worker · Save history, Stats

| Control | Status |
|---|---|
| Project filter | OK — only shown when the worker covers more than one |
| Per-day rows, KES | OK |

## Admin · project screens

| Screen · control | Status |
|---|---|
| Review Images tab | OK — shown only when the gate exists and is on, or a backlog remains |
| Title List · type filter | OK — hidden when `has_content_type` |
| Title List · year column | OK — hidden via `no-year` |
| Pipeline · Photoshop settings, JSX editor | OK — gated on `processor` |
| Pipeline · GPT settings, prompt, style image | OK — gated on `processor` |
| Pipeline · Test download / process | OK — hidden for GPT projects |
| Pipeline · Test generation | OK — GPT only |
| **Review Posters · "Paste URL to add one"** | **QUESTIONABLE** — the admin has no URL source for MUSIK either. Left in place: it is the admin's only way to add an image directly, and unlike the worker's box it is not a dead end. Flagged, not removed. |
| **Upload · title template help text** | **MINOR** — lists `{year}` and `{content_type}` as available variables. Both render empty in MUSIK. |

## Shared / master

| Thing | Status |
|---|---|
| Node offline alarm | OK — master dashboard, counts processing *and* uploads |
| Diagnostics | OK — all projects by default, per-project filter, findings tagged |
| Payments, Users, Backups, Activity log, Chat | OK — genuinely account-wide |

---

## Root causes, not symptoms

Three of the four real faults share one cause: **a control was gated on the
wrong question, or on nothing at all.**

1. `source_search_url` has no MUSIK override, so it silently inherits TMDB.
   The active-title button escapes this only because it asks `search_mode`
   first. The flag card asks nothing and just sets the link.
2. The replace-by-URL boxes were never gated on anything.
3. `master_browse.html` never received the `no-year` treatment its admin
   equivalent got.

The lesson matches the rule already in `CLAUDE.md`: ask what a mechanism
*serves*, and gate it on the project's declared capability rather than on
whether some value happens to be blank.
