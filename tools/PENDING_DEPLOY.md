# Not yet deployed

## v175 — the second Mega Audit, and the one bug it found

The owner asked for a full audit from scratch now that he is done tweaking.
The pass walked everything built since the last audit. The artefact is the
second half of `MEGA_AUDIT.md`. Seven questions, and only one real bug.

### The bug — the reset would have crashed on its first import

Your next big step is to wipe the database and re-import the 88,970 travel
titles. That import would have failed on its very first batch with "NOT NULL
constraint failed: master_titles.year".

The reason is that v174 made the `year` column nullable in the model, which
was right, but a database that already exists keeps its old column. The old
column says "a year is required", the re-import writes no year for a place,
and the two disagree. Neither startup nor the reset changes an existing
column, so nothing had fixed it.

### The fix

- `master_titles.year` is now in the list of columns the startup migration
  relaxes from "required" to "optional".
- That migration used to only know how to relax whole-number columns. The
  `year` column holds text, so it is widened to relax a column of ANY type.
- It now fails loudly if it ever meets a column it cannot relax, instead of
  quietly rebuilding an identical table and claiming success.

Reproduced in sqlite three ways: the old whole-number case still works, the
text case now works, and a missing year inserts fine after the migration
where it was refused before.

### What this means for you, in order

1. **Deploy v175.**
2. On the TEST box, rehearse the whole thing before production: deploy, then
   `reset_workflow.py --dry-run --wipe-titles`, then the real
   `--yes --wipe-titles`, then import `IMPORT_titles.csv`. The import should
   now finish with no error. This is ROADMAP stage 6, and it is the one test
   that proves the reset path end to end.
3. The startup will print a line like `master_titles.year (now nullable)` the
   first time it runs on a box that still had the old column. That is the fix
   doing its job. On every boot after that it says nothing.

### Also riding in v175 — the THIRD audit pass (same day, owner's request)

A from-scratch walk over the whole system. Full record in `MEGA_AUDIT.md`;
what changes behaviour:

- **The reset now wipes five more tables** it previously left behind —
  earnings rows, marketplace snapshots, listing sweeps, sale-name aliases,
  and the search cache. Without this, a "reset to zero" opened with the
  TEST shop's money still showing on the Earnings tab. **This means a reset
  now clears the Earnings tab too.** The money is not lost: the next
  earnings read pulls the full history back off FAA's own Balance page.
- **Two titles that FAA would fold into ONE name are now caught**: a new
  Diagnostics check scans the whole catalogue ("Los Ángeles" vs
  "Los Angeles"), and retitling a held upload refuses a name the account
  already lists — FAA would silently rename it "#2" and the listing checker
  could never find it again.
- **A dead setting was deleted** (`allowed_download_hosts` — read by
  nothing; the real one is `allowed_image_hosts`), three dead functions
  were removed, and one findings list now shows account NAMES instead of
  "account #3".
- **Three new preflight checks**, each sabotage-tested red: every table must
  be wiped-or-kept-on-purpose by the reset; every setting must be read by
  something; (plus the v174 magic-word check now guards the seed data too).
- **Documents that gave dead instructions were corrected** — DEPLOY.md and
  SETUP_VPS.md both still told you to run a script deleted on 2026-09-01,
  and PIPELINE.md described the movie era as current.

### Nothing else changed

The Windows node did NOT change, so there is nothing to copy and
`AGENT_VERSION` stays where it is. No new setting, no new screen.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
