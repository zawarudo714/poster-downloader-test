# Not yet deployed

## v209 — sort by problems, and judge the place from the zoom

Waiting to deploy. The node does NOT need copying. Version 209 (the deploy
log shows 208 live). This one bundle TWO changes to the Worker Images
screen, because neither shows as deployed — if your deploy tool says 209 is
already used, tell me and I will bump it.

1. A new order in the dropdown: "place check: problems first". It puts the
   images that need your eye at the top of the grid — the ones Google
   disagrees with first, then no-opinion, then not-checked, then the fine
   ones. Same ranking as the PLACE CHECK panel, so the two agree. The option
   is hidden while the place check is off, and the menu always shows the
   order actually in use.

2. The zoom (lightbox) now carries the same pills as the grid box — the
   source pill (Brave / Google / pasted) and the place-check verdict — and a
   "CHECKED, IT'S FINE" button. So you can open one image, arrow through the
   whole day, and clear each problem in place without going back to the grid.
   Pressing the button stays in the zoom and keeps you moving; it writes the
   same acknowledgement the grid and panel read, so all three agree.

3. The zoom also gets a CHECK GOOGLE button. It opens Google Images for this
   place in a new tab — the same search the worker's own GOOGLE button uses
   (google_query, which for travel is "{title} {kind} view", plus the
   source_search_url address), so it stays editable on the dashboard and
   matches what the worker searched. This is for eyeballing whether the
   picture is the real place and the most scenic view, without leaving the
   zoom. Built once per title in /admin/api/browse by reusing
   worker._source_search_url; empty (button hidden) when the project has no
   source link.

Design notes for a future session:

- The source pill and the place pill are now built by sourcePillNode() and
  placePillNode() in admin.js, used by the grid, the panel and the zoom — one
  spelling, so they cannot drift.
- The lightbox ack posts to the existing /admin/api/place_check/ack and
  updates the poster object in place, so no reload is needed and the grid
  behind reflects it on close.

Verified: the JavaScript parses, the template balances, the page-hook check
passes (the new ib-lb-pills and ib-lb-place-ack hooks exist on both sides),
nothing is stuck behind a hidden ancestor, and every colour name has a rule.
Run in isolation because the full preflight suite runs longer than the
sandbox allows; the deploy tool runs it in full before shipping.
