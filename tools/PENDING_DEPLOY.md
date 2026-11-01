# Not yet deployed

## v289 — RE-UPLOAD the deleted 417, USE MY OWN PICTURE on Approve Artwork, swap removed

**The NODE needs copying too:** `worker_service/` changed (the swap stage is
gone), `AGENT_VERSION` is now **1.34.0**. Check the Nodes tab reads it.

- Listing check: "PUT YOUR DELETED LISTINGS BACK UP" panel and RE-UPLOAD
  (`POST /admin/api/listings/relist`). Only rows read as 410. Same row,
  first in the queue, plain title. New column `upload_tracking.relisted_at`
  (added by the startup migration), new row state 'relist'.
- Approve Artwork: USE MY OWN PICTURE. Every own pick is now named for
  painting (`paint_requested_at`), so Step 1 OFF paints it.
- SWAP N LIVE LISTINGS NOW removed: server endpoint, node stage, job kind.
- Three Diagnostics checks: one listing per title slot, relisted rows are
  sound, own picks painted while Step 1 is off (expect 444 — see
  OPEN_ISSUES).
- `tools/faa_delete_all.js` — for the owner to paste in his own browser.
- Tests: `tools/test_relist.py` (new), `tools/test_step2_batch.py`.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
