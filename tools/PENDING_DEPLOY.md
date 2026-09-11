# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v187 — refine buttons REPLACE the extra word, not stack · 2026-09-11

- **/api/state now returns `google_base_query`** on the open title: the
  "{title} {kind}" part rendered, with any literal extra in the Google
  template (like the default "view") left OUT. The phone add-on's refine
  buttons rebuild the search as this base plus their one word, so tapping a
  term REPLACES "view" and replaces a previously tapped term instead of
  piling words up (owner's ask). One line of state, no behaviour change for
  the site itself.

## v187 also — two worker-page reach tweaks · 2026-09-11

- **OPEN GOOGLE and CLOSE swapped sides.** CLOSE is on the left now and OPEN
  GOOGLE IMAGE SEARCH sits at the right edge, under the thumb — that button
  being on the left was the real reach problem, not the scroll.
- **BROWSE ALL TITLES is centred** on its own line instead of packed to the
  left.

Pairs with add-on v1.8. Deploy: the server only. The NODE does not need
copying — nothing in `worker_service/` changed.
