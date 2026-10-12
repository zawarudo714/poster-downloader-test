# Not yet deployed

## v260 — number pad 3 pastes in the Worker Images zoom (2026-10-12)

- In the zoom, the paste key (3 by default, a setting beside KEEP on the
  Pipeline settings) does what Ctrl+V does.
- On today's plain-http site a key cannot read the clipboard, so until the
  one-time Chrome setting is on, 3 opens the box and asks for Ctrl+V.
- Files: `app/pipeline.py`, `app/routes/admin.py`, `app/static/js/admin.js`,
  `app/static/js/admin_pick.js`, `app/static/js/admin_pipeline.js`,
  `TO_TEST.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
