# Not yet deployed

## v263 — chat cannot be double-sent (2026-10-18)

- While a message is sending, SEND and Enter are locked and a faded
  "Sending…" bubble shows in the conversation (with seconds after 3s). A
  failed send says why and keeps the words in the box.
- The server drops an exact repeat (same sender, same words, same picture)
  sent within 30 seconds, for admin and worker alike.
- Files: `app/chat.py`, `app/routes/admin.py`, `app/routes/worker.py`,
  `app/static/js/chat.js`, `app/static/css/style.css`, `CLAUDE.md`,
  `TO_TEST.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
