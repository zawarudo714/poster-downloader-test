# Not yet deployed

**v226 — zoom decisions + the stale flag-panel fix (waiting to deploy).**
- Changes Requested: the zoom now carries the CARD's own buttons (APPROVE /
  REJECT, ACKNOWLEDGE / SEND BACK, CLEAR FLAG — whatever that card offers)
  plus a verdict box, so no more closing the zoom to decide. The buttons
  are remote controls for the card's real buttons — one action path.
- The K shortcut is OFF on Changes Requested (approving stamps the mark
  itself; K still works on Worker Images).
- Worker Images: fixed the long-standing "previous flag comment shows as
  the latest" — a slow server answer re-opened the zoom on the OLD picture.
  Now a late answer refreshes the zoom only if you are still on that
  picture. Same guard on CLEAR FLAG and the place-check tick.
- The "click does nothing after closing the zoom" report could NOT be
  reproduced (the exact shipped code passes open→close→reopen in
  simulation). If it happens again on v226: F12 → Console → send the red
  line + which page.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
