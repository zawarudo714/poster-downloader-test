# Not yet deployed

**v237 — nothing that follows a deletion or your look slips through
unseen, and workers can no longer change a picture that is already moving.**
- The worker's DONE now waits on Changes Requested when the title holds a
  picture you have not seen that arrived after you flagged or looked at the
  title. Examples: the new picture after a flagged one was deleted (even
  after you acknowledged or sent it back), a picture swapped or added after
  a reopen. The card shows it under "NEW PICTURE YOU HAVE NOT SEEN".
  Approving, acknowledging a deleted card that showed it, or pressing the
  keep key on Worker Images all count as seen.
- Delete, REPLACE and SKIP now refuse a picture that is greenlit, being
  painted, painted, uploading or uploaded, with the same wording the
  search-grid swap already used.
- New Diagnostics check: finished titles holding a replacement you never
  saw. On its first run it lists the ones that slipped through before this
  fix, each with a link to the picture.
- Database: one new column (saved_posters.review_voided_at), added
  automatically on startup.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
