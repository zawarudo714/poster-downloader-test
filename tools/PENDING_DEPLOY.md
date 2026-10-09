# Not yet deployed

## v254 — the two position counters say what they count (2026-10-09)

- Worker Images: "title 1 / 100 (91 left)" under the gallery and
  "image 1 / 102 (93 left)" in the zoom. They were bare numbers and read
  as a disagreement; one counts titles, the other pictures.
- Files: `static/js/admin.js`, `static/js/poster_lightbox.js`, `CLAUDE.md`,
  `config.py`.
- The Windows node is not affected. No copying needed.

## v255 — the paste box can no longer put a second picture on a title (2026-10-09)

- The worker's paste-a-link box used to ask "Save another?" when the title
  already had its picture, and saved a second one on OK. It now asks
  "Replace it with this one?" and swaps, exactly like the search grid. A
  picture already being painted or uploaded cannot be swapped.
- Any open flag on the old picture moves to the new one and goes to the
  admin for approval, the same as the grid swap.
- Diagnostics: "Titles holding more pictures than they take" now also lists
  titles still being worked on, not only finished ones.
- Preflight: a new guard fails any new worker save door that does not ask
  the shared picture limit.
- Files: `app/routes/worker.py`, `app/static/js/user.js`, `app/diagnostics.py`,
  `tools/preflight.py`, `CLAUDE.md`, `TO_TEST.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
