# Not yet deployed

## v266 — step 2 (the flatten), the Step 1 switch, OpenAI batches, the 417 swap

Written 2026-10-28. **Deploy with the pipeline still PAUSED.** Then follow
TO_TEST.md item 113.

- Step 2: every new painting is flattened with the owner's step 2 prompt
  (gpt-image-2-2026-04-21, medium, opaque) and Approve Artwork shows that.
  REPAINT (7) and REDO STEP 2 (6) on Approve Artwork; no colour controls on
  step-2 pictures.
- Step 1 switch: travel starts with step 1 OFF; only pictures named by hand
  (RETRY, REPAINT) are painted until it is switched on.
- Batches: both steps go to OpenAI's Batch API (half price, within 24 h).
  AI PAINTING panel on Pipeline shows progress and STOP.
- One-click catch-up: SEND N PAINTED PICTURES TO STEP 2.
- The uploader now only sends a row whose picture is current and approved.
- The 417: SWAP N LIVE LISTINGS NOW queues a `listing_swap` job on the
  worker machine.
- Four new Diagnostics checks; new tables ai_batches / ai_batch_items and
  five new columns (added automatically at startup).

**The worker machine needs its folder copied** (AGENT_VERSION 1.31.0 →
1.32.0): `worker_service/agent.py` and the new `listing_swap.py`. No new
pip packages. Only needed before pressing SWAP; everything else works
without it.
