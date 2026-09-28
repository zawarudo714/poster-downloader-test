# Not yet deployed

## v242 — warn before replacing a flagged title; blue for "saved after paid" (2026-09-28)

- USE MY OWN PICTURE asks first when the title has an open flag (or a fix
  waiting for approval), quoting the flag note. Changes Requested skips the
  question, because the flag is the card the button is on.
- Worker Images' PAID band: "saved after this day was paid" is now blue,
  apart from amber "waiting for the next payment" and red "flag still open".
- Files: `routes/admin.py`, `static/js/admin_pick.js`,
  `static/js/admin_revisions.js`, `static/js/admin.js`, `static/css/style.css`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
