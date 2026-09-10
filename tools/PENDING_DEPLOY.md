# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v183 — a skip stops waiting on you once you have read it · 2026-09-10

- **The WAITING ON YOU card no longer counts skips for ever.** The Skipped
  page has a new button per row: I'VE READ IT — LEAVE IT SKIPPED. Pressing
  it moves the row to an ALREADY READ section and the home-page card stops
  counting it. SEND BACK still works from both sections, and ASK ME AGAIN
  undoes the mark. If a worker ever skips the SAME title again, it returns
  to the waiting list on its own — the mark remembers WHICH skip you read,
  not "never show this".
- Under the surface: two new date columns on titles (`skipped_at`,
  `skip_acked_at`), added by the automatic startup migration — no manual
  database step. The "is this skip unread" rule is written ONCE and used by
  both the count and the page, so they cannot disagree.
- The year column on the Skipped page now renders inline and only when a
  year exists, instead of an always-empty column.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
