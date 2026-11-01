# Not yet deployed

## v290 — USE MY OWN PICTURE on a live title; NEXT NOT MARKED (*)

**The node does NOT need copying for v290.** Nothing in `worker_service/`
changed. (The v289 node copy, which brings AGENT_VERSION 1.34.0, is still
outstanding if it has not been done.)

- **USE MY OWN PICTURE works on a title still live on FineArtAmerica**, for
  example Hamburg. Before this it refused with "is in the pipeline or
  already on the marketplace". The live listing's record moves onto the new
  picture unchanged, so nothing changes on FineArtAmerica. When the listing
  is deleted and RE-UPLOAD is pressed, the NEW picture goes up. It is
  refused only while a picture is being painted or uploaded, or when the
  title has two listings on one account. (`routes/admin.py`
  `_pick_cannot_replace` and `admin_pick_title`; `admin_pick.js`.)
- **NEXT NOT MARKED (*) on Approve Artwork.** A button and the `*` key go
  to the next design that still has a picture with no mark.
  (`admin_review_images.js` and `.html`.)

Tested: `tools/test_relist.py` (new live-title section, sabotaged four
ways), `tools/test_next_unmarked.js` (new, sabotaged), plus
test_step2_batch, test_upload_receipts and test_attention_count, and
preflight. Not clicked on the real site. See TO_TEST.md item 116.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
