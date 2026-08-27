# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v142 — review pass part 3: v140 created an immortal claim, and
the GPT stage was never audited** · targets 178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — server-side
only, `AGENT_VERSION` stays 1.28.0. **Schema change:** none.

## The finding: my own v140 fix created a wedge

The chain, traced end to end and reproduced:

  1. `gpt_worker._claim_next` COMMITS the claim before returning.
  2. `process_one` handles its three known failure shapes; anything OUTSIDE
     them escaped to the cycle handler, whose `rollback` cannot undo a
     committed claim. The poster stays at 'processing', claimed by 'server'.
  3. Every GPT success writes `ProcessedImage(processed_by="server")`.
  4. v140's reaper spares any claim whose owner produced evidence inside the
     window — so while the worker is busy with OTHER posters, the wedged one
     is spared forever. Before v140 it was freed in 45 minutes; after v140
     it was immortal. **One day old, found by asking what the fix meant for
     the one stage nobody audited.**

## Fixed at the source, per rule 8

  * `_cycle` now wraps each poster: any unexpected exception releases the
    claim through `report_process_failure` — the same call the node's
    report endpoint uses. Claim released, attempts bumped, error recorded;
    past `process_max_attempts` it surfaces in the failure list instead of
    retrying forever.
  * `_run` releases all 'server' claims at worker startup — the same rule
    as the node's first hello (v133/v140): a worker that just started is
    not processing anything. Covers the crash-and-restart path the in-cycle
    handler cannot.

**Reproduced against real SQLite:** the wedged poster is spared by the v140
reaper (immortal), the in-cycle handler frees it with correct semantics,
and the startup release covers the restart case. No path now leaves a
poster claimed forever.

## Also reviewed this part, NOT findings

  * The v132/v133 JS wiring traced end to end: `reconcile()` →
    `/api/overview` → `renderReconcile` → `unclassifiedNote`. The
    `unclassified` fields genuinely travel.
  * GPT's claim ORDERING is right — spend-cap check before the claim, three
    failure shapes released per poster.
  * `gpt_worker`'s heartbeat/watchdog structure: sound.

### Files

`app/gpt_worker.py`, `app/config.py` (141 → 142).

**After deploying:** the container restart itself exercises the startup
release — the app log will say "released N claim(s) left over" if anything
was stranded, or nothing if clean.
