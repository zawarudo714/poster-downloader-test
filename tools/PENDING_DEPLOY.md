# Not yet deployed

## v270 — RESET on the GPT prompt really resets it; site-wide settings save where they are read (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- RESET next to the GPT prompt said it put the starting prompt back. It
  did not: it cleared an empty site-wide copy and left your own prompt in
  place. Now it removes your prompt for this project, after asking you
  first. The removed prompt is kept in the activity log.
- Some settings have one value for the whole site: claim timeout, the
  schedule, the upload gap, the earnings reader times, the listing check.
  The Settings page saved them for the current project only, where the
  program never read them. So a change there did nothing. Now they are
  saved for the whole site. The live data had no such hidden values, so
  nothing you set was lost.
- Two settings were read without the project: the upload retry limit and
  the storage folder. They now read the project's value.
- Two new deploy checks: every RESET says which level it resets, and a
  setting read without a project must be a site-wide one.

## v269 — RELEASE and RETRY no longer re-send a picture that is out at OpenAI (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- A picture sent to OpenAI in a batch can take up to 24 hours. On the
  In-flight list it looked stuck, and it had a RELEASE button. Pressing
  RELEASE, or RETRY on Needs Attention, sent it again. You paid twice, and
  the first painting was thrown away.
- Now RELEASE and RETRY leave such a picture alone. RETRY also only takes
  pictures that are not being painted.
- On the In-flight list, a batch picture now says "at OpenAI" instead of
  showing a RELEASE button. The help text says to STOP its batch on the AI
  panel to take it back.
- New Diagnostics check: "Every picture in an open batch is still held by
  it". It warns if a picture leaves a batch that OpenAI is still painting.
- `tools/test_step2_batch.py` presses both buttons on a batch picture. It
  fails on the old code and passes on the new.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
