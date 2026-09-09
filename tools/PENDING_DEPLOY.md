# Not yet deployed

## v169 — the recall panel: the 500 on COUNT, and a checkbox list

**Server only. The Windows node is unchanged, so nothing needs copying.**

* **Fixed the 500 on the count button.** `_recall_targets` called
  `P.project_scope()` with a query as its first argument. That function takes
  a project id and returns a filter condition, so the call raised TypeError
  on every press. It now goes through `_title_scope()`, the helper already at
  the top of the same file that every other endpoint there uses.
* **The recall panel now reads the Title Browser's ticked rows** instead of a
  box of typed numbers, and it has moved to sit directly below that browser.
  The owner asked for the checkbox list on 2026-09-09.
* **COUNT THEM FIRST is now SHOW ME WHAT THIS WOULD DELETE**, and the panel
  shows how many titles are ticked at all times, so the button has a visible
  subject.
* **SEND BACK TO THE START is also in the Title Browser's sticky bulk bar**,
  beside GREENLIGHT SELECTED and PULL BACK SELECTED. Both buttons call one
  function, so there is one destructive path with two ways in.

New mechanical checks:

* `check_call_arity` in `preflight.py` — a call into our own code with the
  wrong number of arguments. Sabotage-tested in three directions: too many
  positional, a misspelt keyword, too few. It would have caught this 500
  before deploy.
* `check_recalled_poster_still_painted` in `diagnostics.py` — a poster that is
  back at the start while painted versions survive.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
