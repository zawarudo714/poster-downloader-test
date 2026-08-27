# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v136 — a long upload batch could be reaped mid-flight** · targets
178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — nothing under
it changed, `AGENT_VERSION` stays 1.27.0. **Schema change:** none.

## The defect

`reap_stale_claims` releases work claimed by a node that never reported
back. Your notes already record this exact mistake being found and fixed
FOR JOBS — comparing `started_at` against the timeout meant a healthy
hour-long TeePublic job was cancelled at minute 45 and its remaining
designs handed out again.

**The same function still compares `claimed_at` for posters and uploads.**
The fix went to one of three places.

`MEASURED 2026-08-27` from 13 days of node logs:

    upload per item     median 16-22s   p90 55-59s   outliers 5-9 min
    upload_batch_size   40
    claim_timeout_min   45 minutes

40 items at p90 pace is **37 minutes of a 45-minute timeout**, and
multi-minute outliers appear on every single day in the logs. A healthy
batch can outlive its own claim, at which point its remaining rows go back
to 'pending' while the node is still uploading them — and the next claim
can hand the same rows out again. For an upload that means a **duplicate
listing on a real marketplace.**

**What is measured and what is not:** the timings, the batch size and the
timeout are measured, and the asymmetry between the three tables is read
straight from the source. Whether this has ALREADY produced a duplicate is
NOT established — I have not found one, and I did not go looking hard. The
margin is thin enough to close regardless.

Processing has the same shape (`process_batch_size` 20) but I have no
measurement of Photoshop's per-image time — only one process job appears in
the logs. Treated as the same fix rather than a separate claim.

## The fix

Liveness is the last thing the NODE said, not when the batch started. Every
item a node finishes stamps `uploaded_at`, so a node working steadily
through a long batch is visibly alive even though the item in its hand is
not. The reaper now skips rows claimed by a node that has reported anything
inside the timeout window.

Derived, not stored — no new column, nothing to keep correct.

**Tested against real SQLite:** a busy node claimed 50 minutes ago but
reporting 2 minutes ago keeps its work; a dead node claimed at the same
moment loses its work; and when the busy node goes quiet for 90 minutes its
work is released too.

### Files

`app/pipeline.py`, `app/config.py` (135 → 136).

**Not verified:** nothing has run through this against the live node.
Nothing to click — it shows itself only when a batch runs longer than 45
minutes, which is exactly the case that was broken.
