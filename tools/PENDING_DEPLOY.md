# Not yet deployed

**v200 — the heal brush: brush smudges out of a painting for free.**

On the Approve Artwork ZOOM view there is now a HEAL SMUDGES button.
Click or drag over the smudges (brush sizes S/M/L), UNDO STROKE steps
back one gesture at a time, CLEAR wipes the sitting, Escape cancels.
APPLY computes the fill-in on the Linux server — no OpenAI call, no
money — and the result arrives as a LETTERED version: healing v1 makes
v1b, sorted inside its family (v1 · v1b · v2). Healing v1b again makes
v1c. Nothing is overwritten, so a heal that smudged a detail is undone
by picking the parent version back. When a version is approved, the
files of every version nobody chose are deleted by the existing sweep,
so no useless files pile up. New Diagnostics check: every healed
version must trace to its parent.

Also fixed while building: a rerun numbered itself by COUNTING rows, so
after v1 and v1b the next paid generation would have been called v3
with no v2 existing. It now takes the highest number plus one.

**THE CONTAINER MUST REBUILD for the healing maths** — the deploy's
normal `docker compose up -d --build` does this by itself; the first
press of APPLY proves it. If it ever says the healing library is
missing, the build did not pick up requirements.txt.

Files: models.py, schema_migrations.py (two new nullable columns, added
automatically at startup), routes/pipeline_admin.py, gpt_worker.py,
gpt_images-adjacent requirements.txt, diagnostics.py,
admin_review_images.js/.html, style.css, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
