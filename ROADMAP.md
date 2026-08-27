# The parked list — everything outstanding, in order

---

## WHERE THE AUDIT GOT TO — `MEASURED 2026-08-27`

Fifteen deploys, v124 to v138. **Stage 1 (bug fixes) is done for every code
area that was planned.**

Covered: the control enumeration and dead code · the worker screens · the
admin screens · the shared image-download path · earnings · the pipeline ·
the node.

**Not covered, and the honest remainder:** the server-side store and listing
modules (`earnings/store_health.py`, `listing_check.py`, ~1,700 lines) and
most of `routes/pipeline_admin.py` (~3,500 lines, the admin API surface).
Neither was reached. They are the natural batch 7 if the audit resumes.

### What it found

  * the phantom marketplace account, and `ensure_account` looking accounts
    up by the dead `project_id` column, which created it
  * 240 lines of dead code — including the FineArtAmerica server-side fetch
    path that FAA answers with "Verify Visitor", left sitting there looking
    usable, and `scope_titles_multi`, the worker scoping that had been
    REPLACED for causing a bug while these notes still recommended it
  * the button map traced 22 of 57 controls and reported two of them
    wrongly — `reject` as calling `approve`
  * `go_to_title` claimed titles with no permission check at all
  * `check_guards_are_called` could be satisfied by a COMMENT naming the
    guard, which affected every entry in the table
  * no protection against the server fetching its own internal addresses,
    and an allow-list that could never be switched on
  * the $6.00 ledger gap: one row we could not name, dropping out of every
    total, while the screen said rows were missing
  * one niche would never have uploaded through a shared account
  * long upload batches reaped while the node was still working on them
  * a maintenance page reading as "they redesigned the site"
  * a stranded Photoshop batch reporting only to the node's local log

### What it got WRONG — three false reports in one day

`RULE`, and the reason the evidence rule now exists: the flag-card mixing,
the "six stranded jobs", and the "silent 400 loop" were all reported as
defects and none was one. Each time a pattern was treated as a result and
the disproving evidence — one grep, in a file already open — was not looked
for. See `CLAUDE.md` "NOTHING IS A FINDING UNTIL YOU HAVE TRIED TO KILL IT".

Two of the searches that found nothing are also recorded, in
`PENDING_DEPLOY` history and here: the silent-exception sweep (172 matches,
a flood, and the detector itself was wrong) and node dependencies (sound).
An empty search reported as empty is worth more than a plausible story.

---

Agreed 2026-08-27. This is the working order and the full contents of each
stage. `OPEN_ISSUES.md` holds the detail on individual defects; this file
holds the shape of the work.

---

## The order, and why it is this order

    0. LOOSE ENDS        independent, do whenever
    1. BUG FIXES         known problems, small, low risk
       -> short test     confirms a known-good baseline
    2. QoL / STRUCTURE   what screens exist, what lives where
       -> short test     focused on whatever moved
    3. UI REVAMP         how it looks, on a settled structure
       -> short test
    4. FULL WALKTHROUGH  owner as worker and as admin, every motion
    5. FIX WHAT 4 FINDS
    6. MIGRATION         upgrade 178.105.34.144

**Bugs before structure** because a known bug fixed is a small change with a
small blast radius, and it leaves a baseline that is known to work. Make the
big changes first and every later fault has two possible causes.

**Structure before looks** because QoL decides what screens EXIST and the
revamp decides how they look. Polishing a screen that is about to be merged
or moved is work paid for twice.

**A test pass after each stage, not one at the end.** The expensive part of
a defect here has never been fixing it — it is working out which of three
recent changes caused it. One change, then look at it.

**Run `python tools/preflight.py` before every deploy in every stage.**

---

## 0. LOOSE ENDS — no dependencies, do any time

**Point the test box at its own storage root.**
One dashboard setting. Today both boxes write to the same place on `S:`,
so a test-painted image can be picked up by the main box's READ THE
STORAGE BOX step and recorded as real finished work — which would mark
those posters done and stop the pipeline ever painting them properly.
Deleting test output by hand also works, and only has to be forgotten once.

**Reboot 34.144 and take the 61 package updates.**
The container has not restarted in two months and a kernel update is
pending. Do it as its own event, days before the migration. Stacking a
reboot, a kernel change and the arrival of a whole subsystem into one
evening means a failure has three possible causes.

**Move `/tmp/poster.db` somewhere real first.** 47MB, dated 30 July,
nothing points at it — but `/tmp` is cleared on reboot and a July snapshot
of the live database is worth keeping.

**Clean up the MUSIK master sheet.**
Owner is merging duplicate artists (Chris Brown / Christopher Brown). Paste
the CSV in and it gets scanned for duplicates, name variants, punctuation
and accent differences, grouped by likely-same-artist with a confidence
note on each.

**This is safe ONLY because MUSIK has no history.** Measured: main holds
101,605 titles, the test box 201,133 — the ~99.5k difference is MUSIK, and
it exists nowhere but the test box. `external_id` is the row's position in
that sheet and it is the key for the worker's folder name, the archive
folder prefix and every upload record. Deleting rows shifts every number
below. Free now; very expensive after a few thousand uploads.

---

## 1. BUG FIXES — known, small, do first

### Earnings — real money, highest value

**The third GoldenR T FineArtAmerica account.** Appears on the Earnings
tab, never read successfully, fails with "still on the login form".
Probably a leftover from before one account could serve two projects — the
old way was to create it twice. Check before deleting: does it hold upload
records of its own, what does `account_projects` say, and does the activity
log show a person or a migration creating it. An empty duplicate is safe to
remove; one holding records is the only link between marketplace listings
and us.

**The $6.00 ledger gap on the real GoldenR T.** We hold 29 sales and 2
payouts = $273.30; FAA says $267.30. Their Current Balance is
authoritative, so ours is wrong. We hold MORE than they do, which points at
a row we kept that they removed or revised, not a row we missed.

**Refunds read `-$0.00` across every account.** 70+ sales and not one
refund ever recorded is far more likely to be a hole in the reader than a
fact about the business — and it is the obvious candidate for the $6.00.
The untested assumption is that our parser recognises a refunded or
corrected sale on FAA's Balance page at all. If it does not, this gap grows
every month.

**Earnings retry on failure.** Measured across 13 days of node logs: it is
NOT the time of day. 18:00 worked perfectly on 25 Aug and produced 2 of 12
on 26 Aug. But twice, a failed run was followed by a completely successful
one two to three hours later with nothing changed:

    23 Aug 05:00 -> 2 of 11        23 Aug 08:00 -> 13 of 13
    24 Aug 20:00 -> 2 of 11        24 Aug 22:00 -> 11 of 11

So: retry on failure, **2 hours then 4 hours, then stop until tomorrow.**
Not every-6-hours on a schedule — that quadruples the knocking on a site
that punishes knocking, on the ~80% of days when the first read works.
Not 30 minutes either; nothing in the data shows a 30-minute gap working,
because nothing tried one.

Three things it must do:

  * **Not reopen the quiet window.** A failed read already marks the day
    dealt with so work resumes. If a retry re-blocks, one failure costs
    three separate pauses.
  * **A success clears the backoff**, including a manual READ NOW.
  * **When every account fails, that is one problem, not nine.** On 26 Aug
    all nine TeePublic accounts failed. Retry the batch once, not nine
    times independently.
  * Say so on screen: "read failed at 19:04 · queued for 21:00". Queued,
    not running — the node does one thing at a time, so it starts when it
    comes free.

**Honest wording instead of "TODAY / since yesterday".** That is a calendar
word describing the gap between two readings. Miss a read and it quietly
lies. It should say what it measured:

    +$12.00  SINCE LAST READ
    2 days · 25 Aug 18:26 -> 27 Aug 08:12

And per site, because they differ: FineArtAmerica gives dated rows, so a
missed read loses nothing and the next one backfills. TeePublic gives only
a running total, so two merged days can never be separated. Say which.

"Last 7 days" needs no change — it is a window with two endpoints, so a
missing sample in the middle does not break it.

### From the node logs

**Six jobs claimed and never reported anything** — one earnings read and
three store scans on 23 Aug, two deactivations on 24 Aug. Each left work
claimed with nothing saying why. A stranded deactivation means live
listings switched off with nothing tracking them.

**Four `400 Invalid HTTP request` on `/jobs/claim`**, 23 Aug 07:50-07:52.
The node re-polled and got the same 400 each time, and nothing escalated.
A silent 400 loop is invisible from the dashboard — the node looks alive
and does nothing.

### Confirmed NOT bugs — do not spend time here

**Site speed.** Measured: the app builds the login page in 8.7ms and the
201k-row dashboard grouping takes 0.16s, while two different servers both
delivered to Kenya at ~13 KB/s and the server itself pulled at 130 MB/s.
It was the link, and it has since recovered. **CLAUDE.md planned item 9
still blames the row count and should be corrected.** The index on
`(project_id, status)` is still worth adding — 0.16s to about 0.01s, and
it removes a TEMP B-TREE — but it was never what he felt.

**Cloudflare on TeePublic.** 22 bot-protection failures, all between 20 and
23 August, none since. Genuinely gone.

**The 157 "No publish button" errors.** All on 25 Aug in a single
reactivation run — the already-fixed bug where the reactivate loop had no
wall check. One event, not a recurring fault.

---

## 2. QoL / STRUCTURE — the widest-reaching stage

### Diagnostics made actionable

Today: **240 needing attention, 82 worth a look.** Inside those 74 "files
on disk with no database record" are 1-byte `1_Sample.png` files from dev
setup, and the ten renumbered April titles that were investigated months
ago and deliberately left alone.

A count that never goes down stops being read. **The fix is not fewer
checks — it is letting a finding be settled**: acknowledge it with a
reason, it drops into a "known and accepted" list, and the headline number
comes to mean *new*.

Also from CLAUDE.md: the disk walk over 10,092 posters is genuinely slow
and belongs behind an explicit "run it" rather than a page load.

### The master-level Accounts tab

Already a stated plan. It is also the test case for the retrofit audit
below — an account is the thing that has repeatedly turned out to be
plural, or to fail in more than one way at once.

### The retrofit audit (CLAUDE.md item 10)

Belongs here rather than as its own stage, because it is the same question
this stage is already asking: what is shared, what is per-project, what
lives where. For every field and mechanism: **what will the SECOND user of
it need, and can it express that today?**

  * every column naming a single owner (`project_id`, `account_id`,
    `target_site`) — can that thing ever be plural?
  * every status field — can the thing it describes fail in more than one
    way independently?
  * every mechanism that exists once — the store sweep, the listing sweep
    and the earnings read each grew their own chunked-job machinery. Which
    parts are genuinely one shared mechanism?

The known next arrivals are the test: celebrity portraits from Pinterest,
TeePublic as an uploading project, Redbubble as a third marketplace, the
master Accounts tab. For each, say which existing pieces would be DEAD.

### Navigation and wording

The master/project split, where a shared fact is shown, and the standing
rule that every number names its scope — "17 of 1543" when the run only
covers 627 is the example that cost an evening.

---

## 3. UI REVAMP

Visual only, on a structure that has stopped moving.

**The risk in this stage is structural template edits**, which is the most
expensive class of bug in this project's history: removing a panel and
leaving one closing tag behind reparents everything below it, renders
fine, parses fine, and breaks a button on a different tab. `preflight.py`
checks tag balance and `data-` hooks; run it every time.

---

## 4. FULL WALKTHROUGH

Owner as worker and as admin, every motion. **This is the interaction audit
(CLAUDE.md item 8) done by walking rather than by writing** — which is the
better version, because a written trace inherits whatever the writer failed
to imagine.

Seed it with the seams already known to leak: the quiet window against a
store run against a scheduled earnings read; an account paused for
uploading while being read for money; a wall arriving mid-scan versus
mid-deactivation; the node going offline in each stage; a marketplace
serving maintenance to any of the three readers.

**Whatever this finds, ask the two standing questions:** what would have
caught it without you, and what mechanical check is being added so the
answer changes.

---

## 5. MIGRATION — upgrade 178.105.34.144

### What is actually there, measured 2026-08-27

    path            /root/poster-downloader   (data/poster.db, mounted)
    version         APP_VERSION "15", commit 84d9b9a
    master_titles   101,605      saved_posters  10,697
    payment_runs         15      users               3

**Every pipeline table is absent.** No projects, accounts, upload tracking,
processed images, store listings, earnings or jobs. Main is the
sourcing-only build — the entire post-production half arrives at once,
along with the master/project split, earnings and the store tools.

**That is the easy case, not the hard one.** No half-built state to
reconcile, no partial imports. Every table is created empty and filled once.

### Established rules

  * **Data never moves between the boxes.** 34.144 holds the only copy of
    the worker's posters. The test box is a copy and everything done on it
    — processing, uploads, sweeps, greenlights — is thrown away and needs
    no unwinding. Settings travel; data does not.
  * **The movie project is the default and this is already frozen in code**
    (`DEFAULT_PROJECT_SLUG = "tell-a-vision"`). All 101,605 titles have no
    project attached and resolve there. Nothing to do.
  * All FineArtAmerica accounts are deleted before importing to main, which
    disposes of the Test FAA account used for both projects.
  * TeePublic accounts are left alone — linked to no projects, so earn-only
    and unable to become upload targets.
  * The worker stays off during the upgrade.
  * `data/poster.db` is backed up first.
  * MUSIK's master list is imported fresh from the cleaned CSV.

### The dot-truncated 44 — DONE, waiting only for this step

Both halves repaired 2026-08-27: 44 of 44 files correctly named on `S:`,
44 of 44 entries corrected in `faa_upload_tracking.json` (backed up to
`faa_upload_tracking.BEFORE_dotfix_*.json`). Nothing has taken effect —
that JSON only does anything when the import runs here, against the final
database.

Afterwards: those 44 get an upload record and are never touched again. The
**other ~62 posters** in those 37 titles were painted and instantly
overwritten so never reached the marketplace — they are ordinary
unprocessed work. Because the Photoshop queue is oldest-save-date-first and
they date from 30 April to 24 May, **they will be at the very front of the
queue**, starting with 48. Kill Bill Vol. 1. Useful accident: if anything
about the renaming was wrong it shows up in the first hour.

### Then

A day or two on main adding the FAA accounts and watching the earnings
scans before trusting it unattended.

---

## AFTER MIGRATION — the stated plans, not yet started

**Celebrity portraits** — a third niche, sourced from Pinterest, its own
master sheet schema, ~2 images per title, its own FineArtAmerica account.
Remember the standing rule: a new project is a conversation, not a copy.
Say out loud which existing mechanisms would be DEAD for it before
building.

**TeePublic as an uploading project** rather than only an earning one. The
owner's old tool already contains a working uploader.

**Redbubble as a third marketplace** — needs a reader module, an entry in
`service.READERS` and a `CAPABILITIES` row. Nothing else.

**Reverse listing check** — listings live on FineArtAmerica that are not in
the database. Needs the shop's own pages paged and parsed, which is a
different mechanism from the current check and a rarer question.

**Ban recovery is built but has never been through a real ban.**

**An invariant that no upload-tracking entry points at a poster that does
not exist.** The import's orphan report already computes exactly this; it
is simply never run as a check. Turning it into a Diagnostics entry would
have caught the 44 without anyone noticing anything.
