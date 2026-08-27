# Sitewide control audit

Every control on every screen, walked as each role in each project,
**including states only reachable by clicking** (tap a saved image, raise a
flag, open the catalogue). The question asked of each one is not "does it
work" but **"does it do anything in THIS project?"**

**This is a LIVING inventory. Extend it; do not start a new one.**

> First written 2026-08-17. Nothing referenced it, so on 2026-08-27 a fresh
> session re-did most of it from scratch and missed findings this file
> already had. That is the reason `CLAUDE.md` now carries a document index
> and `preflight.py` fails when a document is not linked from it.

---

## How to read a claim in this repo

Every claim carries its PROVENANCE, because the four contradictions found on
2026-08-27 were all the same shape: a guess written in the same voice as a
measurement, and later acted on as fact.

| Tag | Means | How to treat it |
|---|---|---|
| `MEASURED <date>` | Somebody ran it. Numbers are real. | Trust it. If you doubt it, re-run and update the date. |
| `DECIDED <date>` | A choice, not a fact. | Can be revisited — but say why it was decided before overturning it. |
| `LEAD` | A hypothesis nobody has tested. | **Do not act on it as a diagnosis.** Measure it first. |
| `RULE` | Timeless. Applies until the code changes. | No date needed. |

The worked example is `CLAUDE.md` planned item 9: it said the site was slow
because of 201,133 titles, in the same confident voice as the measured facts
around it. Measured, that query costs 0.16s and the real cause was the
network link. It had already been repeated to the owner as fact.

---

## Status of this audit

`MEASURED 2026-08-27` — every DEAD and WRONG row below was re-checked
against the code as it stands today.

**All three root causes from the 17 August pass are FIXED.** Two entries
remain open and are marked so.

---

## Worker · active title panel

| Control | What it does | MUSIK (in-page) | Movie (external) |
|---|---|---|---|
| `↗ Open source` | Opens the source site | OK — hidden by `search_mode` | OK |
| Paste image URL + SAVE | Downloads from a pasted URL | OK — hidden by `search_mode` | OK |
| SEARCH / DEEP SEARCH | In-page Brave grid | OK | OK — hidden |
| CLEAR / SAVE SELECTED | Acts on grid selection | OK | OK — hidden |
| `↑ DONE PICKING` | Scrolls to the actions | OK | OK — no grid, never shows |
| DONE / SKIP / REOPEN | Title state | OK | OK |

## Worker · saved image, after tapping it

| Control | What it does | MUSIK | Movie |
|---|---|---|---|
| Thumbnail | Opens full size | OK | OK |
| Paste replacement URL | Replaces the file from a URL | **FIXED** — was DEAD, now removed for in-page projects | OK |
| REPLACE | Submits that URL | **FIXED** — same | OK |
| DELETE | Removes it | OK — this is how you re-pick | OK |

`MEASURED 2026-08-27` — `buildPosterCard()` removes both when the mode is
in-page, and now takes the mode as an argument rather than reading the
global, so it cannot drift if the caller ever changes.

## Worker · change-request (flag) card

| Control | What it does | MUSIK | Movie |
|---|---|---|---|
| `↗ Open source` | Links to `source_search_url` | **FIXED** — was WRONG (opened TMDB) | OK |
| VIEW ALL | Catalogue of the title | OK | OK |
| GO TO TITLE | Opens and scrolls to it | OK | OK |
| Paste replacement image URL | Replace the flagged file | **FIXED** — was DEAD | OK |
| REPLACE FILE | Submits it | **FIXED** | OK |
| DELETE FILE | Deletes it | OK | OK |
| SEND FOR APPROVAL | Marks the fix submitted | OK | OK |

`MEASURED 2026-08-27` — gated on `r.search_mode`, the TITLE's own mode.

`DECIDED 2026-08-27` — **the flag list stays scoped to one project.** A
worker standing in MUSIK does not see movie flags at all. The owner
confirmed this is wanted: mixing two niches' change requests in one list is
confusing. Consequence worth remembering: because the list is scoped, the
title's mode and the standing project's mode can never actually disagree
today. The per-title gating above is therefore correctness insurance, not a
live bug fix — it was reported as a live bug and that was wrong.

## Worker · Browse all titles

| Control | MUSIK | Movie |
|---|---|---|
| Year column | **FIXED** — was DEAD (always blank) | OK |
| Type column | **FIXED** — same cause | OK |
| Search / status filter / claim | OK | OK |

`MEASURED 2026-08-27` — `master_browse.html` now applies the same
`no-year` / `no-type` classes the admin Title List uses.

## Worker · Save history, Stats

| Control | Status |
|---|---|
| Project filter | OK — only shown when the worker covers more than one |
| Per-day rows, KES | OK |

`MEASURED 2026-08-27` — Save history is deliberately CROSS-project and
shows a split when a day spans two. That is right: pay is cross-project,
one payment run covers all of a worker's work. Do not "fix" it to match the
queue's scoping.

## Admin · project screens

| Screen · control | Status |
|---|---|
| Review Images tab | OK — shown only when the gate exists and is on, or a backlog remains |
| Title List · type filter | OK — hidden when `has_content_type` is off |
| Title List · year column | OK — hidden via `no-year` |
| Pipeline · Photoshop settings, JSX editor | OK — gated on `processor` |
| Pipeline · GPT settings, prompt, style image | OK — gated on `processor` |
| Pipeline · Test download / process | OK — hidden for GPT projects |
| Pipeline · Test generation | OK — GPT only |
| Review Posters · "Paste URL to add one" | **OPEN — needs a look.** See below. |
| Upload · title template help text | **OPEN — still true.** See below. |

### OPEN · Upload title template help text

`MEASURED 2026-08-27` — `admin_pipeline.html:519` lists the available
variables as `{title} {year} {letter} {index} {content_type} {external_id}
{description}` for every project. `{year}` and `{content_type}` render
EMPTY for MUSIK, whose `has_year` and `has_content_type` are both 0.

Minor: it misleads rather than breaks. Worth gating the help text on the
project's declared capabilities when batch 3 reaches this screen.

### OPEN · Review Posters "Paste URL to add one"

`DECIDED 2026-08-17` — flagged, not removed. The admin has no URL source
for MUSIK either, but unlike the worker's box it is not a dead end: it is
the admin's only way to add an image directly.

`LEAD 2026-08-27` — the control could not be found by that name in
`admin_review_images.html`. It may have moved, been renamed, or been
removed. **Not verified either way.** Confirm when batch 3 reaches the
admin screens rather than assuming the 17 August note still describes what
is there.

## Shared / master

| Thing | Status |
|---|---|
| Node offline alarm | OK — master dashboard, counts processing *and* uploads |
| Diagnostics | OK — all projects by default, per-project filter, findings tagged |
| Payments, Users, Backups, Activity log, Chat | OK — genuinely account-wide |

---

## Root causes, not symptoms

`RULE` — three of the four faults found on 17 August shared one cause: **a
control was gated on the wrong question, or on nothing at all.**

1. `source_search_url` has no MUSIK override, so it silently inherited
   TMDB. The active-title button escaped this only because it asked
   `search_mode` first; the flag card asked nothing.
2. The replace-by-URL boxes were never gated on anything.
3. `master_browse.html` never received the `no-year` treatment its admin
   equivalent got.

The lesson matches the rule already in `CLAUDE.md`: ask what a mechanism
*serves*, and gate it on the project's declared capability rather than on
whether some value happens to be blank.

`RULE` — and the lesson from this file's own history: **an audit nobody
links to is an audit nobody reads.** All three causes above were correctly
identified on 17 August and two of them were then rediscovered from scratch
ten days later. The finding is worth less than the pointer to it.
