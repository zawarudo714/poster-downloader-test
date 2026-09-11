# Not yet deployed

**v192 — PAUSE NEW WORK now also holds back side jobs.**

The pause covered image batches but not the jobs queue, so a "paused"
machine could still pick up an earnings read or a listing sweep — and a
reboot at that moment would kill it mid-run. Now, while EVERY active
project is paused, only test jobs are handed out; earnings reads, listing
sweeps and manual runs stay queued and start by themselves after RESUME.
The quiet window is untouched (it closes a different gate, on purpose, so
the nightly read can run). New preflight guard row, sabotage-tested both
ways. Files: pipeline.py, routes/pipeline_api.py, admin_pipeline.html
(help text only), tools/preflight.py, config.py.

Also in v192: **the owner's running dashboard values are now the code
defaults**, so a fresh or wiped database comes up with them instead of the
old guesses. Changed: results per search 90, Google refine terms
aerial/landscape/photography, generation size 1024x1536, flatten colour
#0067c6, signature 11 wide / 50 opacity / 93.5 across, timings (element
timeout 60, form delay 0.2, between images 0.5), and the travel project
now declares its phrasing buttons, Google query "{title} {kind} view",
and description template "{title}". Live behaviour does NOT change on
deploy — existing saved settings are never overwritten; this only matters
after the reset. Selectors, upload settings and everything else on the
screenshots already matched the code.

The node was NOT changed — no worker_service copy needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
