# Not yet deployed

**v198 — Worker Images gets the same memory as Approve Artwork, plus a
sweep of the "shows one thing, means another" family.**

- **Worker Images order picker, remembered:** by sheet number (default),
  newest saved first, or flagged first. This also FIXES a quiet ordering
  fault: the server sorts folder names as text, so title "10." came
  before "2." — the numeric sort now shows the sheet order anyone would
  expect.
- **Worker Images walks back in:** leave the page with the enlarged view
  open, and the next visit reopens on that exact image. Close the
  enlarged view on purpose, and the next visit opens the plain gallery.
  (Worker, date and position were already remembered; the enlarged view
  now is too.)
- **The crop sweep** — four more places showed travel photos through a
  movie-poster-shaped crop, same defect as the Changes Requested cards:
  the worker's flag-card thumbnail, the Peek page's flag thumbnail, the
  worker catalogue's saved-image thumbnail, and the style-reference
  preview (which cropped the very file it exists to show). All four now
  show the whole image.

Files: admin.js, admin_image_browser.html, style.css, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
