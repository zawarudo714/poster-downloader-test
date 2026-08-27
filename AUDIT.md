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

**All three root causes from the 17 August pass are FIXED**, and both
entries it left open are now closed — one fixed, one verified as
deliberate. Checking the second turned up a fault the 17 August pass did
not look for, because it walked CONTROLS and this one is in what a control
does after you press it. See "Image downloading" below.

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
| Review Posters · "Paste URL to add one" | OK — verified, see below |
| Upload · title template help text | **FIXED 2026-08-27** — gated on capability |

### FIXED · Upload title template help text

`MEASURED 2026-08-27` — it listed `{year}` and `{content_type}` for every
project, and both render EMPTY where `has_year` / `has_content_type` are
off. It was inviting the owner to build a listing template with a hole in
it. Now assembled from the project's declared capabilities, like every other
control on that screen.

### VERIFIED · Review Posters "Paste URL to add one"

`MEASURED 2026-08-27` — the control exists. The 17 August note was right;
it is simply named `.g-add-url` / `.g-add-btn` in `admin.js`, posting to
`/admin/poster/add`, which is why a search for the label found nothing.

`DECIDED 2026-08-17, still stands` — kept for MUSIK. Unlike the worker's
paste box it is not a dead end: the in-page grid is worker-only, so this is
the admin's only way to add an image directly.

**But checking it turned up something the 17 August pass did not** — see
the image-download section below.

## Image downloading — what happens AFTER you press the button

`MEASURED 2026-08-27`. Four controls end in the server fetching a URL a
person typed: the worker's SAVE box, the worker's two REPLACE boxes, and
the admin's "+ ADD". All four went through one validator, and it checked
the scheme, the host being non-empty, and the file extension.

**It did not check where the host pointed.** `http://127.0.0.1:8000/x.jpg`
or `http://169.254.169.254/meta.jpg` would make the server fetch its own
admin API, or the cloud provider's credentials endpoint, and write the
result into the workspace as a poster.

There WAS a control for this and it could never be switched on:

  * `RESTRICT_HOSTS` was an ENVIRONMENT VARIABLE, so invisible from the
    dashboard — against the standing rule that anything the owner might
    change lives on a screen.
  * It defaulted to off and `MEASURED 2026-08-27` had always been off.
  * Its allow-list held TMDB only, so switching it on would have blocked
    every MUSIK save — those images come from wherever Brave found them.

`CLAUDE.md` names "allowed hosts" explicitly in the list of things that must
resolve per project. This was a constant in `config.py`.

**Fixed two ways, because there are two different questions here:**

  * Internal, private, loopback and link-local addresses are refused
    ALWAYS, for every project, with no setting. This can never refuse a
    legitimate image, because none is served from such an address. The
    hostname is RESOLVED, not pattern-matched — `sneaky.example.com` can
    point at 127.0.0.1 and a string check waves it through.
  * The allow-list is now `allowed_image_hosts`, a per-project dashboard
    setting. **Blank by default, which is exactly today's behaviour**, so
    nothing changes until someone chooses to narrow it.

`RULE` — and the reason this was missed on 17 August: that pass walked
CONTROLS and asked "does this do anything in this project?". This fault is
not in a control, it is in what four controls share once pressed. **A
control audit and a path audit are different audits.** When a screen's
buttons all funnel into one function, that function needs its own look.

**Three answers, not two.** The first version of the fix treated "could not
resolve that name" the same as "that name is internal" — both refuse, so
the behaviour was right, but the message said "on this server's own
network", which is false for a DNS hiccup and would send someone hunting a
security problem. Same shape as FAA's 410 versus 404. Found because the
sandbox it was written in has no DNS, so the first test run rejected every
legitimate host.

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
