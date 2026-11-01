# Not yet deployed

## v291 — a review batch keeps its designs until it is saved

Owner, 2026-11-01: repaints must not insert themselves into the batch he is
working through; they come in the next batch, after a save.

- `app/routes/pipeline_admin.py` `api_review_queue`: new `batch` parameter
  (the open batch's title ids). In the "everything waiting" door it returns
  those titles still waiting instead of re-picking the first N; the reply
  carries `batch_title_ids`.
- `app/static/js/admin_review_images.js`: remembers the batch
  (`pd_review_batch_v1`), sends it on every reload, forgets it on a save from
  that door. The door button reads CONTINUE THIS BATCH (n).
- `tools/test_review_batch.py` (new, sabotaged). TO_TEST item 117.

Node: no change. `worker_service/` untouched.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
