# Not yet deployed

## v161 — greenlight one-by-one selection · worker "pick another" undo

* **Greenlight titles table: individual checkboxes were dead** on uploaded
  rows, so the only way to select was SELECT ALL MATCHING. Cause: a row was
  marked selectable only when it was *greenlightable* (`pending>0 &
  complete`), but the table also offers PULL BACK, which applies to
  uploaded rows. A row is now selectable whenever it has any posters; both
  bulk endpoints already filter server-side, so ticking an ineligible row
  is a safe no-op.
* **Worker screen: no easy undo after SAVE SELECTED from the Brave grid.**
  URL projects have REPLACE as a smooth swap; in-page (grid) projects only
  had the reason-gated DELETE. Grid cards now show **"↩ PICK ANOTHER"** —
  one tap removes the pick (no reason dialog, it's a workflow correction
  not a rejection) and scrolls back to the still-populated results to
  choose again. URL projects keep the reasoned DELETE + REPLACE unchanged.

No schema change, no node copy.

**Verified**: preflight green (the scoped undefined-name checker caught a
bad variable mid-edit); both JS files parse. **NOT verified**: never
clicked — tick a few greenlight rows individually, and on a grid save press
PICK ANOTHER and confirm the results return.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
