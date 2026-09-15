# Not yet deployed

## v206 — the place check: is the worker's photo really that place?

Waiting to deploy. The node does NOT need copying — everything runs on the
server. Version 206 (live is 205).

What it is, in plain words:

- Every image a worker saves is shown to Google (web detection, the
  official cousin of Lens) and Google's words are compared to the title
  and its search phrase. The worker never waits — the check runs just
  after the save, in the background.
- On the Worker Images screen every box gets a small round dot marker
  beside the source pill: calm green "place ✓", loud amber "CHECK PLACE",
  quiet grey "no read", red "check failed", dashed "not checked". A round
  dot on purpose, so it cannot be confused with the rectangular source
  pill.
- A PLACE CHECK THIS DAY button opens a panel listing that worker-day's
  images beside Google's words — the ones Google disagrees with at the
  top, then the no-answers, then the rest. Each flagged row has a
  "CHECKED, IT'S FINE" button so the list is a to-do, not a treadmill; a
  later re-check brings a row back on its own.
- The same button fills in anything unchecked, a small chunk at a time.
  Closing the panel is the stop button — nothing is ever queued, so there
  is nothing to cancel. Old days are never scanned unless he presses the
  button on that day, so he controls the spend.
- Bias is toward CATCHING, at his instruction: any Google answer sharing
  no word with the title flags the image, vague answers included.

Before it can do anything he must paste a Google Vision API key into the
KEYS panel on the Pipeline page (make one in Google Cloud with the Vision
API turned on). The toggle "Check each image is the right place" is in the
search settings, ON by default; with it on and no key, attempts fail
loudly into a visible "check failed" state rather than doing nothing.

Underneath, for a future session: app/place_check.py carries the design
contract. One verdict per row for ever (a poster's file is immutable);
Google's answer is reused across identical images via content_hash — which
finally gets its producer — but the verdict is always recomputed against
the row's own title. "Google had no opinion" (status no_opinion) and "we
could not ask" (status NULL + place_check_error) are different states on
purpose. Five new saved_posters columns, migrated additively.

Safety nets: Diagnostics check `place_check_answering` goes red when
attempted checks are failing (a dead key must not hide as normal), grouped
by error so one broken key is one finding. Costs are visible in the panel
header: images checked this month, with Google's first 1,000 free.

Side effect worth knowing: because the checker now fills content_hash,
the existing duplicate-image Diagnostics check can start finding REAL
duplicate photos among newly checked images. That is it working, not a
fault.

Verified: preflight green, both JS files parse, pyflakes clean, and the
matching logic passes a 10-case behaviour test run against the shipped
file (with a sabotage run proving the test can go red). NOT verified from
here: a real Google call — this sandbox has no key and no network to
Google, so the first live click is his (TO_TEST 61). If Google refuses
key-style sign-in, the exact refusal lands in the "check failed" state
where it can be read.
