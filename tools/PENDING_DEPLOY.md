# Not yet deployed

**v193 — the Approve Artwork screen remembers your place, and the counter
is a jump box.**

The "23 / 25" counter is now a box you can type in: enter a design number,
press Enter (or click away), and you land on that design. The screen also
remembers the last design you looked at — by WHICH design it was, not its
position number, because positions shift as work is released — so leaving
and coming back resumes there instead of at design 1. If that design was
already released or is outside the loaded batch, it starts at the
beginning and nothing is lost. A small message says when it resumed.
Files: admin_review_images.js, admin_review_images.html, style.css,
config.py.

The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
