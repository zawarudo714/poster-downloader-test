# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v135 — one niche would never have uploaded** · targets
178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — nothing under
it changed, `AGENT_VERSION` stays 1.27.0. **Schema change:** none.

## The defect

`claim_upload_batch` decides which of an account's projects gets its turn.
The comment said:

> the account's turn goes to whichever of its projects has waited longest

It did not. It called `project_ids_for_account()`, whose query has **no
`ORDER BY`**, and took the FIRST project that had any work at all. SQLite
returns rows in rowid order, so in practice **the project attached first
won every single turn.**

`MEASURED 2026-08-27`, traced producer to consumer: the query is unordered,
the loop breaks on the first match, and `_has_upload_work` only asked
"is there anything?" — never "how long has it waited?".

**The consequence is total, not partial.** An account serving two projects
where the first-attached one always has work never uploads the other's work
AT ALL. The movie project carries a backlog of roughly three thousand
posters, so "always has work" is its permanent state — and one FineArtAmerica
account is meant to serve both niches after migration. Test Account is
linked to both projects right now.

**Reproduced against a real SQLite database** before changing anything: one
account, both projects, 3,000 movie rows queued two hours ago and 5 MUSIK
rows queued three weeks ago. Old logic picks movie. New logic picks MUSIK,
and once those five are done movie takes over again — no starvation in
either direction.

## The fix

`_oldest_upload_wait()` returns when the longest-waiting queued item for an
(account, project) pair was created, and the turn goes to the oldest.

Derived from the WORK rather than stored in a counter — the same choice the
quiet window and `scan_incomplete` make. There is no per-project "last
served" column to keep correct, nothing to forget to update, and nothing a
future path can leave wrong.

The comment is now true, which it was not before.

## The invariant

`check_upload_project_starved` in Diagnostics: an account serving more than
one project, where one has work queued and has sent nothing for seven days
while another has uploaded through the same account in that time.

Watches the SYMPTOM, not the cause. The picker is fixed, but anything that
makes one niche silently stop uploading through a shared account — a paused
project, a quota rule, a future rotation change — looks exactly like this.

### Files

`app/pipeline.py`, `app/diagnostics.py`, `app/config.py` (134 → 135).

**Verified:** preflight green; the starvation reproduced against a real
database and then shown fixed, including that the other project recovers
its turn afterwards.

**Not verified:** no real upload has run through the new picker. Nothing to
click — it only shows itself when the node next claims an upload batch for
an account serving two projects.
