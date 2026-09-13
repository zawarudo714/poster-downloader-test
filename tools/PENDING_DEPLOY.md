# Not yet deployed

**v196 — deleting a flagged image no longer asks for approval; it leaves a
record instead.**

The old flow parked the flag at "awaiting admin approval" — but the file
was already deleted before the question was asked, so approving was a
keystroke with no decision in it, and the worker's screen claimed they
were waiting on something that had already happened. Now: the delete
closes the flag itself, the worker's toast says plainly they can carry
on, and the record lands in the RECENT DELETIONS panel on Changes
Requested — ACKNOWLEDGE clears it, SEND BACK reopens the matter with a
note pinned to the title. The dashboard's "pending deletions" count
drives off the same records. (That panel and its buttons already existed
from an earlier round; this reconnects them.) The verdict text all four
places match on is now ONE constant in models.py, so the writer and the
readers cannot drift apart.

One leftover to clear by hand after deploying: the Troy, Turkey flag is
still sitting at "awaiting approval" from the old flow — approve it once
on Changes Requested and it files into history. Only rows created before
this deploy need that.

Replacements are unchanged: a worker's REPLACEMENT still waits for your
judgement, because there a real decision exists — is the new image good.

Also in v196 — **Changes Requested now shows everything on the card, and
the grid swap finally answers flags properly.**

- **The real bug behind the "REPLACED pill over a deleted thumbnail"
  card:** swapping an image from the search grid stands down the old row
  and creates a NEW one, but the flag stayed pinned to the dead row —
  open, invisible to the approval flow, showing a placeholder. Now the
  swap MOVES the flag onto the new image and submits it for approval,
  exactly like the paste-replacement flow. The worker gets a toast saying
  their new image went to the admin.
- **Every pending-completion card now shows "THE TITLE NOW HOLDS"** — the
  live images, clickable to full size — so approving never needs a trip
  to Worker Images. (This replaced a per-title activity query the page
  fetched and never displayed.)
- **Recent-deletion cards show the same strip**, so "did they re-do it?"
  is answered on the card; a title with nothing live says so plainly.
- The Diagnostics check "change requests on deleted work" is back in
  authority: that state is impossible again by design, so any hit it
  reports after this deploy is real.

And two screen fixes on the same page, from the owner's look before
deploying:

- **Landscape photos were cropped to portrait slivers.** The thumbnail
  boxes on Changes Requested were portrait with crop-to-fill — right for
  movie posters, wrong for travel. The boxes are landscape now and every
  image fits inside whole, whatever its shape. A dead duplicate of the
  thumbnail rule (older copy, losing the cascade) was removed.
- **Every thumbnail on the page opens full size in a new tab** — the
  change cards, the awaiting cards, the open-flag cards and the "title
  now holds" strips — so judging never needs the Worker Images page.

Files: routes/worker.py, routes/admin.py, models.py, diagnostics.py,
admin_revisions.html, user.js, style.css, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
