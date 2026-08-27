# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it. It is a
SEPARATE file from `DEPLOY_LOG.md` because the deploy tool rewrites that one
from scratch every time, so anything hand-written there is destroyed on the
next deploy.

---

**Waiting: v126 — the earnings retry** · targets 178.105.232.196 (test box)

  * `app/earnings/service.py`
      * `retry_unread_if_due()` — re-queues accounts today's scheduled read
        did not get. The GAP between tries is the account's own cooldown (3h
        general, 12h signed-out), NOT a second timer, because a separate
        timer would have been silently swallowed by that cooldown.
      * `accounts_unread_since()` / `daily_run_started()` — measured from the
        run, not the calendar day, so it behaves the same at 23:50 and 00:10.
      * `retry_state()` — what the page needs to SAY while it waits.
      * `summary()` now reports `covers_days` and `last_read_at`.
  * `app/backups.py` — scheduler tick calls the retry when the daily run did
    not just fire.
  * `app/pipeline.py` — two new declared settings:
    `earnings_retry_window_hours` (8, dashboard-editable, 0 switches it off)
    and `earnings_daily_run_started_at`.
  * `app/diagnostics.py` — `check_earnings_retry_gave_up`, because a retry
    that gives up quietly looks exactly like a day with no sales.
  * `app/static/js/admin_earnings.js` — the card no longer says
    "TODAY / since yesterday", and the page now states when the figures were
    last actually refreshed.
  * `app/config.py` — APP_VERSION 125 → 126

**After deploying, the thing to look at** is the Earnings page: the first
card should read LAST 24 HOURS, and underneath there should be a line saying
when the last successful read was.
