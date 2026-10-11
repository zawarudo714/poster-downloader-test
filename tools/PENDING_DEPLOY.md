# Not yet deployed

## v259 — the pay rate box saves (2026-10-11)

- SAVE SETTINGS on Payments wrote the rate where nothing read it, so it
  snapped back to 5. It now saves to the setting the payments actually use.
- The old store now refuses that key outright, and a hidden fallback that
  said 10 KES is gone.
- Files: `app/payments.py`, `app/routes/admin.py`,
  `app/templates/admin_payments.html`, `tools/preflight.py`, `CLAUDE.md`,
  `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
