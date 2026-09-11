# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v191 — audit of everything since v175 · 2026-09-11

The real find, fixed on two rungs:

- **A number setting could hold text, and twenty pages trusted it not to.**
  The settings door checked the KEY but not the VALUE, while readers like
  the worker's whole state call and the review start screen read numbers
  with a bare `int(...)`. One stored "abc" would have 500'd them. Now the
  save REFUSES a non-number into a number box, with a plain message
  ("review_batch_size needs a whole number..."), and a new Diagnostics
  check, "number settings hold numbers", watches for garbage stored before
  the door existed. The two most exposed readers also degrade to their
  defaults instead of failing, as a belt.

Smaller repairs and cleanings from the same walk:

- The two copies of the batch-size read in the review endpoints became ONE
  helper, so they cannot drift.
- The three screens using the shared subject drawings no longer die
  mid-render if that one static file ever fails to load — the chip goes
  blank instead.
- Dead CSS from the one-row action bar rewrite deleted (.skip-row /
  .done-row rules that styled nothing).
- A duplicate write of the review button's count removed; the relabel is
  the one owner of that label now.
- The skip-acknowledge endpoint's signature tidied to house style.

Checked and found CLEAN, for the record: the paste helper's SEND IT ANYWAY
survives every confirm round-trip; the grid save alerts the new too-small
refusal loudly; batching correctly skips date-range and rerun reviews; the
advance gate cannot trap the reviewer; skip-ack, pulse and the Skipped page
share one unread rule; the Brave ranking behaviour test still passes all
cases; every diagnostics check is registered (42).

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
