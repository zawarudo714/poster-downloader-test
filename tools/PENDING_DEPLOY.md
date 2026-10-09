# Not yet deployed

## v257 — paste a copied picture into USE MY OWN PICTURE (2026-10-09)

- Copy an image in Google, open the zoom on Worker Images and press
  Ctrl+V (or the new PASTE PICTURE button): USE MY OWN PICTURE opens with
  the picture already in it, showing its size. Nothing is sent until USE
  THIS PICTURE (or Enter).
- The dialog takes a pasted picture on every screen that opens it.
- On today's plain-http site the button cannot read the clipboard by
  itself, so it asks for Ctrl+V. Phone pasting is not tested.
- Files: `app/static/js/admin_pick.js`, `app/static/js/admin.js`,
  `app/static/css/style.css`, `AUDIT.md`, `TO_TEST.md`, `config.py`.
- The Windows node is not affected. No copying needed.

## v256 — your own picks reach painting; two saves can no longer race (2026-10-09)

- USE MY OWN PICTURE on a title that was not already finished never sent
  the pick to painting (16 found by Diagnostics). Fixed, and at startup the
  stranded picks are sent to painting automatically (only titles where
  every live picture is your own).
- Two saves on one title at the same moment (a slow paste plus a phone
  send) could both land. Both worker save doors now ask the limit again
  after the download and offer a swap instead.
- Files: `app/pipeline.py`, `app/utils.py`, `app/main.py`,
  `app/routes/worker.py`, `tools/preflight.py`, `CLAUDE.md`, `TO_TEST.md`,
  `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
