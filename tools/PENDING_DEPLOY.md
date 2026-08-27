# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v140 — review pass over yesterday's own fixes, part 1** ·
targets 178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — all four
changes are server-side, `AGENT_VERSION` stays 1.28.0. **Schema change:**
none.

A fresh-eyes re-audit, pointed first at the sixteen deploys made yesterday —
because quickly-written fixes are where new bugs live, and v130 proved that
once already. Two of yesterday's fixes turned out to be half-fixes of
exactly the shape they were correcting.

## 1. The v136 reaper fix protected uploads and abandoned Photoshop

v136 taught the reaper that a long batch is not an abandoned one — liveness
is the last thing the node SAID. But `live_nodes` was built from
`UploadTracking.uploaded_at` ONLY, while its own comment claimed processing
counted too.

Traced producer to consumer: every painted image writes a `ProcessedImage`
row carrying `processed_by` (the node's name) and `created_at` — the
evidence existed and was not consulted. So a node mid-Photoshop-batch,
uploading nothing for 45 minutes because it was busy PAINTING, was invisible
to the very check meant to protect it, and its remaining posters were
released mid-stride. `process_batch_size` is 20 and the painterly effect is
minutes per image; the margin is the same one v136 closed for uploads.

**Reproduced against real SQLite:** a node with 8 recent ProcessedImage rows
and zero uploads — v136 logic releases its 12 remaining posters alongside
the dead node's; the fix releases only the dead node's. `live_nodes` is now
the union of both signals.

## 2. The v133 startup release freed jobs and left batches waiting

When the agent restarts, its first hello releases PipelineJobs the server
still had it running — but NOT its batch claims. A process that has just
started is not running a batch either, yet its posters and upload rows
waited out the reaper's full 45 minutes. 65 restarts in the August logs,
each one up to 45 minutes of claimed work sitting still.

New `release_claims_for_node()` in pipeline.py — same resets as the reaper,
in one shared place so the two paths cannot drift — called from the startup
hello. Tested: releases exactly the restarting node's claims, leaves the
other node's alone.

## 3. v139's own check had a blind spot in its glob

`check_state_changes_are_logged` scanned `*_admin.py`, which does not match
`routes/admin.py` — the largest admin file. Scanned it by hand: the two
unlogged mutating endpoints there are benign (`master_upload` records itself
as an ImportJob row; marking chat read is not worth auditing). The glob now
includes it and both are in ALLOW with reasons — the net widened without
noise.

## 4. v137's caller shim could resurrect the bug v137 fixed

`except TypeError` around `parse_account_page(html, markers)` also catches a
TypeError raised INSIDE the parser by bad data — and silently re-parses
without markers, restoring the exact "wall reads as redesign" misdiagnosis
the markers exist to prevent. Now an `inspect.signature` check: precise,
and a parse-time TypeError propagates as itself.

## Reviewed and NOT findings

  * `Scope.label(pid)` in the starvation check — safe on both scoped and
    all-projects runs.
  * The agent's `_said_hello` ordering — set only after a successful hello,
    so a failed first handshake retries with `startup=true`. Correct.
  * The v135 rotation, v132 arithmetic (validated by the live "reconciles
    exactly" screenshot), retry quiet-window behaviour, and the
    dead-code removals — re-read, no new issues.

### Files

`app/pipeline.py`, `app/routes/pipeline_api.py`, `app/earnings/service.py`,
`tools/preflight.py`, `app/config.py` (139 → 140).

**After deploying:** nothing to click. Both fixes show themselves only when
a node restarts mid-batch or a Photoshop batch outlives 45 minutes — the
cases that were broken.
