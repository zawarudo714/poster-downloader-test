# Not yet deployed

## v250 — no reload after USE MY OWN PICTURE; quick note buttons (2026-10-09)

- Worker Images: USE MY OWN PICTURE now re-reads the day and swaps in only
  that title, keeping the order, the scroll and the zoom (which shows the
  new picture). It used to run the full reload. `admin.js`
  `refreshTitleInPlace`.
- Shared zoom (Worker Images and Changes Requested): QUICK NOTE buttons in
  the title bar, one per line of the new setting `flag_quick_notes`
  (default "generic" and "camera angle", box in the SEARCH group of the
  Settings page). A click types the note into the comment box; it does not
  flag. New endpoint `/admin/api/quick_notes`.
- Files: `app/pipeline.py`, `routes/admin.py`, `static/js/admin.js`,
  `static/js/poster_lightbox.js`, `static/js/admin_pipeline.js`,
  `templates/_poster_lightbox.html`, `static/css/style.css`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
