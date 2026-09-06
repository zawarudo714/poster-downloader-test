# THE MEGA AUDIT — 2026-09-06

One pass, seven questions, plus the owner's addition on the day: **speed**.
This file is the artefact the roadmap asks for — the tree of what was asked,
what was found, and what was NOT covered. Honesty about coverage matters
more than the appearance of completeness: a question marked "not traced" is
an instruction to a future session; a question silently skipped is a trap.

Provenance tags as everywhere: `MEASURED` / `TRACED` (code path read from
producer to consumer) / `QUESTION` (looked inconsistent, not confirmed).

---

## THE GROUND (what was enumerated before anything was judged)

193 routes across 8 route files · 28 screens · 6 job kinds
(process, upload, earnings_read, listing_check, profile_cleanup, test_*)
· 57 controls traced by `preflight --map` · ~100 settings in DEFAULTS ·
85 indexed columns + 1 compound-index list in migrations.

---

## SPEED (the owner's addition) — all fixed this pass, v153

| Finding | Fix |
|---|---|
| `TRACED` `/static` files carried NO cache header, so every page load revalidated every CSS/JS file — on the measured 12 KB/s day, each 304 still costs a round trip | `Cache-Control: public, max-age=1y, immutable`. Safe because every static URL carries `?v={APP_VERSION}`, so a deploy changes every URL |
| `TRACED` `all_settings()` ran up to TWO queries per key — ~200 round trips every time a settings screen loaded | One query loads every override; resolution order (project → global → default, empty = unset) byte-identical to `get_setting` |
| `TRACED` SQLite ran in default journal mode: one writer blocks EVERY reader, and this site is many small readers (every open tab polls the pulse every 15 s, the node polls, the GPT worker writes) | WAL mode + busy_timeout 5 s + synchronous=NORMAL, set on every connection in `db.py` |
| `TRACED` the master dashboard walked the whole WORKSPACE ON DISK on every load, for the tree panel at the bottom | The walk now runs on a LOAD TREE button, never on page load |
| **The instrument, because the one measured slowness ever was the NETWORK, not the server**: nothing could tell those apart without an evening of work | Every response now carries a `Server-Timing` header (visible in F12 → Network), and Diagnostics has a SLOWEST PAGES panel fed by the same clock: the server's own milliseconds for the worst of the last 500 requests. Slow page + small number here = the connection |

**Left alone, deliberately, with reasons:**
- The five query-in-loop warnings preflight prints: all five are bounded by
  MAX_ROWS, account count, or active-project count (currently 1). Hoisting
  them buys microseconds and risks behaviour. They stay warnings.
- The dashboard computes owed-pay per worker in a loop. With a handful of
  workers this is invisible; at fifty workers it becomes the page's cost.
  Written here so the SLOWEST PAGES panel is the tripwire, not a guess.

## Q1 · INTERACTION — what else is true while this happens

- `TRACED` **GPT worker**: claims are committed before risky work, all three
  exits report (permanent → failed + attempts=999 · transient → requeued
  with attempts+1, failing to failed at max · storage error → requeued),
  and a startup sweep frees claims stranded by a crash. Rule 8 holds.
- `TRACED` **node upload batch**: `start()`/`login()` sit inside the
  try; a Chrome that will not launch reports failure per item rather than
  stranding the batch. The historical stranding bug remains fixed.
- `TRACED` **listing sweep**: an escaped exception posts `chunk-done` with
  the error and re-raises, so the sweep ENDS and says why.
- `TRACED` **quiet window vs GPT worker**: the worker checks run state each
  cycle; the window needs no second edge. Serial node = earnings reads
  cannot collide with uploads, only delay them. Unchanged and correct.
- **FOUND AND FIXED** — `TRACED` **clearing or replace-importing the title
  list while saved images exist ORPHANED them silently.** Every screen
  reaches posters by joining through titles, so an orphan vanishes from all
  counts while its file and pay record stand. This is exactly the owner's
  own upcoming re-import. Now: both paths REFUSE, in plain words, while
  living posters point at the doomed rows; and a new Diagnostics invariant
  (`posters_without_title`) is the net under the guard. What would have
  caught it without him: nothing — hence both the guard and the net.

## Q2 · RETROFIT — the second user of every field

- The historical fixes hold and no new column repeats their shapes:
  `account_projects` (plural projects) · `earnings_paused_until` beside
  `paused_until` (two failure modes, two fields) · `listing_status` kept
  apart from `status` (their record vs ours).
- New columns added since the last audit were checked one by one:
  `master_path`, `background_color`, `has_source_link`, `openai_background`
  — none names a possibly-plural owner, none folds two failure modes into
  one value.
- `QUESTION` `SavedPoster.claimed_by` holds either a node name or the
  literal string "server" (GPT worker). Two kinds of claimant in one
  column. It works because every consumer treats it as an opaque label, and
  the reaper frees both the same way. Watch it if a claimant ever needs
  per-kind behaviour.

## Q3 · DEAD MECHANISM — screen by screen, travel-only reality

- `preflight --map`: 57 controls, all wired; the TeePublic tab and its 14
  controls are GONE rather than disabled (v147), which is this question's
  preferred answer.
- Capability gating verified present for: year/type columns, Photoshop vs
  GPT panels, review-gate tab, in-page search grid vs source link, the
  paste-a-URL box. (`AUDIT.md` remains the control-level inventory; its
  2026-08-27 verdicts were spot-checked, not re-walked.)
- **NOT re-walked as a user this pass**: the click-reachable states
  (flag card, saved-image card). The owner's walkthrough (stage 7) covers
  exactly this with real eyes on real screens.

## Q4 · SILENT FAILURE — every unreported exit

- Node-side `client.post` calls whose reply is discarded: two, both
  `chunk-done` notifications whose reply genuinely carries nothing.
- `except: continue` sites on the node: process-iteration guards
  (psutil) — correct.
- Import job failures land in `job.error` and the dialog shows them,
  including the new orphan refusal (RuntimeError → job.error, verified in
  the handler).
- The GPT worker's cycle-level catch reports through the SAME
  `report_process_failure` the node uses. One path, not two.

## Q5 · THE OUTSIDE NUMBER — checking ourselves against what we did not write

In use already: FAA's Current Balance (reconciled, and the payout rule is
re-tested on every render) · FAA's 404-vs-410 distinction · OpenAI's own
spend figure (admin key + reconcile fields).

Not yet used, written down as candidates rather than built:
- `QUESTION` Brave's rate-limit/quota headers ride along free on every
  search response and are currently discarded. Surfacing "X of 2,000 spent
  this month" on IMAGE SEARCH would cost one field.
- `QUESTION` FAA's own "daily upload limit" message, if it serves one, is
  not captured as a distinct signal — a run of upload failures near the cap
  would read as form trouble.

## Q6 · WORDS AND NUMBERS — read as a stranger

- The two known offenders were fixed in earlier passes ("17 of 1543"; two
  tabs both called REVIEW IMAGES).
- Every count on the new pulse/home surfaces names its scope in words.
- `QUESTION` The pulse strip's "workers online" uses a 5-minute rule; the
  Users table's presence label uses its own tiers. The two can disagree at
  the margin ("away" there, counted here). Not confirmed as noticed by
  anyone; unify if it ever confuses.

## Q7 · MONEY — one sale and one payment, end to end

- Sale: absolute ledger rows → matching that refuses to guess → figures as
  arithmetic at read time → Current Balance authoritative. Holds.
- Payment: `PaymentRun` freezes `rate_kes` at pay time (rate changes never
  rewrite history), denormalises the username, stores the poster list; the
  zero-daily-limit fix (0 must mean "do not upload today") is in place with
  its preflight check.
- Cost leaks checked: spend cap exists with warn/stop action · RERUN spends
  deliberately and resets attempts (owner's call) · UNUSABLE stops spend ·
  upload failure pauses the account after a run, success clears early.
- **NOT traced this pass**: the greenlight-to-payment eligibility seam
  (`payments.eligible_poster_ids` internals) beyond confirming
  `check_unpayable_but_counted` watches its known failure. One evening of
  the walkthrough (stage 7) exercises it with real money maths.

---

## THE SECOND PASS — the owner pushed back, and he was right to

"Did you look for bugs, or only speed?" The first pass leaned hardest on
old code that a month of incidents had already hardened, and went easiest
on the code built THIS WEEK — which is backwards: fresh code is where bugs
live. The second pass walked the week's own additions and found:

- **FIXED** `TRACED` — the three Pipeline doors flashed the WRONG section
  for as long as boot took: the overview panel rendered visible in the
  markup and the script hid it afterwards, so the Greenlight and Settings
  doors showed a flash of overview before snapping to their own section.
  The server knows the door; it now renders the right panel visible and the
  rest hidden.
- `TRACED` clean — the Home strip's "being worked on" link carries
  `?status=in_progress` and the Title List really does accept and pre-apply
  it (checked the route, the template's select, and the JS that reads it).
- `TRACED` clean — the funnel-click filter carry, the chat toast's
  am-I-on-the-chat-page test (admin and worker paths both), the RERUN
  requeue (resets attempts, supersedes rather than deletes), the
  claim-vs-review seams (a poster under review is in a state the processor
  never claims).
- `QUESTION`, left as a known wrinkle — the Worker Images nav badge counts
  titles awaiting review, but the screen opens on ONE worker and ONE date,
  so a badge of 4 can greet an empty screen. The empty state now says to
  try ALL DATES, which is the cheap half of a fix. If it confuses in
  practice, the badge should deep-link to an all-dates view.

## THE OWNER'S TEN MINUTES — the audit's most important section

After the second pass I reported the new code traced clean. The owner then
clicked around for ten minutes and found THIRTEEN issues: a 500 opening a
title, a button naming the wrong search engine, template placeholders shown
raw on buttons, "(N/A)" beside every place name, a colour control that
could never do anything, a stuck rerun counter, a comparison screen where
the two things being compared were different sizes, Photoshop wording on a
GPT project, and more.

**The lesson, at the right altitude, for every future session:** this
environment cannot run the site or render a page. `preflight` proves the
wiring is CONNECTED; tracing proves a path is CONSISTENT; neither proves
what a person SEES. A claim of "traced clean" must always carry the words
"never rendered" next to it — and the owner's first ten minutes of clicking
are worth more than a day of code-reading, so ASK FOR THEM EARLY, on each
feature, instead of presenting a finished-sounding audit first.

Three of his finds were data poisoning, not logic: the importer wrote the
literal text "N/A" into the year column, and every screen's correct
`year ?` guard passed on it because a non-empty string is truthy. A
producer writing junk defeats every well-written consumer — which is why
the fix is at the door (import), not another guard at each of the nine
places that render a year.

All thirteen are fixed in v154; the deploy note carries the list.

## What this audit did NOT do

Say it plainly so nobody trusts it further than it goes: it did not re-walk
every click-reachable UI state (stage 7 does); it did not re-derive any
marketplace behaviour (the measured sections stand); it read the payments
eligibility internals only at the seams. Every fix above carries either a
refusal, an invariant, or an instrument — no fix relies on somebody
remembering this file.
