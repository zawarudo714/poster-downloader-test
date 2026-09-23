# Not yet deployed

**v225 — Changes Requested gets the shared zoom (waiting to deploy).**
- The zoom overlay moved out of admin.js into poster_lightbox.js +
  _poster_lightbox.html, shared by Worker Images and Changes Requested.
  Worker Images behaves exactly as before; anything odd in its zoom
  (arrows, K, flag, retire, place-ack, CHECK GOOGLE) is this change.
- Changes Requested: clicking any thumbnail opens that zoom in place
  (no more new tab); arrows walk the page; K works and rings the thumb.
- Approving a fix — single APPROVE or APPROVE COMPLETION — now stamps
  those pictures as reviewed (the K mark), narrowly: only the pictures
  the approval covered, live ones, first look keeps its date.
- Preflight: the hook check now reads {% include %}'d markup, and a new
  check "the shared zoom ships with its markup" fails any page loading
  poster_lightbox.js without _poster_lightbox.html (both sabotage-tested).
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
