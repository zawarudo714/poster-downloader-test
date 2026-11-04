# Not yet deployed

## v292 — the Usage tab (2026-11-04)

- A new Usage tab under JUST LOOKING: what this project's OpenAI key has
  cost, from OpenAI's own token counts on every answer. Charts and numbers
  only. New table `ai_usage` and two new `ai_batches` columns; the
  migration runs by itself at start.
- After deploy the server fills in older batches by itself, one every two
  minutes (it downloads each answer file again; downloads are free). The
  history is complete after a few hours.
- The settings save now also records which settings really changed, for the
  dashed "setting changed" lines.
- New Diagnostics check `check_usage_recorded`.
- The node (`worker_service/`) did NOT change: no node copy.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
