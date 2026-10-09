# Not yet deployed

## v251 — keep settles the place check; "left" counts; folded PAID list; picture fetch can no longer hang (2026-10-09)

- Worker Images: the keep key (K mark) also acknowledges the place check,
  stamped with the same instant; un-keeping takes back only an ack the keep
  made. `admin_toggle_reviewed` now returns `place_acked`.
- "2 / 100 (61 left)" beside the title counter, and "(31 left)" beside the
  zoom's position — titles / pictures neither kept nor flagged.
- PAID band: the lists fold into one line with coloured counts; a new
  purple kind "your look" (was drawn red like an open flag).
- USE MY OWN PICTURE: the dialog counts the seconds and stops waiting after
  120 s, refreshing the title; the server gives up on a slow website after
  60 s in total (`imagefetch.TOTAL_S`, read in 8 KB pieces); the activity
  log records how long the download and the whole save took.
- Files: `app/imagefetch.py`, `routes/admin.py`, `static/js/admin.js`,
  `static/js/admin_pick.js`, `static/js/poster_lightbox.js`,
  `static/css/style.css`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
