# Deploy log

What is actually live on the server, newest first. Written automatically by
the deploy tool, and only when the server was confirmed to be running the
commit that was just pushed.

**FOR A FUTURE SESSION — READ THE TWO BLOCKS BELOW BEFORE ANYTHING ELSE.**
They cost about twenty lines and they answer "what is live" and "what is
waiting". Do not run `git log`, `git diff` or `git status` to work it out:
they cost far more to read and answer a different question, and on a mounted
working copy `git status` cannot refresh its index so it reports stale
answers with no warning.

## ⚠ NOT YET DEPLOYED

Anything listed here is written but NOT running on the server. **Say so at
the start of the session**, before the owner asks — he cannot tell by
looking at the site, and work built on top of an undeployed change is work
he will have to test twice.

Clear this block when the deploy is confirmed, and add the new line to the
log below.

**Waiting: v124** — targets 178.105.232.196 (test box)

  * `app/main.py` — gzip compression on text responses
  * `app/config.py` — APP_VERSION 123 → 124
  * `app/diagnostics.py` — two new checks: duplicate marketplace accounts,
    and accounts that have never once reported money
  * `scripts/migrate_pipeline.py` — `ensure_account` identified accounts by
    the dead `project_id` column and created a duplicate; no longer invents
    an email address; writes the canonical `fineartamerica`
  * `scripts/dev_setup.py` — writes the canonical `fineartamerica`

---

## Live

- **2026-08-25 19:19** · `557a4e74` · deploy v123
- **2026-08-25 18:09** · `eb18a279` · deploy v122
- **2026-08-25 17:52** · `e29893df` · v122 — external_id repeats across projects; the legacy import was matching MUSIK
- **2026-08-25 17:12** · `6c2f9afc` · v121 — legacy upload history, archive index, and the settings-drift warning
- **2026-08-25 17:00** · `44676a70` · v121 — legacy upload history, archive index, and the settings-drift warning
- **2026-08-25 15:36** · `264148df` · v120 — the wall now stops a reactivation instead of blaming 79 designs
- **2026-08-25 10:54** · `dcc9c48d` · deploy v119
- **2026-08-25 09:55** · `1eb9f747` · deploy v117
- **2026-08-25 09:50** · `272011a2` · deploy v116
- **2026-08-25 09:44** · `29106305` · Listing check now tells a REMOVED listing (410) apart from an address that
- **2026-08-25 09:14** · `0cf6dc6d` · Migration tool now copies the poster files server-to-server and can promote the
- **2026-08-25 08:56** · `82c60041` · Added .dockerignore. The Dockerfile ends with COPY . . and there was no ignore
- **2026-08-25 08:01** · `4a49f262` · FAA returns 410 for a REMOVED listing and 404 for an address that never
- **2026-08-25 07:55** · `de86461e` · (no message)
- **2026-08-25 07:38** · `a82f6f7a` · Deploy tool: an empty commit message no longer silently skips the commit and
- **2026-08-25 07:20** · `c21e035f` · The deploy tool now proves the RUNNING site matches the code you pushed, not
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
