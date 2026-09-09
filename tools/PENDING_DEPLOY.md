# Not yet deployed

## v173 — a gap between upload batches, switched OFF

**Server only. The Windows machine is unchanged, so nothing needs copying.**
No new column, so no backup needed.

The owner: *"the FAA upload limit is not exactly 24 hours."* The existing cap
counts uploads on a CALENDAR day and resets at local midnight, which is the
wrong shape if FineArtAmerica's own allowance turns over some interval after
your last upload instead.

`upload_gap_enabled` (off) and `upload_gap_hours` (12), under Upload Settings.

Three decisions worth reading, because they are not obvious from the request:

  * **It gates the CLAIM of a batch, not each design.** Read literally,
    "wait N hours after the last design" means ONE design every twelve
    hours, because every upload restarts the clock. The check is asked once,
    in `claim_upload_batch`, and a batch already claimed runs to the end.
  * **It sits BESIDE the daily cap, not instead of it.** Both must pass, so
    switching it on can only ever slow an account down. Wrong in the cheap
    direction, because the expensive direction is a closed account. If it
    should REPLACE the cap, that is one line and a deliberate decision.
  * **It is derived, never stored.** Nothing is written down and nothing is
    toggled; the clock and the last upload are read fresh each time, exactly
    like the quiet window. A stored "waiting until" is a second edge somebody
    has to clear, and a lost edge leaves uploading dead looking fine.

**BOTH account panels say why.** An account in its gap shows a WAITING pill
with the time it resumes, and a note explaining that it is deliberate. There
are two separate account renderers in `admin_pipeline.js` fed by two separate
endpoints, and both were changed — a gap visible on one screen and invisible
on the other is the same account looking broken on one of them.

New invariant: `check_upload_gap_is_holding` groups an account's uploads into
runs (more than half an hour of silence starts a new one) and reports
consecutive runs closer together than the gap. It compares BATCHES rather
than designs, because uploads inside one batch are seconds apart by design.
It declines to look at all when the gap is under an hour, rather than
inventing findings from its own grouping window.

**Verified:** 11 behaviour cases on the gap arithmetic, run against the
function LIFTED OUT OF THE SHIPPED FILE — off, waiting, exactly at the
boundary, one second short, never-uploaded, zero hours, unreadable hours,
fractional hours. Preflight clean. Sabotage: removing the settings box goes
red, and the gate was confirmed to be called inside an `if` that skips the
account rather than merely mentioned.

**NOT verified:** anything against a real database or a real upload run. The
owner has said he will test this much later, and until he does, twelve hours
is his guess rather than a measurement — which is exactly why it ships off.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
