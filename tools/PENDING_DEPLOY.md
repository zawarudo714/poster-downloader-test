# Not yet deployed

## v261 — PAY AHEAD (2026-10-12)

- MARK PAID has a new tick box: PAY AHEAD. It covers every picture on the
  days, flagged and not-yet-looked-at ones included (one per title), so a
  later fix or replacement is never paid again.
- A paid-ahead picture later removed without pay is offered as a deduction
  in the next MARK PAID box (untick to let it go); settled once.
- Payday now also sends to painting earlier-paid pictures that have passed
  their checks since (a fixed paid-ahead picture, an approved replacement
  on a paid title). Flagged ones still wait.
- New Diagnostics check: paid-ahead pictures removed without pay.
- One-off repair, run after deploy: `scripts/convert_run_to_pay_ahead.py`
  (preview first, `--apply` to do it).
- Database: two new columns on payment_runs, added automatically at start.
- Files: `app/models.py`, `app/schema_migrations.py`, `app/payments.py`,
  `app/pipeline.py`, `app/routes/admin.py`, `app/diagnostics.py`,
  `app/templates/admin_payments.html`, `app/static/js/admin_payments.js`,
  `scripts/convert_run_to_pay_ahead.py`, `CLAUDE.md`, `OPEN_ISSUES.md`,
  `TO_TEST.md`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
