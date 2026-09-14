# Not yet deployed

**v204 — SAVE AND RELEASE can no longer approve what you never saw.**

The real cause of the unpermitted uploads (owner's find, 2026-09-14):
the release approves every UNMARKED image in the loaded batch, and a
mid-sitting reload — a Photopea save, a version delete, a page refresh
— re-fetches the queue, letting a rerun that finished painting slip
into the batch behind your position. Your silence then approved
repaints you never looked at, and the node uploaded them.

Now the screen records which titles were actually RENDERED in front of
you, and silence only approves those. An image that arrived unseen is
not sent at all and keeps waiting for the next batch. The tally reads,
for example: "18 will be released · 1 rerun · 0 retired · 2 arrived
unseen, staying", and the release confirmation says the same before
you press it. Explicit marks always count, seen or restored.

The repaints already uploaded this way are identifiable as today's
uploads whose filename carries _v2 or higher — recall any you dislike
via the Title List's recall panel.

Files: admin_review_images.js, CLAUDE.md rule, config.py.
The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
