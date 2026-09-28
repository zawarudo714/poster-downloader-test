# Not yet deployed

## v245 — put Photopea back when it slides sideways (2026-09-28)

- Photopea often opened pushed about 300px left, hiding its tools. After the
  picture is handed in, the page checks at 0.5, 2, 5, 10 and 20 seconds
  whether Photopea's own page has scrolled sideways, scrolls it back, and
  says by how much. A FIX EDITOR VIEW button does the same on demand.
  Measured in a test frame; the cause on his machine is still a LEAD until
  the message confirms it.
- Files: `static/js/admin_review_images.js`, `templates/admin_review_images.html`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
