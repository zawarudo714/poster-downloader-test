# Not yet deployed

## v268 — pictures named by hand keep their mark until they are painted (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- While step 1 is off, the site paints only the pictures you named by hand
  (RETRY, REPAINT). Each one carries a "named by hand" mark.
- Until now, the site removed that mark when it sent the picture out. If the
  picture came back unpainted — for example OpenAI refused the batch, you
  stopped the batch, a temporary error, a restart, or RELEASE — it went back
  in the queue without the mark. It was then never painted until step 1 was
  switched on, and nothing on screen said so.
- Now the mark stays while the picture is out. It is removed only when the
  picture is painted, or when its last try fails (RETRY sets it again).
  Changed in `app/gpt_worker.py` and `app/ai_batches.py`.
- New preflight check, "hand-named mark ends with the painting": it fails if
  any other code removes the mark. Tested by putting the old lines back (red).
- `tools/test_step2_batch.py` now checks the mark after a refused batch, a
  restart, a temporary error, a last failed try and a successful painting.
  It fails on the old code and passes on the new.
- To test: nothing extra to click. It matters during TO_TEST 113. If OpenAI
  refuses the batch, the 95 pictures should still show as named by hand in
  the AI panel, not join the "waiting" pile.

## v267 — two dead TeePublic jobs removed from the server's timer (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- The server's once-a-minute timer still ran two jobs that looked after
  TeePublic store listings. Their file was deleted with TeePublic in v147
  (2026-09-06), so both jobs failed and wrote an error into the server log
  every minute for seven weeks. Nothing was lost, because TeePublic is gone,
  but the noise could hide a real error. Both jobs are removed
  (`app/backups.py`).
- New preflight check, "imports point at real files": it fails when any
  code asks for a file, or a name inside a file, that no longer exists.
  Tested by putting the old lines back (red) and by misspelling a name (red).
- To test: nothing to click. After the deploy, the server log should no
  longer show "Listing-sweep retry failed" or "Stalled listing-run sweep
  failed".

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
