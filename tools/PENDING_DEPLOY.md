# Not yet deployed

**v164 — the worker screen, the thumbnail warning, tidying up after a
choice, and the listing check answering "which ones".**

**There IS a schema change** — four new columns on `upload_tracking`
(`listing_note`, `listing_ack_status`, `listing_ack_at`, `listing_ack_by`).
They are added by `migrate_schema()` at startup, so there is no separate
step, but **back up `poster.db` before deploying**.

**No node copy.** `worker_service/` is untouched and `AGENT_VERSION` stays
at 1.31.0.

What is in it:

- SAVE SELECTED keeps its own name and re-enables itself. The waiting is
  shown by a spinner beside the saved-images count instead.
- DONE is greyed out until the title has a saved image, which is what the
  server already required.
- A "Subject: City" strip with a one-colour drawing sits in the gap between
  DONE and SEARCH. Seventeen drawings, one per kind in the catalogue,
  checked against the real CSV.
- Every trace of TMDB is gone from the code: the dead search-URL helper, the
  `tmdb_search` payload key (now `source_link`), the `.att-tmdb` class, the
  `SITE_LABELS` entry, and the worker-facing advice that named it.
- The "low resolution" warning now measures the downloaded picture and only
  fires when it is under `min_image_px` (300) on BOTH sides. New dashboard
  box on the IMAGE SEARCH settings panel.
- `get_setting` is imported once at the top of `routes/worker.py` instead of
  locally in five functions — preflight caught the fifth one.
- Approving an artwork now DELETES the files of the generations that were
  not chosen. The rows stay, marked `discarded`; the version picker will not
  offer one whose files have gone.
- The listing check names the listings a sweep never reached, and LEAVE IT
  became "I CHECKED IT — STOP ASKING": the note is stored against the answer
  it acknowledged, so a settled listing stays quiet while the marketplace
  keeps saying the same thing, and comes back by itself if that changes.
- The worker-machine log panel is always on screen, falling back to the last
  sweep, and says which it is showing.
- New Diagnostics invariant `current_image_was_discarded` — the picture
  about to be uploaded must still have its file.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
