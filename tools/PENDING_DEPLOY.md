# Not yet deployed

## v213 — cleaner flag card for workers, and the strip shows the last worker action

Waiting to deploy. The node does NOT need copying. Version 213 (live is 212).

Two changes, both the owner's ask (2026-09-15):

1. The worker's Changes Requested card is decluttered.
   - DELETE FILE is gone: deleting happens in the saved-images panel (FIND A
     REPLACEMENT or GO TO TITLE takes the worker there), and a delete there
     answers the flag with a record exactly the same way.
   - The big SEND FOR APPROVAL button is gone from the row. Its one unique
     ability — answering a flag with "no change needed" — survives as a
     small text link under the card: "Nothing to change? Send it back to
     admin with a note." Option (b), chosen by the owner.
   - On a phone, REPLACE FILE now sits beside the paste box on the same row
     (the box's 200px minimum width was what pushed the button down).
   - The link is removed on similar-pair cards (where it was never wired)
     and on deleted-image cards (the deletion already answered the flag).

2. The status strip gains one entry: the newest activity-log action made by
   a WORKER (never the admin's own), in plain words with month-day and time,
   for example "humphrey saved Cortina 1.jpg · 09-15 09:16". One entry
   across all workers; clicking it opens the full activity log. It rides in
   the existing /admin/api/pulse poll — no new timer, per that file's own
   rule.

A defect found and fixed while working here (5e, honestly): when a worker
deleted a flagged image, their card was supposed to show a placeholder thumb
and "admin is reviewing the deletion" — but the code removed the container
and then wrote the note into the node it had just detached, so the worker
saw a bare card with nothing to read. Nothing mechanical could have caught
it (the hook existed, the JS parsed; the write went to a detached element,
which only a rendered page shows). Found by reading the function while
changing its neighbours; fixed so the thumb stays and the note appears.

## v213 also — two fixes from the audit of v195–v212

The audit walked every change since 2026-09-11 against its neighbours and
found two real defects, both at the seam where one mechanism changes a
thing another mechanism remembers. Both are fixed in this version:

1. STALE PLACE CHECK AFTER A PASTE-REPLACE. The replace flow swaps the
   picture on the SAME row — the one place a row's file changes in place —
   and the place check was built believing that never happens. So after a
   worker replaced a flagged image by URL, the card kept showing Google's
   verdict (and the fingerprint) for the OLD picture. The replace flow now
   clears every place-check field plus content_hash and re-runs the check
   on the new picture, the same way it already voided the old pipeline
   verdict. The place-check module's own header claimed the file was
   immutable; that stale claim is corrected too.

2. THE ADMIN + ADD BOX SKIPPED THE 350px FLOOR. "No save-anyway on any
   front" now genuinely covers every front: the admin's own add-by-URL
   door was the fourth door and the only one without the size gate. It now
   refuses a picture under 350 on any side with the same shared test.

Also fixed while in there (found reading, not reported): when a worker
deleted a flagged image, their card was meant to show a placeholder and
"admin is reviewing the deletion", but the code wrote the note into a
container it had just removed, so the worker saw a bare card. The note and
placeholder now actually show.

Verified: both JS files parse, worker.py/admin.py/place_check.py compile
with no undefined names, calls pass the right arguments, local imports come
before use, guards are called, templates balance, hooks exist, every button
has a handler, colour names have rules, and helpers are in scope. The full
preflight suite exceeds the sandbox time limit; the deploy tool runs it in
full before shipping.
