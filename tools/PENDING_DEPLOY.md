# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v138 · AGENT 1.28.0 — a stranded Photoshop batch now reports**
targets 178.105.232.196 (test box)

## THE NODE MUST BE COPIED

`worker_service/` changed. Copy the folder to the Windows box. `AGENT_VERSION`
is 1.27.0 -> 1.28.0 and the Nodes tab is the only way to confirm the copy
happened — a folder copy has no receipt.

No schema change.

## The defect

`processor.run_batch` claims a batch of images, and THEN does its setup:

    batch = self.client.claim_process_batch(...)   # images now claimed
    root   = self._storage_root(settings)          # can throw
    runner = self._ensure_runner(settings)         # can throw
    self._check_storage_writable(root)             # throws BY DESIGN

None of that was inside a handler that reports back. If the storage box is
unreachable — and `CLAUDE.md` records a Hetzner backbone fault affecting
Storage Boxes on 21 Aug — the exception propagates to `run_process_stage`,
which writes it to THIS MACHINE'S LOCAL LOG and nowhere else.

The images sit at 'processing'. The reaper releases them after the timeout,
the next cycle claims them, fails at the same line, and strands them again.
Forever. From the dashboard that is indistinguishable from a healthy idle
node.

**This is the exact failure `CLAUDE.md` rule 8 documents** — and the fix was
applied to the UPLOADER only. `uploader.run_batch` wraps `start()` and
`login()` and reports every claimed item with a reason, a screenshot and a
pause. The processor, its sibling, had nothing.

`_check_storage_writable`'s own docstring says it runs "before claiming
work". It runs after. That is why reporting matters there.

`MEASURED 2026-08-27` by reading both siblings side by side. **Not observed
happening** — I have not found an instance in the logs, and only one process
job appears in 13 days. The asymmetry is the evidence, not an incident.

## Fixed

Setup is now inside a try. On failure every claimed poster is reported via
`report_process_failure`, the job log gets the reason, and the batch returns
a summary instead of raising. One item failing to report does not stop the
rest.

Verified structurally against the shipped source: all three setup calls sit
inside a try, the claim happens before them, and the handler reports per
item.

Also documented the two stage wrappers in `agent.py` as a LAST RESORT rather
than the reporting path — if anyone finds themselves relying on those local
`self.log` lines to explain a stalled pipeline, the real report is missing
upstream.

## What batch 6 did NOT find

Recorded so nobody repeats them:

  * **Node dependencies.** `REQUIRED_MODULES` covers all four the agent
    needs and all four are in requirements.txt. My first check said it did
    not exist — the regex only looked for a tuple and it is a dict. The
    check was wrong, not the code.
  * **`pynput` missing from requirements.** It is used only by
    `path_recorder.py`, a tool run by hand, and both uses fail with an exact
    install command on screen. Deliberate, and requirements.txt says it is
    kept minimal on purpose.
  * **The job dispatcher.** Every job kind sits inside one handler covering
    all three exits — success, known failure, and anything thrown. Sound.

### Files

`worker_service/processor.py`, `worker_service/agent.py` (1.27.0 -> 1.28.0),
`app/config.py` (137 -> 138).
