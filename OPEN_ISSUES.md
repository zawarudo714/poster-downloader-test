# Open issues — picked up next session

---

## THE FULL RESET TO ZERO — agreed 2026-09-09, to be done LAST

The owner's plan, in his words: the production address is reset to absolute
zero, no admin account, created from SSH. He is still tweaking the site, so
**this is the last step, after the tweaking and after the Mega Audit.**

What he stated, and what it changes:

  * **The S: drive must be EMPTY.** So the Storage Box archive is wiped too,
    not only the database. That removes the usual worry about a database
    that no longer indexes a full archive — there will be no archive.
  * **Everything from the earlier projects goes to zero.** No counts carried
    over, no leftovers to explain away.
  * **The FineArtAmerica test account is NOT the one he will use.** So the
    `Test Account 25 (avvesomedia@gmail.com)` row is disposable, and the
    "never reported any money" finding against it is expected rather than a
    defect. Do not spend time on that row.

The reset itself is `docker compose down`, `rm -rf data`, `up -d --build`,
then `scripts/create_admin.py` — the server and the IP are not touched, and
must not be, because deleting the server releases the address.

**Save `.env` first.** `PIPELINE_SECRET` is the only thing that can decrypt a
database backup, and `rm -rf data` also deletes the local backups folder.

**Before pressing anything, check nothing is live on FineArtAmerica.** A
wiped database no longer knows what was uploaded, and re-greenlighting the
same places gets them silently renamed to "Title #2" at an address nothing
computed — see the title rules in `CLAUDE.md`. He says the account is a test
account, so this is expected to be a non-issue; confirm rather than assume.

---

## REUSING AN EARNING FAA ACCOUNT FOR TRAVEL — decided 2026-09-09

The owner is rebranding an existing FineArtAmerica account rather than buying
a second premium membership. The old designs are deleted; the sales history
is not, and cannot be.

`TRACED 2026-09-09`, so a future session does not re-derive it:

  * **Old sales will NOT be mis-credited.** `matching.py` has four exact
    tiers and no similarity scoring, and anything left over stays UNMATCHED
    on purpose. The cost is NOISE on the Earnings page, not wrong data.
  * **A hard cutoff that DROPS pre-date rows breaks two things**, and both
    are worth more than the noise. `Current Balance` is FineArtAmerica's own
    figure and is the checksum on our arithmetic — gross minus payouts must
    land on it, and it never will again if our ledger is a subset of theirs.
    And `due_next` in `service.py` is `owed - (gross - back)`, which goes
    wrong the moment our gross is short; `_payout_rule_holds()` would then
    stop holding and the next-payout figure would disappear.
  * **So PARK the old rows, do not drop them.** Import everything, mark rows
    before the date as pre-travel, and have the MATCHER skip them. The money
    arithmetic stays whole, the checksum still catches a missed row, and the
    unmatched list only ever shows travel. This is the same shape as
    `listing_ack_status`: store WHAT you silenced, not the silence.
  * **The cutoff must exist BEFORE the first earnings read.** After the
    reset our database has no ledger, and the first read pulls FAA's entire
    history in one go.

**The artist name is the dangerous part of the rebrand, not the earnings.**
A listing's address is `{title-slug}-{artist-slug}`. Change the shop name
after anything is uploaded and every listing check 404s — which the sweep
correctly reads as "our address is wrong", but only after spending the
requests. Order: rebrand on FAA, then update `artist_name` on the account,
then upload. Never the other way round.

---

## THE TWO 'other' LEDGER ROWS — ANSWERED AND FIXED 2026-09-09

The word was **"Canceled Item"**, read off the owner's Balance page. Verified
against their own running balance rather than assumed: each row moves the
balance down by exactly its amount, so it reverses a sale. `classify()` now
maps anything containing "cancel" to `refund`, and asks that BEFORE the sale
test so a hypothetical "Canceled Sale" can never be filed as income.

Kept below because the reasoning is the record of how it was found.

---

## THE TWO 'other' LEDGER ROWS — the original trace

`TRACED 2026-09-09`. Nothing is lost: the reconcile passes exactly, and the
totals sum credit minus debit across every row, so the money is in the
balance. What is missing is a LABEL, which is why the rows are absent from
REFUNDED and from WHAT SOLD.

`classify()` in `earnings/faa.py` knows six words — sale, payment, payout,
refund, return, credit — and maps anything else to `other`.

The two rows, both DEBITS (they render in the payout column, not the sales
column):

  * 8/25/2026 · $6.00 · this is the SAME ROW already measured on 2026-08-27
    and written into `CLAUDE.md` — "Highlander - 1986 A - T-Shirt - Navy -
    Medium". The fix was identified then as one word and never done.
  * 9/4/2026 · $5.00 · "Gladiator - 2000 B - Jigsaw Puzzle - 20x28"

**Do not guess the word** (rule 3c-bis). The owner has been asked to read the
Type column on FAA's Balance page for those two dates. Almost certainly a
return or a refund under a word FAA uses that we do not know. One line in
`classify()` fixes both and every future one — and `raw_type` is already
stored for exactly this.

**Also worth saying to him:** the payout rule now holds on 1 observation, not
7. The old account's history is gone, so "$10.50 lands on the 15th" rests on
a single payout. The panel is honest about the count; the figure is just thin
until more payouts accumulate.

---

## TWO DEAD CONTROLS FOUND WHILE COSTING THE SPEND REMOVAL — RESOLVED v172

`TRACED 2026-09-09`, both the same shape as the `daily_limit` 0 bug — a
control that looks like protection and is not: `brave_daily_query_cap` was
read by nothing, and Brave spend was never recorded at all.

**Resolved by v172**, which removed the whole spend meter at the owner's
instruction — both dead controls went with it, and so did the live OpenAI
cap (`cap_state()`), knowingly: the discrepancy was org-wide numbers we
could not verify, and he watches spend on OpenAI's own dashboard. There is
deliberately NO spend cap in the app now. If one is ever wanted again, build
it on a number that can be checked from outside, per rule 5d.

---

## PARKED until after the UI revamp

### The MUSIK master sheet is being changed — CLOSED (MUSIK was deleted 2026-09-01)

MUSIK no longer exists, so there is no sheet to edit and nothing to remind
him about. The lesson underneath — that `external_id` is the key for
everything inside a project, so renumbering a sheet orphans the work keyed
to it — is general, applies to the TRAVEL sheet exactly as much, and lives
in CLAUDE.md. Kept here only as the original writeup:

**The thing to say to him first, because it is not obvious from outside the
code:** `external_id` is column 0 of that sheet, and inside a project it is
the key for EVERYTHING — the workspace folder name, `MasterTitle.external_id`,
the archive folder prefix, and the key in the legacy tracking file. If the
edit inserts or removes rows anywhere except the end, every row after that
point gets a different number, and the old number now points at a different
artist.

Consequences if that happens after any work exists:

  * saved posters resolve to the wrong artist
  * anything already uploaded is recorded against the wrong title
  * the archive index matches images to the wrong folder

So before the edit: ask whether rows are being ADDED AT THE END (safe) or
inserted, reordered or deleted (not safe). If not safe, the sheet needs a
stable key that is not row position, and that is a conversation before it is
a code change.

MUSIK has little or no real work yet, so the cheap moment to fix this is
now. It would be very expensive after a few thousand uploads.

---

## Migration plan — agreed 2026-08-27 — SUPERSEDED 2026-09-09 by the FULL RESET

**Nothing below is the plan any more.** The owner chose a genuine fresh
start (see "THE FULL RESET TO ZERO" at the top of this file): the production
box is wiped, nothing is migrated, and the migration tool itself was deleted
on 2026-09-01. What follows is kept only because it records which box is
which and what was on them at the time:

  * **178.105.34.144 is LIVE and holds the only copy of the worker's
    posters.** It runs v14. The worker saves there.
  * **178.105.232.196 is a TEST COPY.** Nothing on it is irreplaceable.
  * **"Migration" means upgrading the code on 34.144.** DATA NEVER MOVES
    between the boxes. Settings do; data does not. Anything done on the test
    box — processing, uploads, sweeps, greenlights — writes to a database
    that is thrown away and needs no unwinding.

Decisions taken:

  * **The test box gets its own `storage_root`** so it can never write into
    the archive that main reads. Without this, main's READ THE STORAGE BOX
    step would index test-painted images as real work and mark those posters
    processed. Deleting by hand also works and only has to be forgotten once.
  * All FineArtAmerica accounts are deleted before importing to main, which
    disposes of the Test FAA account used for both projects.
  * TeePublic accounts are left alone — they are linked to no projects, so
    they are earn-only and cannot become upload targets.
  * The worker is told to stay off during the upgrade.
  * `poster.db` on 34.144 is backed up before any schema change.
  * The MUSIK master list is NOT carried across from the test box — it is
    re-imported from the CSV on main, so the newer sheet is used. See the
    parked item above about external_id first.

### What is actually on 34.144 — MEASURED 2026-08-27

    path            /root/poster-downloader   (data/poster.db, mounted)
    version         APP_VERSION "15", commit 84d9b9a "round 15"
    container       up 2 months, never restarted
    master_titles   101,605
    saved_posters    10,697
    payment_runs        15
    users                3

**Every pipeline table is ABSENT** — no `projects`, `upload_accounts`,
`upload_tracking`, `processed_images`, `store_listings`, `earnings_*` or
`pipeline_jobs`. Main is the SOURCING-ONLY build. So this is not an upgrade
with a few new columns: the entire post-production half arrives at once,
plus the master/project split, plus earnings, plus the store tools.

**That is the best possible starting point, not the worst.** There is no
half-built pipeline state to reconcile, no partial imports, no conflicting
rows. Every pipeline table is created empty and filled once, correctly.

Consequences that follow from those numbers:

  * **The movie project MUST be the default.** All 101,605 titles and
    10,697 posters have no `project_id`, and NULL means "the default
    project". If MUSIK were default, every movie title would silently
    become a music artist.
  * **MUSIK exists only on the test box.** Main has 101,605 titles against
    the test box's 201,133 — the ~99.5k difference IS the MUSIK master
    list. So MUSIK has no history on main and its sheet is imported fresh,
    which is why the CSV cleanup is free right now.
  * **10,697 posters arrive with no pipeline status at all**, against
    7,972 recorded on 2026-07-30 — the worker has added ~2,700 since. The
    upload-tracking import is what sorts the already-uploaded from the
    genuinely new, so it must run before anything is greenlit in bulk.
  * `/tmp/poster.db` is a stale 47MB copy dated 30 July. Nothing points at
    it — docker-compose mounts `./data/poster.db`. It is a useful old
    snapshot sitting somewhere a reboot will erase.

**Do the overdue reboot and the 61 package updates as their OWN event,
days before the upgrade.** The container has not restarted in two months.
Combining a reboot, a kernel update and the arrival of an entire subsystem
in one evening means a failure has three possible causes instead of one.

---

## 2026-08-27 — the dot-truncated names are FIXED, both halves

Planned item 7 in CLAUDE.md said no tool was needed and the pipeline should
just redo the work, accepting ~44 duplicate listings. The owner chose to
repair them instead, and it worked, so **that section should be rewritten**
rather than left saying a tool would be wasted effort.

What was done, and it needed BOTH halves — this is the "two different
records" trap from CLAUDE.md turning up again:

  * **The files on `S:`** — 35 renamed by `rename_painted.py`, 2 already
    done by hand, all read back off the drive to confirm. This is what lets
    `import_processed_files` match a painted image to its poster, because
    that function matches on the filename stem.
  * **`faa_upload_tracking.json`** — all 44 keys rewritten, backed up first
    to `faa_upload_tracking.BEFORE_dotfix_*.json`. Renaming files on disk
    does NOT touch this file, and this is the half that stops the pipeline
    uploading everything a second time. Verified after: 2,077 titles and
    4,865 entries unchanged, 0 entries left without a poster number.

**The rule that made it possible, and it was read from the source rather
than guessed:** `FAA_Real_Paint_FX.jsx` walks a folder with an ascending
loop (line 84) and saves with `doc.saveAs(..., true, ...)` (line 144),
which overwrites silently. So the surviving file is the LAST one processed
— the highest-numbered poster. Both things that could break that rule were
checked and neither applies: no title here has 10+ posters (where Windows
would sort "10" before "2"), and no title has a poster saved after its
painting run.

**Two counts landed exactly on figures recorded months earlier by a
different route — 2,077 title folders on the drive, and 44 broken entries
in the JSON.** Independent agreement is the best confirmation available
that the matching rule is right.

### What is still owed

  * ~~`check_seven.py`~~ RUN 2026-08-27: all 7 correct on disk. So the
    dot-truncation is fully closed — 44 of 44 correct on the drive, 44 of
    44 correct in the tracking file.
  * The import has NOT been re-run. Per CLAUDE.md the real import happens
    against the FINAL database at migration time, so this repair is
    banked for then, not applied now.

### The lesson worth carrying

**A list handed to the owner is not the same as the set of affected rows.**
The working list had 37 titles; the JSON had 44. The other 7 were
single-poster titles he had already renamed by hand, so they had quietly
dropped off the list while their tracking entries stayed broken. Nothing
would have caught that — the 7 looked finished from the disk and nothing
compares the disk against the tracking file.

**Mechanical check that should exist:** an invariant that no entry in the
upload tracking file resolves to a poster that does not exist. That is
exactly what the import's orphan report already computes; it just is not
run as a check. Turning that into a Diagnostics entry would catch this
whole class — a tracking record pointing at nothing — for ever, instead of
the owner noticing.


Newest first. An entry stays here until it is fixed, then it moves into
CLAUDE.md as a RULE if it taught us something general, or is deleted if it
was a one-off.

---

## 2026-08-25 — TWO earnings problems, found by the evening read

Owner's instruction: finish the 37-title renaming first, then fix these.
**Remind him about this section before starting anything else that day.**

Both were found by the SCREEN, not by him reading a log — which is the
outcome rule 5e is aiming at, so the cross-checks are working. Neither is
a made-up finding; both are the far side's own numbers disagreeing with
ours.

### 1. RESOLVED 2026-08-27 — the phantom "GoldenR T" account

**Cause found and fixed in code.** `ensure_account` in
`scripts/migrate_pipeline.py` looked accounts up with

    filter_by(project_id=project.id, name=name)

— the DEAD legacy column. The real GoldenR T says which projects it serves
through `account_projects` and leaves `project_id` NULL, so it was invisible
to that lookup and the import created a second row, with an invented email
(`unknown@example.com`) because the code defaulted to one when none was
given. That phantom then became the only account linked to the movie
project, so every nightly earnings read tried to sign in as it and failed.

Two rules already in CLAUDE.md were both broken by that one function: never
scope by `UploadAccount.project_id`, and never invent an external value.

Fixed: identity is now (marketplace, email); falls back to name across all
projects and REFUSES when ambiguous; never invents an address; links through
`attach_account`.

Live data corrected the same day. Final state on the test box:

    id=1   Test Account  eltonodhis@gmail.com   artist 'White And Black'  projects [1,2]
    id=11  GoldenR T     darktitan72@gmail.com  artist 'Golden Reel'      earn-only

GoldenR T is deliberately linked to NO project — it reports money and
receives no uploads, which is right for a test box. Test Account serves both
projects, which is how the owner tests.

**What would have caught it without him: nothing.** Both rows were
individually valid, so no check that looks at one account could disagree with
anything. Now covered by two new Diagnostics checks —
`check_duplicate_accounts` (compares accounts with EACH OTHER, on email and
on name, because the two rows did not share an address) and
`check_account_never_read` (an account that has never once reported money is
a broken row, not bad weather). The duplicate check was sabotage-tested
against the exact live data and goes red on it.

### 1b. ORIGINAL NOTES — kept for the reasoning, superseded above

On the Earnings tab, under FineArtAmerica:

    Test Account   $299.28   read 8/25/2026
    GoldenR T      $267.30   read 8/25/2026
    GoldenR T      —         never read   <- this one
      "Last read failed: UploadError: Still on the login form after
       submitting — credentials look wrong or the account is locked."

The node's log shows it as job #3, sitting between two reads that both
worked, so the machine and Chrome were fine — that account's stored
password simply does not sign in.

**The likely cause, and it should be checked BEFORE deleting anything.**
CLAUDE.md records that before the `account_projects` link table existed,
the only way to make one FAA account serve two niches was to CREATE IT
TWICE. This has exactly that signature: same marketplace, same display
name, one row carrying real history and one that has never successfully
read anything. So this is probably a leftover of that era that the link
table made redundant — not something new that appeared today.

Check, in this order:

1. Does it have any `UploadTracking`, sales or ledger rows of its own, or
   is it empty? An empty duplicate is safe to remove; one with history is
   not.
2. What does `account_projects` say for both rows? If the surviving one
   already links to both projects, the duplicate has no job left to do.
3. Was it created by a migration step rather than by him? The Activity Log
   should say.

**Do not simply delete it.** A `UploadAccount` row is what connects
listings on the marketplace back to us. If it turns out to hold the movie
project's uploads while the other holds MUSIK's, deleting it loses the
only record of which listings are whose.

**The invariant that is missing, and it is the real lesson here.** Nothing
watches for two accounts on the same marketplace with the same name, and
nothing watches for an account that has NEVER been read successfully since
it was created. Either check would have surfaced this the day it appeared
instead of the day he happened to look. Both belong in `diagnostics.py`:

  * `check_duplicate_marketplace_accounts` — same site + same login or
    same display name, more than one row.
  * `check_account_never_read` — an account enabled for earnings whose
    every read has failed since creation. Distinct from "read failed
    recently", which is normal and already covered.

### 2. GoldenR T's ledger is $6.00 short — rows are missing

The screen says it plainly:

    We hold 29 sale(s) and 2 payout(s), which come to $273.30.
    They say $267.30 — a difference of $6.00.

FAA's `Current Balance` is authoritative (CLAUDE.md, FineArtAmerica
section: the Balance page is a running ledger and its own checksum). So
OUR figure is the wrong one, and the totals shown above it on that page
are wrong by the same amount.

Note the direction: **we hold MORE than they say.** That is not "we missed
a row" — it is more likely a row we hold that they have since removed or
corrected, e.g. a refunded sale still counted as a sale, or a sale row
that was revised downward within the 48-hour window the page already warns
about.

Where to start:

1. Re-read the account (READ NOW) and see whether the gap survives. The
   page already tells him to try this first.
2. If it survives, compare our 29 sale rows against the Balance page line
   by line for that account. $6.00 is small enough that one row will
   explain it.
3. Check whether the refund path writes anything. `REFUNDED` showed
   `-$0.00` across all accounts, which is suspicious on its own — 70+
   sales and not one refund ever recorded is more likely a gap in the
   reader than a fact about the business.

**Untested assumption to name explicitly:** that a refunded or corrected
sale on FAA's Balance page is something our parser recognises at all. If
it is not, this $6.00 is the first visible symptom of a whole category we
have never stored, and it will drift further every month.

### What went RIGHT, worth not breaking

  * Every TeePublic account hit the wall and every one got through, first
    path, first attempt — 9 accounts, ~20 seconds each.
  * The FAA reads paged correctly and the ledger balance matched on the
    two healthy accounts.
  * The failing account did not stop the run; jobs #4 onward carried on.

---

## 2026-08-25 — "The site is slow" was the WIRE, not the code

Measured, so that nobody spends an evening on indexes that fix nothing:

    login page built by the app          8.7 ms
    dashboard group-by over 201,133 rows 0.16 s
    plain COUNT(*)                       0.00 s
    server pulling from Hetzner's mirror  130 MB/s
    laptop pulling from THIS server       12.6 KB/s
    laptop pulling from the OLD server    16.3 KB/s

Two different servers in two different places, equally slow to the same
laptop within a minute of each other. So the link out to Kenya was the
problem that day, not the promotion, not the 201,133 titles, not the app.

**CLAUDE.md planned item 9 still claims the slowness is the row count.
That claim is now measured and wrong — correct it.** The index on
`(project_id, status)` is still worth having (0.16s → ~0.01s, and it
removes a TEMP B-TREE), but it was never the cause of anything he felt.

Done in response: `GZipMiddleware` added in `app/main.py`, APP_VERSION
122 → 123. Not yet deployed or verified at time of writing.
