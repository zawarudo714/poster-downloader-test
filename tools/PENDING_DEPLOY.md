# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v189 — review painted images in batches · 2026-09-11

- **New dashboard setting "Review batch size"** (Pipeline -> Settings ->
  Processing), default 20. When set, the REVIEW EVERYTHING WAITING button
  loads only the oldest N painted images. You inspect those N, press
  SAVE & RELEASE, and only those N go to the pipeline. Back at the start
  screen the button now reads "REVIEW NEXT 20 · 117 waiting"; press it for
  the next batch, and so on. Set to 0 to load everything at once, as before.
- **Why it reuses the existing loop rather than a new one:** releasing a
  batch removes it from the pending set, so asking for the oldest N again
  naturally returns the NEXT N — no page numbers to keep in step. Only the
  "everything waiting" flow batches; reviewing a single DATE still loads
  that whole day, and JUST THE RERUNS is unchanged.
- The review stage header now says "batch of 20 - 117 more waiting after
  this" so releasing feels like clearing a slice, not the whole pile.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
