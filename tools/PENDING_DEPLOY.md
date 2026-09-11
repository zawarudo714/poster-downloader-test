# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v184 — the paste helper, and one value for the phone add-on · 2026-09-11

- **The paste-a-URL box now catches a bad link before saving.** If the
  worker pastes Google's small grey preview link, or a Google page link
  instead of a picture, the box turns red and says what to do, with a SEND
  IT ANYWAY button so our guess can be overruled. This is the site-side
  half of the phone add-on idea and needs no add-on.
- **/api/state now also returns `min_image_px`.** The phone add-on reads
  the site's own too-small number from here instead of keeping a second
  copy that would drift. No behaviour change for the site itself.

The phone ADD-ON is a separate thing, delivered as a zip beside the repo
(`../poster_helper_extension/`). It is NOT deployed to the server — the
worker loads it on their own phone. It talks to this site's existing
`/api/state` and `/save_image`, which is why the only server change needed
was the one value above.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
