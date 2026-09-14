# Not yet deployed

**v199 — refused paintings can be SENT BACK TO PAINTING, and the panel
now tells the truth about when that works.**

The old panel claimed a refusal always repeats unless the source photo
changes, and offered only MARK UNUSABLE. The owner's San Francisco case
disproved the claim: the same photo painted cleanly once, and its RERUN
was refused — because the filter judged the model's OWN painting, not
the photo. So:

- Each row now says WHERE it was refused: "refused at output" (the
  model's own painting — a repaint rolls fresh dice and often passes)
  or "refused at input" (the photo itself — repainting repeats it).
  Hover the pill for the plain-words version.
- A **SEND BACK TO PAINTING** button repaints the ticked rows, with a
  confirm that names the cost. The owner decides how many tries are
  enough before MARK UNUSABLE. Works on rows already stuck from before
  the deploy — the button reads the same rows the panel shows.
- The TRIES column showed the internal give-up number "999"; it now
  reads "refused".
- Fixed on the way: requeueing a painting retry never wrote an activity
  log line (the upload retry always has). It does now.

Files: routes/pipeline_admin.py, gpt_images.py, admin_pipeline.js,
config.py. The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
