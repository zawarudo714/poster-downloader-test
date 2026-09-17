# Not yet deployed

**v218 — queue priority for the worker (waiting to deploy).**
- New column `master_titles.queue_priority` (default 0), added by
  `schema_migrations.py`. Additive and nullable-safe: every existing row is 0,
  so today's order is unchanged.
- `pull_next()` in `routes/worker.py` now orders by `queue_priority DESC` then
  `external_id ASC`. Lets late-added rows be worked early without renumbering.
- Purpose: the 146 famous landmarks added on the end of the sheet (Eiffel
  Tower, Colosseum…) get a high priority so the worker picks them up next.
- Server only. The Windows node is NOT affected (no `worker_service/` change,
  no AGENT_VERSION bump).

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
