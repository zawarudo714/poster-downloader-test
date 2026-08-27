# Open issues — picked up next session

---

## PARKED until after the UI revamp

### The MUSIK master sheet is being changed

The owner intends to edit the MUSIK CSV. **Remind him about this after the
UI revamp, before the sheet is re-imported anywhere.**

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

## Migration plan — agreed 2026-08-27

Established this session, so a future one does not re-derive it:

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

### 1. A THIRD "GoldenR T" FineArtAmerica account exists, and he never made it

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
