# Not yet deployed

## v258 — the worker's TODAY and WEEK count only his own work (2026-10-09)

- Your USE MY OWN PICTURE picks no longer count as the worker's saves on
  the dashboard (TODAY, WEEK) or on his own screen. His redos still count.
- Those days now start at midnight Kenya time, not 03:00.
- Pay is not affected; it never counted your picks.
- Files: `app/utils.py`, `app/timeutil.py`, `tools/preflight.py`,
  `CLAUDE.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
