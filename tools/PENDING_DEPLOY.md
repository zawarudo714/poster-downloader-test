# Not yet deployed

**v197 — Approve Artwork: a remembered queue order, and coming back walks
straight back in, zoom and all.**

- **Order picker on the review start screen** — oldest saved first (the
  old behaviour, still the default), freshest painted first, or by sheet
  number. The choice is remembered in the browser and applies to every
  door: the big button, a single day, a range, the reruns. Batches take
  the first N of the chosen order.
- **An interrupted session resumes by itself.** If you leave the page
  mid-review without pressing CLOSE, opening Approve Artwork again goes
  straight back in: same door, same design, and the zoom compare view
  reopened if it was open. Pressing CLOSE, or finishing a save, clears
  that — after either, the page opens on the picker as it always did.
  That split is what keeps the memory honest: it only repeats a sitting
  YOU left unfinished, never a state something else put you in.

Files: routes/pipeline_admin.py, admin_review_images.js,
admin_review_images.html, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
