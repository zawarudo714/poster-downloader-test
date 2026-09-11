# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v188 — Approve Artwork will not step past a poster still loading · 2026-09-11

- **The NEXT / arrow step is held until the painted poster of the title on
  screen has loaded.** The worker photograph loads in a blink and the poster
  lags, so a fast second press used to swap the fast half and carry the
  reviewer past a title whose poster they never saw (owner's find). Now a
  second press while the poster is loading is ignored, and a small amber
  "waiting for the poster to load..." note shows why.
- Deciding (KEEP / RERUN / UNUSABLE) is NOT affected — only stepping
  between titles waits.
- The lock ALWAYS clears: on load, on a broken image, or after a 4-second
  safety timeout, so a poster that never arrives can never trap the
  reviewer.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
