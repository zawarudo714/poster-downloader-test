# Not yet deployed

**v203 — the Approve Artwork badge counts decisions again, one editor at
a time, and lettered versions are green.**

- **Real counting bug, confirmed:** the nav badge counted every image
  row waiting for review — and after an edit, the parent v1 AND its v1b
  both wear that mark while you choose between them. So every Photopea
  edit pushed the badge up by one although the queue of decisions was
  unchanged. The badge now counts only the CURRENT rows, the same
  spelling the review screen itself uses. The review screen's own
  numbers were never wrong — only the badge.
- **Double-clicking EDIT IN PHOTOPEA no longer boots two editors** — the
  button ignores clicks while one is opening or open.
- **Lettered versions are green in the generations bar** — v1 and v2
  purple as before, v1b and v1c green, so a paid generation and a free
  edit read differently at a glance.

Files: routes/admin.py, admin_review_images.js, style.css, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
