# Not yet deployed

**v163 — every generation kept · Approve Artwork rework · no more auto-jump
to Needs Attention · a log for the listing check.**

No schema change beyond one new value in an existing column
(`processed_images.review_status` can now read `superseded`), so nothing to
migrate. **No node copy** — `worker_service/` is untouched and
`AGENT_VERSION` stays at 1.31.0.

What is in it:

- `pipeline.storage_path_for()` puts the generation number in the filename
  from attempt 2 onwards (`..._v2.jpg`). Attempt 1 keeps the plain name, so
  nothing already in the archive has to be renamed. This is what makes a
  rerun stop writing over the picture it claimed to be keeping.
- `gpt_worker.process_one()` decides the attempt number BEFORE building the
  path, and passes it in.
- The review queue returns every generation of each poster, and the review
  screen lets you pick between them. Approving one moves `is_current` to it.
- `.review-img img` no longer carries `background: #111`, which was painting
  over the colour plate and is why the colour preview only worked in zoom.
- Keyboard shortcuts on Approve Artwork, and the colour controls repeated
  inside the big view.
- The Pipeline page no longer remembers the last section you opened.
- The Listing check tab shows the worker machine's own job log, its
  heartbeat, and which machine has the work.
- New preflight check `check_no_background_on_composited_img`, sabotage
  tested against the original bug.
- New Diagnostics invariant `generations_share_a_file`.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
