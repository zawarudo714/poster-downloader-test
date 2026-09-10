# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v182 — the skip question · 2026-09-10

- **The skip question matches its own dialog.** It said "pick a common
  reason or type your own" while there were no reason buttons at all. It
  now says to type the reason — and because typing is the only option, the
  box is open from the start with the cursor in it, instead of hiding
  behind a TYPE OWN REASON click on every single skip.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
