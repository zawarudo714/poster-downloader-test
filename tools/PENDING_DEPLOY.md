# Not yet deployed

**v223 — seal the worker's side of RETIRE TITLE (small, deploy soon).**
Found while answering "can the worker interact with a retired title": in
the v222 that is live, a retired title still shows in the worker's Browse
All view, and the go-to-title door CLAIMS any unclaimed title — written
when unclaimed could only mean pending — so a worker clicking a retired
title would quietly resurrect it to in_progress. v223 closes both: retired
titles are excluded from the worker's browse, and go-to-title refuses them
with a plain sentence. Until this deploys, the hole is open but narrow —
the worker would have to browse to a retired title on purpose.
Server only. The Windows node is NOT affected.

**Also in v223 — retired titles become findable (owner's asks, 2026-09-20).**
- The Title List's "Unusable (retired)" filter now actually filters: the
  dropdown option shipped without its status being added to the server's
  whitelist, so picking it showed everything. The whitelist knows it now.
- Needs Attention gains a second card beside the artwork one: "Titles
  retired by you — final, worker paid" — title, reason, date, no buttons
  (nothing can or should be done to them). The artwork card is retitled
  "Artwork retired by you — reversible" so the two kinds of retired can
  never be confused again.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
