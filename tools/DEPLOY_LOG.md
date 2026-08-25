# Deploy log

What is actually live on the server, newest first. Written automatically by
the deploy tool, and only when the server was confirmed to be running the
commit that was just pushed.

**For a future session: read THIS file to see what shipped. Do not run git
log or diff to work it out — that costs far more to read than these lines.**
If the top entry looks older than the work in the repo, the difference is
what has not been deployed yet.

- **2026-08-25 00:02** · `fea7974e` · (no message)
- **2026-08-24 13:57** · `86d0410c` · TeePublic switching stages made stoppable, resumable and self-repairing.
- **2026-08-24 13:10** · `8a967de1` · Deploy now runs tools/preflight.py first: compiles, undefined names, JS parsing,
- **2026-08-24 07:38** · `3592bbde` · Counts now say what they mean: 17 of 627 in this continue, not 17 of 1543.
- **2026-08-24 07:23** · `c5ce73cf` · A wall no longer kills the night: the sweep waits 30/60/90 minutes and retries,
- **2026-08-23 21:30** · `05083e9f` · A paused or stopped scan no longer counts as a FINISHED scan. Resume carries on
- **2026-08-23 20:11** · `2cc8cbbd` · Search pages are now addressed directly (?page=N&query=) instead of following a
- **2026-08-23 17:53** · `6bff3a33` · The design catalogue is now kept between runs: added, removed and returned
- **2026-08-23 17:03** · `3b54c6a0` · STOP now actually stops the node, within one design, keeping everything checked.
- **2026-08-23 16:46** · `3cfceca0` · DEPLOY THE SITE TOO - the scan job payload is built server-side.
- **2026-08-23 16:27** · `2d5afb19` · Scan sends the full account payload (the browser needs selectors/timings) and
- **2026-08-23 16:18** · `54d0ff63` · Node needs: pip install -r worker_service/requirements.txt (beautifulsoup4).
- **2026-08-23 16:05** · `f0994d7c` · TeePublic tab: scan every design for search visibility, then deactivate and
- **2026-08-23 11:02** · `bc645ce5` · The TeePublic wall is dismissed by replaying a recorded mouse path.
- **2026-08-20 23:31** · `0cd07ae9` · Reading money and uploading now have one pause each, so a failure in one no
- **2026-08-20 22:43** · `354a27f1` · FAA still signs in to read earnings; TeePublic uses its saved session. Paused
- **2026-08-20 21:45** · `e034d293` · Reconciliation only runs where we hold the sales; the owed headline names the sites actually selected
- **2026-08-20 21:32** · `4edfb0af` · Earnings read no longer opens an upload tab it does not need, and reports what the server actually said instead of finishing silently
- **2026-08-20 21:04** · `c8834b8d` · Wait out self-clearing security checks instead of failing in three seconds; headless is now per marketplace and off for TeePublic
- **2026-08-20 20:57** · `04d693e4` · Failure screenshots and page dumps are now viewable on Diagnostics, including from jobs with no project
- **2026-08-20 18:48** · `4c6241f9` · Bot-wall check reads what the page SAYS, not its HTML — a dormant recaptcha widget was parking accounts for three hours
- **2026-08-20 18:29** · `c0328ec8` · TeePublic earnings; Earnings page marketplace-agnostic with owed per account; marketplace is a closed list so a typo cannot create a dead account
- **2026-08-20 18:12** · `ae7206d0` · TeePublic earnings (daily snapshots via the node); Earnings page now marketplace-agnostic with owed broken down per account; marketplace names settled on fineartamerica/teepublic
- **2026-08-20 15:40** · `5de54642` · Quiet-time and earnings-check times editable on the Earnings page, next to the line that describes them
- **2026-08-20 15:16** · `e251dee5` · Deleting a marketplace account now removes its Chrome profile from the worker machine
- **2026-08-20 14:29** · `268e4228` · One Chrome profile per account keyed on id, cleaned before launch; chromedriver log reported on first failure; sale timestamps upgraded from the ledger; quiet-time settings editable; funnel auto-refreshes
- **2026-08-20 13:20** · `170bbb5d` · Earnings: ledger no longer stops on sales it already has, so payouts are read; balance stored and shown as the headline with a reconciliation check
- **2026-08-20 11:41** · `7e857061` · Accounts shared across projects (ADD EXISTING); earnings read through the node's Chrome; nightly quiet window at 22:00 with a visible reason
- **2026-08-17 19:57** · `9c11311a` · Earnings reads its URLs from settings instead of hardcoded guesses — sign-in uses the same login_url the uploader does
- **2026-08-17 19:51** · `e266574e` · Earnings: read the real sign-in form instead of a guessed endpoint, and report what the page actually said
