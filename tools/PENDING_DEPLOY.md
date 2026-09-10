# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v180 — captions on the search results, waiting since 2026-09-10

- **Every tile in the worker's search grid now has a caption** — the title
  of the page the picture came from, in the ordinary reading font, under
  the image with the pixel size in small beneath. Two lines at most,
  ending in "…", so a long page title cannot stretch its tile. Asked for
  by the owner. The words were already being sent to the page (the ranking
  reads them); only the drawing of the tile changed.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
