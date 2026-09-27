# Not yet deployed

## v240 — Worker Images: untouched titles first (2026-09-27)

- "Unreviewed" now means neither reviewed nor flagged, as the owner meant
  it. Titles sort untouched → flagged → reviewed (except in the "flagged
  first" order). The day count, NEXT DAY TO REVIEW and the day the page
  opens on use the same definition (`pictureOwed()` in admin.js, and the
  per-day count in `browse_page`).
- Files: `static/js/admin.js`, `routes/admin.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
