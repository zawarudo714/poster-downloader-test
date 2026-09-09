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

## THE 500'S TRUE CAUSE — and the check that was blind to it (v156)

The "Failed to open title: 500" resisted code-reading because the code
LOOKED right and `preflight` was green. The cause: `search_text` was
imported inside one function and used bare in three others — a NameError
on every title open since v146. The undefined-name check pooled bound
names FILE-WIDE, so that one local import vouched for the whole file; and
v155 then shipped the identical shape (`full` used in a route whose
sibling declared it), which blanked the poster pane.

The check is rewritten to per-function scope, was run over the entire
codebase (it found the second live NameError on the allow-list path too),
and was sabotage-tested with the exact shipped bug: red with it, green
without. Rule 5e answered properly this time: what found it was the owner,
twice; what finds it now is preflight, before deploy, every time.

## What the FIRST audit did NOT do

Say it plainly so nobody trusts it further than it goes: it did not re-walk
every click-reachable UI state (stage 7 does); it did not re-derive any
marketplace behaviour (the measured sections stand); it read the payments
eligibility internals only at the seams. Every fix above carries either a
refusal, an invariant, or an instrument — no fix relies on somebody
remembering this file.

---

# THE SECOND MEGA AUDIT — 2026-09-09 (v175)

The owner said he was done tweaking and asked for a second full pass. The
first audit (above) hardened the code as it stood on 2026-09-06. Everything
from v155 to v174 was built AFTER it, and fresh code is where bugs live, so
this pass walked the new mechanisms first: the upload gap, the failure-
evidence pruning, the chosen-vs-painted colour, the generations picker, the
recall button, and the whole "N/A" removal of v174.

Same rule as always: nothing is a finding until it has been killed. Where
this sandbox could not run the thing, that is said next to the claim rather
than hidden.

## THE ONE REAL BUG — the reset would have crashed on its first import

`MEASURED 2026-09-09` — reproduced in sqlite, fixed, and the fix reproduced.

**What would have happened.** The owner's next big step is ROADMAP stage 6:
deploy, wipe the database, and re-import the 88,970 travel titles. That
import would have died on its very first batch with "NOT NULL constraint
failed: master_titles.year", and the reset would have looked broken.

**Why.** v174 made the `year` column nullable in `models.py`, so a travel
place with no year stores nothing. That was the right fix. But
`create_all()` never ALTERs a table that already exists, and the reset
deletes ROWS rather than dropping the table, so every box built before v174
still carries the old `year VARCHAR(16) NOT NULL` column. The re-import
writes `year=None`, and the old column refuses it. The model said one thing
and the live table said another.

**The second half, which is worse.** There is a `RELAX_NOT_NULL` mechanism
in `schema_migrations.py` that rebuilds a table to drop a NOT NULL. But
`year` was not listed in it — and even if it had been, its rebuild only
knew how to strip NOT NULL from an `INTEGER` column. `year` is a `VARCHAR`,
so it would have rebuilt the table into an IDENTICAL still-NOT-NULL copy and
reported "(now nullable)" falsely, on every single boot, for ever.

**The fix (v175).**
- `("master_titles", "year")` added to `RELAX_NOT_NULL`.
- The rebuild now matches the column's OWN type token, whatever it is, so it
  relaxes a VARCHAR, an INTEGER or anything else. Proven in sqlite: the old
  INTEGER case still works, the VARCHAR case now works, and a NULL year
  inserts after the rebuild where it was refused before.
- It now RAISES if it cannot actually change the definition, rather than
  rebuilding an identical table and lying. A shape it cannot handle becomes
  a loud boot failure, not a silent forever-loop.

**5e — what would have caught this without him.** Nothing pure-source could:
"the live table is still NOT NULL" is a fact about a running database, and
this sandbox has neither the database nor SQLAlchemy. The real net already
exists in the plan and had simply not been run yet — ROADMAP stage 6 says to
rehearse the whole deploy-wipe-reimport on the TEST box before touching
production. **Do that rehearsal.** It is the one thing that exercises this
path end to end, and it is cheap on the box that does not matter.

## THE SEVEN QUESTIONS ON THE NEW CODE — traced, no other bug found

- `TRACED` **Q1 upload gap.** `upload_gap_state` is derived, off by default,
  reads the clock and the last confirmed upload, and gates the CLAIM not each
  design (so it cannot become one-upload-per-gap). It sits BESIDE the daily
  cap and the quiet window — all three must pass — so switching it on can
  only slow uploading, never speed it. A zero gap means off, the same
  `daily_limit` lesson that a typed zero must be harmless. A never-uploaded
  account is not parked. Clean.
- `TRACED` **Q4 failure-evidence pruning.** Keeps the newest N per KIND
  folder, and screenshots and page-dumps live in separate folders — so
  keep=30 is 30 errors and 60 files, which is exactly what the owner asked
  for ("30 as in 60 when paired up"). Runs after the file is safely written,
  swallows its own errors so housekeeping can never fail an upload report,
  and 0 means keep everything. Clean.
- `TRACED` **Q7 money, the rebrand.** Previous-business rows (before
  `earnings_start_date`) are imported and stored as ordinary `sale` rows and
  still counted in gross, so FAA's Current Balance still reconciles — the
  checksum in CLAUDE.md holds. They are excluded ONLY from per-design
  attribution and from the unmatched work queue, which is the honest split:
  the gap between gross and per-design IS the old business, shown rather than
  hidden. The refund `classify()` tests "cancel" before "sale". Clean.
- `TRACED` **Q4 silent registration.** Every one of the 36 Diagnostics
  checks is wired into the `CHECKS` list, including the two added this week —
  a check defined but never registered would run never and say nothing, and
  none is. `check_year_is_a_year_or_nothing` was exercised against sqlite and
  flags "N/A", "20xx" and five-digit junk while passing NULL, "" and a real
  four-digit year.
- `TRACED` **Q2/Q3 new columns and vocabulary.** Columns added since the
  last audit — the signature fields, `background_chosen`, the upload-gap
  settings, `earnings_start_date`, `failure_evidence_keep` — none names a
  possibly-plural owner, and `background_chosen`/`background_color` and
  `signature_json`/`signature_applied` are the deliberate "two facts because
  something compares them" shape, not accidental duplication. No deleted-
  project word (tmdb, movie, MUSIK, TeePublic) survives in executable code;
  every remaining mention is a history note in a comment or docstring, which
  CLAUDE.md wants kept.

## STILL OPEN, deliberately — not bugs, owner's own backlog

- Twelve settings still have no box on any screen (`pay_rate_kes`,
  `allowed_image_hosts` and ten more). Preflight WARNS on them and FAILS on
  any new one. This is the known 2026-09-03 backlog and it is UI-revamp work
  the owner owns, not an audit fix.
- Five query-in-loop warnings, all bounded by MAX_ROWS or the active-project
  count. Unchanged from the first audit; the SLOWEST PAGES panel is the
  tripwire.

## What this SECOND pass did NOT do

The same honest limits as the first, plus one that matters more now: this
sandbox cannot run the app or a database, so every "clean" above is a claim
about a CONSISTENT code path, never about a rendered screen or a real query.
The first audit's own most valuable section was the owner's ten minutes of
clicking, which found thirteen things code-reading missed. So the true test
of everything here is stage 6 (the reset rehearsal on the test box) and
stage 7 (the click-through on production). This pass found the schema bug
that would have blocked stage 6 from even starting.

# THE THIRD MEGA AUDIT — 2026-09-09 (rides in v175)

The owner asked for a pass "from scratch, fixing inconsistencies". The
second pass (above, same day) had walked the NEW code; this one walked the
WHOLE system by machine wherever a machine could walk it, and by hand at the
seams where old code meets new. Method first, findings after, because the
method is what a fourth pass should reuse.

## HOW IT WAS WALKED (reusable)

- **Contract sweeps, two lists at a time.** Every endpoint the node calls vs
  every route the server defines: 16/16, both directions, no orphan on
  either side. Every job kind created vs every kind the node handles vs the
  words the status strip can say: all match, and the strip spells out an
  unknown kind rather than dropping it. Node imports vs
  `requirements.txt` vs `REQUIRED_MODULES`: 3/3/3. Every fetch in the JS and
  templates vs the 203 defined routes: all resolve.
- **Dead-code sweep by syntax tree.** Every module-level function with zero
  references anywhere in the repo.
- **Docs read as instructions.** Every command a document tells a person to
  run, checked against the files that exist today.

## FOUND AND FIXED — code

- **The reset script had no opinion on five tables** (`LedgerEntry`,
  `MarketplaceSnapshot`, `ListingSweep`, `TitleAlias`, `SearchCache`), all
  added after it was written. A "reset to zero" would have opened with the
  TEST shop's money still on the Earnings tab. All five are wiped now, a
  `KEPT_ON_PURPOSE` list names each surviving table with its reason, and
  `check_reset_covers_every_table` in preflight fails the deploy for any
  future table that joins neither list. Sabotage-tested both ways.
- **Nothing ever enforced the folded-title uniqueness rule** that the FAA
  section of CLAUDE.md calls "not optional". The sheet was deduplicated on
  the RAW string; "Los Angeles" and "Los Ángeles" fold to one FAA title and
  would be silently renumbered, stranding the listing at an address the
  checker can never compute. Two doors, two guards now:
  `check_titles_collide_after_folding` in Diagnostics (unattended, watches
  the whole catalogue), and a refusal in the retitle endpoint (the one place
  a duplicate can be typed in by hand after import). The fold grouping was
  exercised against the shipped fold code, lifted from the file by syntax
  tree — accent pairs collide, distinct names do not.
- **`allowed_download_hosts` was a setting read by nothing**, sitting beside
  the real `allowed_image_hosts` — the `brave_daily_query_cap` shape again.
  Deleted, and `check_every_default_is_read` in preflight now fails on any
  key referenced nowhere outside its own DEFAULTS line. Sabotage-tested by
  putting the key back: two checks go red.
- **Four dead functions.** `_has_upload_work` (superseded by the inline
  condition at the claim site), `default_x_pct` (its docstring claimed the
  default "cannot drift" while the default was in fact stored as literals
  and this deriver was unreachable), `_snippet` in `faa.py` (a leftover of
  the deleted server-side fetch path, in the very file whose header promises
  none remains) — all three deleted by syntax tree, one cut at a time.
  `_account_names` in diagnostics was the opposite case: a helper whose
  intended consumer never called it, so `upload_no_processed` findings read
  "account #3". It is now called, and that finding shows the account's name.

## FOUND AND FIXED — documents that gave instructions the code cannot follow

- `DEPLOY.md` §1e and `SETUP_VPS.md` both told the reader to run
  `scripts/migrate_pipeline.py`, deleted 2026-09-01 — one of them in the
  exact runbook the production promotion follows. Both now say the truth:
  startup migrates, there is no command.
- `PIPELINE.md` carried the movie era as if current (TMDB in the stage
  table, the legacy import as a live procedure, "celebrity — planned
  next"). A dated banner now says which sections are design (current) and
  which are history, and the wrong instructions are corrected in place.
- `MULTIPROJECT.md` opened with "today there are two — movie and MUSIK".
  It now states the travel-only reality and why ONE project is the
  dangerous number.
- `CLAUDE.md` cited three deleted mechanisms in the present tense
  (`check_count_check_is_running`, `_park_runs`/`finish_run`, the
  `PRICE_PER_MTOK` table). Corrected to past tense with the rule kept.
- `OPEN_ISSUES.md`: the 2026-08-27 migration plan marked SUPERSEDED by the
  full reset; the MUSIK-sheet parked item CLOSED (MUSIK is gone); the two
  dead spend controls marked RESOLVED by v172.

## JUDGED AND LEFT ALONE

- Recall deletes a poster's upload rows along with its paintings — the seam
  I went in expecting to be broken, and it is handled.
- The three upload gates (daily cap, quiet window, upload gap) — traced in
  the second pass, unchanged.
- Eleven settings still have no box (the known 2026-09-03 backlog), and the
  four bounded query-in-loop warnings. Owner's backlog, warned by preflight.

## WHAT THIS PASS DID NOT DO

Same limit as ever, said plainly: no database and no SQLAlchemy in this
environment, so the new Diagnostics check has had its LOGIC exercised but
has never run against real rows, and every "clean" above is about code
paths, not rendered screens. The proof of the reset work is still ROADMAP
stage 6 — the deploy-wipe-reimport rehearsal on the test box — which now
also proves the five newly wiped tables and prints the year-column
relaxation line.
