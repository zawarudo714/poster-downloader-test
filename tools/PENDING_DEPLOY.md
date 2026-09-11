# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v186 — Google refine words for the phone add-on · 2026-09-11

- **New dashboard setting `google_refine_terms`**, a box on Pipeline ->
  Settings -> Image Search called "Google extra-term buttons (phone
  add-on)". One word or phrase per line. It is SEPARATE from the Brave
  "Extra phrasing buttons" on purpose — the owner did not want the two
  tangled. Default: aerial / skyline / street / at night / old town.
- **/api/state now returns `google_refine_terms`** as a list, read live, so
  the add-on shows one button per word with no second copy anywhere.

The phone ADD-ON (v1.6, zip beside the repo) turns each word into a button
on Google that re-runs the search with that word added. Each button is one
real page load — the same thing the worker would do by typing — so it
does not look automated to Google. It never fires a burst.

## v186 also — the worker action row is one line now · 2026-09-11

- **SKIP and DONE sit on one row with the reason box**, sized like the
  SEARCH button: left to right it reads reason box, SKIP, a small gap, then
  DONE on the far right. The old stacked skip-row / done-row layout is gone.
- **The "note for the admin" box under DONE is removed.** A worker has never
  written one; a bad image is skipped, not noted. A plain DONE now carries
  no comment. The under-target reason prompt still appears when a title is
  finished with fewer images than the target — that is the one completion
  that genuinely needs a word.
- The point of the shorter row: the panel is less tall, so the auto-scroll
  on opening a title leaves the Google button lower and easier to reach on
  a phone. If it still sits high, the auto-scroll target can be nudged
  separately.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
