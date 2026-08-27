# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v139 — audit batch 7, the last of the code areas** · targets
178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — nothing under
it changed, `AGENT_VERSION` stays 1.28.0. **Schema change:** none.

## The finding: permanent actions with no audit trail

`CLAUDE.md`: "ActivityLog for every state change — actor, target, timestamp,
JSON detail." The activity log is the only way the owner can answer "why did
this happen" after the fact; he cannot read the database.

Found by comparing SIBLINGS, which is what has worked all day:

  * **`api_skip_failures` logged nothing. `api_retry_failures` — its
    neighbour on the same screen, doing the REVERSIBLE version of the same
    thing to the same rows — logged properly.** Skip is described in its own
    docstring as "permanently exclude items from the pipeline". So the
    undoable action was audited and the permanent one was not. Without a
    record, "why was this design never uploaded" has no answer at all.
  * **`api_update_node` logged nothing, while `api_ban_account`,
    `api_delete_account` and `api_update_account` all do.** Switching a node
    off or removing a capability stops work happening, with nothing on
    record saying who did it.
  * **`api_reset_setting`** — dropping a settings override is exactly what
    you want to find when behaviour changes and nobody remembers touching
    anything.

All three now log. `MEASURED 2026-08-27`, traced from source.

## The check

`check_state_changes_are_logged` in preflight: an admin endpoint that
persists something and writes no activity log. A WARNING rather than a
failure, because some genuinely do not need one — a hard failure would train
people to silence it rather than think.

Four are explicitly allowed WITH a reason (`api_test`,
`api_test_gpt_process`, `api_trigger_run`, `api_cancel_job`), so the warning
list stays short enough that a new one stands out.

Sabotage-tested: removing the line just added makes it warn, naming the
function and the line. The FIRST sabotage attempt did not warn, and the
check was not at fault — a lazy regex had deleted the `db.commit()` too, so
the function no longer looked like it changed anything. Worth recording: a
sabotage that changes more than intended proves nothing.

## What batch 7 did NOT find — five empty searches

Recorded so nobody repeats them. The store, listing and scan code is the
most recently written and most heavily debugged in the repo, and it shows.

  * **Stop signals.** All three long-running node loops — store scan, store
    actions, listing sweep — carry a stop in the reply the node is already
    asking for, and the node reads all three. The "fixed one loop of three"
    problem from `CLAUDE.md` is genuinely fixed.
  * **Stage completion.** `advance_after_stage` derives whether work remains
    rather than counting, and all three ways a stage can end converge on it.
  * **Scan parallelism.** The node runs several account threads at once,
    which looked like it contradicted "the loop is serial". It does not —
    the AGENT loop is serial (one job), and one job parallelising internally
    is deliberate. The count comes from `scan_parallel_accounts`, a
    dashboard setting. My first grep looked in the wrong files.
  * **The listing sweep sending an empty request body** while the store run
    sends `{auto, mode}`. Correct: the listing check is manual-only with no
    stages or modes, so it has nothing to send.
  * **`_check_storage_writable`-style claim gaps** in the store code: none.

### Files

`app/routes/pipeline_admin.py`, `tools/preflight.py`,
`app/config.py` (138 -> 139).
