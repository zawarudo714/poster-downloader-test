# Not yet deployed

**v231 — the zoom shows your newest instruction too, plus one audit fix.**
- The zoom (Changes Requested AND Worker Images) now shows a flag's
  history newest first — "Your latest: …" on top — from the SAME reader
  the cards use. v230 fixed the cards and missed the zoom, which still
  printed the first flag. New preflight check "admin flag text has one
  reader" fails any admin screen that prints a flag's raw text; proven red
  on the v230 zoom line, green after.
- Audit fix: when one picture sits on two cards (for example a deletion
  card's "now holds" strip and that picture's own REPLACED card), the zoom
  used the FIRST card's buttons even if you clicked the second. Now the
  card you clicked wins. Tested both ways: the test shows the wrong
  buttons without the fix and the right ones with it.
- SEND BACK on a DELETED card no longer marks the title as flagged. The
  picture is gone, so there is nothing the worker could fix, and the red
  FLAG could never clear itself. Your note still reaches the worker as the
  ADMIN NOTE, exactly as before. Trade-off: these titles no longer appear
  under a "needs revision" filter.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
