# Not yet deployed

**v202 — Photopea on the card too, and the heal brush is REMOVED.**

- 🖌 EDIT IN PHOTOPEA now sits on the normal review card as well as in
  the zoom, so editing never requires zooming first.
- The v200 heal brush is gone at the owner's word — Photopea does its
  whole job better, and a tool nobody will use is a control that only
  confuses. Removed entirely, not hidden: the brush UI, its canvas, the
  /api/review/heal endpoint, and the opencv requirement (the container
  slims back down on rebuild). What SURVIVES of it is the
  lettered-version machinery underneath — v1b/v1c, the DELETE button,
  the provenance watchdog and the rerun-numbering fix — because that is
  what Photopea saves through.

Files: routes/pipeline_admin.py, admin_review_images.js/.html,
style.css, requirements.txt, comment updates in models.py,
schema_migrations.py, gpt_worker.py, diagnostics.py, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
