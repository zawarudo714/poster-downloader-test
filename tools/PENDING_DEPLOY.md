# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v190 — the Review batch size box now SHOWS (it was hidden) · 2026-09-11

- **"Review batch size" moved from the Photoshop Settings panel to the
  Image Generation panel**, next to "Review images before upload". The
  Photoshop panel only renders for a Photoshop project, so on Travel (which
  is GPT) the box existed but was never on screen — the owner could not
  see it (his find). The setting itself, the queue batching and the button
  relabel were already correct in v189; only the box was in the wrong panel.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
