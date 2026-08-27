# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v133 · AGENT 1.27.0 — stale claims released on node restart**
targets 178.105.232.196 (test box)

**THE NODE MUST BE UPDATED TOO.** Copy `worker_service/` to the Windows box.
`AGENT_VERSION` is 1.26.0 → 1.27.0, and the Nodes tab is the only way to
confirm the copy actually happened — a folder copy has no receipt.

## First: two things I reported as defects are NOT defects

`MEASURED 2026-08-27`, and both were my error.

**"6 jobs claimed and never reported."** It was five, not six — job #200
finished at 00:06 the next morning and I missed it because the node's log
rotates at midnight. And the five were killed by **agent restarts**: there
are 65 restarts across the 13 days of logs, almost all version bumps during
active development. Each one matches a job's last line:

    #85   07:07:59   agent restarted 07:50:38  (v1.15.0)
    #122  15:28:43   agent restarted 15:46:37  (v1.17.3)
    #123  15:47:42   agent restarted 16:03:14  (v1.18.0)
    #151  19:24:19   agent restarted 19:28:08  (v1.20.0)
    #174  11:53:35   agent restarted 12:57:17  (v1.23.0)

**The four `400 Invalid HTTP request` errors** begin one second after the
07:50:38 restart and stop at the next restart eight minutes later. Same
event. Self-resolved.

I read gaps in a log as stalls without checking what caused the gaps. The
answer was one grep away in files I already had. Recorded here because the
mistake is more useful than the non-finding.

## The real defect underneath

A job killed by a restart dies on the NODE while the SERVER still has it
`running`. It stays claimed until the stalled-job reaper times it out — up
to `claim_timeout_min` minutes of nothing happening. That is the cost, and
it happened 65 times this fortnight.

**Fixed:** the agent now sends `startup: true` on its FIRST hello only, and
the server releases jobs it has down as running for that node. A process
that has just started cannot still be doing what it claimed before.

Only on the first hello. `/hello` is called every poll cycle, and releasing
there would cancel live work every thirty seconds — a far worse bug than
the one being fixed.

The count comes back in the reply and the node logs it, so a restart that
releases work says so rather than being silent.

**Tested** against the shipped function, five cases: releases my own running
job on startup; releases nothing on a routine poll; ignores another node's
job; ignores a queued job; copes with nothing to do.

## Also

`app/static/js/admin_earnings.js` — the unclassified-row note was behind the
early return for "everything reconciles", so it only appeared when something
ALSO failed to reconcile. That is exactly when it is least needed, and it is
why you did not see it after v132. Now shown on both paths.

`app/config.py` — a comment still named `_host_is_internal()`, renamed to
`_host_check()` in v130.

### Files

`app/routes/pipeline_api.py`, `worker_service/agent.py`,
`worker_service/client.py`, `app/static/js/admin_earnings.js`,
`app/config.py` (132 → 133), `worker_service/agent.py` (1.26.0 → 1.27.0).

**After deploying:** restart the node once. Its log should say either
nothing, or "Server released N job(s) it still had me down as running."
The Earnings page should now show the note about the one unrecognised row.
