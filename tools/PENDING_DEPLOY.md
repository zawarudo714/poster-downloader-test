# Not yet deployed

## v246 — a redo after you rejected or deleted comes back to you (2026-09-29)

- `pictures_awaiting_your_look` now counts a picture that arrives after ANY
  flagged picture was taken off the title (the admin's delete too, not only
  the worker's), and after a rejected completion (new column
  `master_titles.completion_rejected_at`, set by REJECT & SEND BACK and
  back-filled at startup from the activity log). Atlanta finished without
  the owner's look because of this.
- The Diagnostics check for finished titles now picks its candidates from
  the same marks, so it lists Atlanta.
- Files: `app/utils.py`, `app/models.py`, `app/schema_migrations.py`,
  `app/main.py`, `routes/admin.py`, `app/diagnostics.py`.
- The Windows node is not affected. No copying needed.

## v247 — a title is paid once, and a redo waits for your look (2026-09-29)

- New `payments.unpayable_reasons`: the ONE answer to "why can this picture
  not be paid now" — already paid, its title already paid (limit is the
  project's images_per_title; travel 1), an open flag, or a replacement
  the owner has not looked at (`utils.awaiting_your_look_by_title`, the
  bulk form of the look gate). The payment run, the older-unpaid-days
  list, the Worker Images PAID band and the worker's own history all ask
  it now.
- The worker's history used its own copy of the pay rules; it now starts
  from `payable_criteria` like the payment run.
- PAID band: a grey "not paid again" list for replacements on paid titles.
- New Diagnostics check `check_titles_are_paid_once`, reading the payment
  runs themselves (runs from 2026-09-29 onwards).
- Files: `app/payments.py`, `app/utils.py`, `app/diagnostics.py`,
  `routes/admin.py`, `routes/worker.py`, `static/js/admin.js`,
  `static/js/user_history.js`, `static/css/style.css`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
