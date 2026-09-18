# Not yet deployed

**v219 — four fixes and the K reviewed-marker (waiting to deploy).**
- `_download_to` in routes/worker.py now deletes its partial file on EVERY
  failure and refuses an empty (0-byte) download — the Ostankino orphan.
- Missing-file message: when a picture's file is gone from disk, the
  Changes Requested cards (admin) and the flag card (worker) say so plainly
  and offer the exit — DELETE THIS RECORD for the admin (existing
  endpoint), REPLACE FILE for the worker. Ends the Atlanta loop.
- Diagnostics "place check could not run" findings now link to the exact
  worker + day, one row per image, instead of the browse front door.
- NEW: `saved_posters.reviewed_at` (migration included) + K key on Worker
  Images — green outline on grid box / title box / zoom, reviewed titles
  sink on the next sort, day header counts NOT YET REVIEWED. Cosmetic by
  design: nothing gates on it; replace_poster clears it with the other
  per-picture facts.
- Server only. The Windows node is NOT affected (no `worker_service/`
  change, no AGENT_VERSION bump).

Also riding along from the 2026-09-17 audit: the `worker queue honours
priority` preflight check, the CLAUDE.md queue-priority convention, and
TO_TEST item 70. `app/` for those was byte-identical to v218.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
