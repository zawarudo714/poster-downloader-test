# Not yet deployed

## v238 — the Photoshop queue (2026-09-27)

- LEAVE FOR PHOTOSHOP (8) is now saved on the server as `review_status =
  'held'` instead of a browser mark. Held pictures leave the normal queue
  and its batches of 20, and wait behind a new JUST THE PHOTOSHOP ONES
  button. In that door only KEEP (9) releases anything.
- Files: `routes/pipeline_admin.py` (the `hold` decision, the held count,
  keeping/rerunning a sibling settles a held one, Photopea edits of a held
  picture stay held), `projects.py`, `diagnostics.py` (new check),
  `static/js/admin_review_images.js`, `templates/admin_review_images.html`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
