# Not yet deployed

**v194 — Approve Artwork: reruns open on the new version, choices survive
leaving, saves that get cut short explain themselves.**

- **Bug fix: every poster with reruns opened on v1.** The versions list
  arrives oldest-first and the screen's fallback took the FIRST entry,
  while its own comment claimed it showed the newest (owner's find,
  2026-09-11: "they all start at v1", in every door). It now opens on the
  newest — the row the queue marks as current.
- **The v1/v2/v3 choice survives leaving**, stored in the browser beside
  the decisions. A generation that arrives AFTER the choice outranks it,
  so a fresh rerun always opens on its new painting.
- **Leaving mid-save asks first.** The browser's "leave site?" prompt is
  armed only while SAVE AND RELEASE is actually running.
- **A cut-short save explains itself.** Next visit shows an amber note:
  how many were released before it stopped, that the rest are waiting
  with marks kept, and that SAVE AND RELEASE finishes the job. Clears
  itself when a save completes; GOT IT dismisses it.

Files: admin_review_images.js, admin_review_images.html, style.css,
config.py. The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
