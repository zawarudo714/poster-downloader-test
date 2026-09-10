# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v179 — the subject drawing on the review screens, waiting since 2026-09-10

- **Worker Images and Approve Artwork now show the KIND of place** — City,
  Castle, Waterfall — as a small chip with the same drawing the worker saw
  while choosing. Asked for by the owner so he judges a photograph with the
  same context the worker had. The seventeen drawings moved out of `user.js`
  into `app/static/js/subject_icons.js`, shared by all three screens, so
  there is one copy instead of three that could drift.
- **Seven leftover "poster" sentences fixed** (Worker Images meta line, two
  payment dialogs, the title catalogue line, two pipeline test messages, one
  help text). The terminology check was blind to them: a quote mark inside a
  `${...}` interpolation hid the whole sentence from it. The check now
  strips interpolations from the line before reading it, and was
  sabotage-tested against the exact original miss.
- **The Worker Images lightbox now shows the title's number** — the screen
  asked for `external_id` and the reply never carried it, so the number
  silently never rendered.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
