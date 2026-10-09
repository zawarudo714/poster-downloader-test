# Not yet deployed

## v252 — a KEEP button in the zoom, for checking on a phone (2026-10-09)

- Worker Images zoom: a KEEP button beside FLAG FOR CHANGES does exactly
  what the keep key does (same `toggleReviewed`, so the place check, the
  "left" counts and the grid follow). It reads "KEPT ✓ — UNDO" once kept.
  Hidden on Changes Requested, where approving is the mark.
- Files: `templates/_poster_lightbox.html`, `static/js/poster_lightbox.js`,
  `config.py`.
- The Windows node is not affected. No copying needed.

## v253 — the Google add-on can be downloaded from the site (2026-10-09)

- The admin Chrome add-on now ships inside the repo,
  `extensions/poster_admin_extension` (copied from the loose folder beside
  the repo; THIS is the master copy now). `GET /admin/extension/
  google-addon.zip` (admins only) zips it fresh on every download, inside a
  folder of its own name, ready for Chrome's Load unpacked.
- Worker Images: a ⬇ GOOGLE ADD-ON link in the top bar.
- Files: `extensions/poster_admin_extension/*` (new), `routes/admin.py`,
  `templates/admin_image_browser.html`, `CLAUDE.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
