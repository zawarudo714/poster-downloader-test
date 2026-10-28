# Not yet deployed

## v273 — the Pipeline page tells the truth before you spend or publish (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed. (The worker's own log still says "dry run"
during a test upload. That line is on the worker machine; it is wrong,
and it changes the next time that folder is copied.)

- RUN TEST UPLOAD really publishes a listing on the marketplace. The panel
  said "Nothing is marked uploaded", which sounded like nothing happens.
  It now says plainly that it publishes, and asks you first.
- SWITCH STEP 1 ON now asks first, and says how many photos will start to
  be painted. Each one costs money.
- RETRY STEP 2 now refuses while step 2 is switched off. Before, the
  paintings went to a step that never runs.
- The AI panel's "N other photos wait" no longer counts the hand-named
  ones twice.
- The AI panel no longer says "M will go in the next send" when M is more
  than one send can take. It says how many wait, and the most per send.
- The STOP button no longer shows on a batch OpenAI has already finished.
  There was nothing left to stop.
- Approve Artwork: pressing SAVE twice quickly no longer sends every
  decision twice.
- A worker can no longer send a flag you already closed back to you. That
  put it back on your list and held the picture's pay again.
- New deploy check: "spending buttons ask first". New Diagnostics check:
  "Every closed change request is still closed".
- New test `tools/test_flag_stays_closed.py`; `test_step2_batch.py` covers
  the panel numbers and the RETRY STEP 2 refusal.

## v272 — taking paid-ahead money back no longer forgives the rest (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- When you pay ahead and a picture is later removed without pay, the next
  MARK PAID offers to take that money off.
- If the money owed was more than this payment, the amount stopped at 0.
  But every owed picture was recorded as paid back, so the rest of the
  money was forgotten.
- Now only the pictures this payment can cover are marked as paid back.
  The box says how much stays owed, and the next payment offers it again.
- No payment so far has taken money back, so nothing was lost yet.
- New Diagnostics check: "No payment took back more than it paid for".
- New test `tools/test_advance_deduction.py`.

## v271 — a picture link can no longer send the server onto its own network (2026-10-29)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- When someone saves a picture from a link, the server downloads it. It
  already refused links that point inside its own network.
- But a website could answer "go here instead" and point inside the
  network, and the server followed without checking. Now every such step
  is checked, and the download is refused if it leads inside.
- Normal pictures are not affected. A site that sends you on to its own
  picture store still works.
- New test `tools/test_download_redirects.py`, and a deploy check that no
  picture download skips this.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
