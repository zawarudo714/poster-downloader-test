# Not yet deployed

**v201 — Photopea in an overlay, and lettered versions can be deleted.**

- **EDIT IN PHOTOPEA** sits beside HEAL SMUDGES in the zoom view. It
  opens the full editor over the page, hands it the full-size picture
  (the transparent master when there is one), and SAVE BACK files the
  result as a lettered version through the exact same door as the heal
  brush — v1 edited becomes v1b, free, same letter rules, same cleanup,
  same Diagnostics watchdog. The picture travels in and out as bytes;
  no address or cookie ever reaches the third-party page. If Photopea
  is unreachable, only this button suffers — the heal brush is the
  built-in fallback.
- **🗑 DELETE on lettered versions.** A botched heal or edit no longer
  squats on the version bar: deleting removes its row and files on the
  spot, and the spotlight returns to the version it was made from. Two
  refusals keep the record honest: paid generations can never be
  deleted here, and a version something else was edited FROM must
  outlive its children (delete v1c before v1b).
- Under the hood the heal endpoint and the new edited-upload endpoint
  now share one variant-filing helper, so the letter rules cannot drift
  between the two doors. A flattened edit of a transparent parent is
  stored as an opaque MASTER — never as a small "print file" that would
  have uploaded at editing size.

Files: routes/pipeline_admin.py, admin_review_images.js/.html,
style.css, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
