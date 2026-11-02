<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## Working with this owner

- He wants to understand the system, not just receive it. Explain trade-offs.
- He will ask "what about X" about the thing you glossed over. Cover failure
  modes, recovery and debugging up front.
- He is cost-conscious and picks cheap, simple, mainstream hosting.
- **Anything he might want to tweak belongs in the dashboard.** He has said
  this more than once. Treat a hardcoded value as a defect.

  **AND A VALUE IN `DEFAULTS` WITH NO BOX IS THE SAME DEFECT WEARING A
  SECOND HAT — it is far easier to miss, because nothing is wrong.**
  `brave_query_normal` is the words the worker's SEARCH button sends to
  Brave. It sat in `pipeline.DEFAULTS` with no field on any screen from the
  day the search was built until 2026-09-03, so the one thing he most needed
  to experiment with was the one thing he could not touch without a deploy.

  Nothing could have found it. The code parses, the key is declared, the
  setting resolves, every page renders. **A rule about something ABSENT has
  nothing to trip over**, which is why writing the rule down was not enough
  and it quietly went on being broken for months.

  Now mechanical: `check_settings_are_reachable` in `preflight.py` compares
  `DEFAULTS` against the fields on every admin screen. A key with nowhere to
  type it fails before deploy unless it is listed as deliberately exempt with
  a reason. Sabotage-tested twice — removing the box goes red, and a mere
  MENTION of the key in help text does not satisfy it.

  It found 18 more the same day, including the worker PAY RATE and the
  ALLOWED IMAGE HOSTS — both named in this very file as examples of what
  belongs on the dashboard. They are named in `SETTINGS_WITH_NO_BOX_YET` and
  warn rather than fail, so the existing debt does not block every deploy
  while anything NEW still fails at once. Clearing that list is a QoL or Mega
  Audit job.
- **And it belongs where the thing it governs is SHOWN.** A setting he cannot
  find is a setting he does not have. The nightly earnings times were added
  to Upload Settings — correct by category, useless in practice, because the
  screen that displays "quiet from 22:00" is the Earnings page and that is
  where he looked. One stored value, editable from wherever it is relevant.
- He notices the thing you glossed over. Say the uncomfortable part first.
- **EVERY SSH COMMAND STARTS WITH THE `cd`.** Standing instruction, 2026-09-05.
  Never write `docker compose exec ...` on its own — write
  `cd /opt/poster && docker compose exec ...`, as one line he can paste.

  He asked for this after the SECOND time a bare `docker compose` command
  came back with "no configuration file provided: not found". The reason it
  keeps happening is that his shell opens in `/root`, and `docker compose`
  only works where the `docker-compose.yml` is. The command is not wrong; it
  is incomplete, and the error it gives names the missing file rather than
  the missing folder, so it reads like a broken tool.

  The general shape: **a command handed to somebody else must carry
  everything it needs to run.** Assuming a working directory, a virtual
  environment, an environment variable or a previous command is the same
  defect as assuming a value — it works on the machine where you imagined it
  and fails on his.

### Every number and label must make sense to someone who never built this

A count is only meaningful next to the right denominator, and the right
denominator is the one the person is actually looking at.

The incident, 2026-08-24: a CONTINUE sweep covering 627 designs reported
**"17 of 1543 checked"**, because the code had `checked` (this run) and
`total` (the whole catalogue) to hand and printed both. Technically true,
and he could only tell it was wrong by opening the node's console. What it
should say is 17 of the 627 THIS run is doing, with 1,543 mentioned as
context — not as the thing being counted against.

So, before shipping any figure or label:

* **Read it as a stranger.** If the sentence needs you to know how the code
  works, rewrite it. "17 of 1543" needs you to know there are two different
  totals in play.
* **Name the SCOPE of a count**, never just the number: "in this run", "in
  the last 24 hours", "across all accounts". And name the THING counted when
  two counters sit near each other: v251 put "1 / 100" under the gallery
  (titles) and "1 / 102" in the zoom (pictures), and the owner read the
  difference as a bug (2026-10-09). They now say "title" and "image". The
  same day, the worker's TODAY read 96 against 58 he had saved, and kept
  climbing while he was offline: it counted by the FOLDER's name, and the
  owner's own picks live in the worker's folder. **Count by who did the
  work, never by where it is filed** — and a "today" compared against a
  UTC column must start at LOCAL midnight (`utc_start_of_local_day`;
  preflight's `check_day_boundaries_are_local` fails any new
  `datetime.combine` day boundary).
* **A mechanism should announce what it is doing**, in its own words —
  "waiting to try again at 23:44", not "retry_at set".
* **A label must not be true and misleading.** RUN TEST UPLOAD's panel said
  "Nothing is marked uploaded" — true — while it published a real listing
  the site never recorded, so the normal upload would list it a second
  time. The AI panel said "N other photos wait" with N counting the named
  ones again, and "M will go in the next send" past the per-send cap
  (`batch_max_items`). All found by the site audit, 2026-10-29, fixed in
  v273. SWAP N LIVE LISTINGS NOW counted every waiting listing while one
  job takes one account and at most 500; v274 made the button, the toast
  and the job's list read one helper (the swap itself was removed in v289).
  Say
  what the button DOES to the outside world first, count each thing once,
  and **a button's number is what ONE press does** — a number the press
  only partly covers reads it from the same helper the work uses.
* **A button that spends money or publishes asks first, every time.**
  SWITCH STEP 1 ON started paying for every held-back photo in one click
  while the catch-up and the swap both asked. Preflight's "spending
  buttons ask first" fails a deploy on a button listed in
  `SPENDS_OR_PUBLISHES` whose handler has no `confirm(`; add new ones
  there. And a button that hands work to a step that is switched off
  refuses instead (RETRY STEP 2 now does, like the catch-up) — otherwise
  the work waits where only Diagnostics sees it.
* **A state change names the state it starts FROM.** The worker's "send
  with no change" set any flag to "awaiting approval", so a closed one
  came back on his list and held its picture's pay again (v273;
  `check_closed_flags_stay_closed`). Every admin path already checked
  first. And a save that runs in pieces refuses a second start while the
  first runs (the review screen's `commit()` now checks `commitBusy`).
  The FROM check goes IN the write when two people can race for the same
  row: checking rows already loaded let two workers pressing GET together
  both be told they held the same titles (v280; `_claim_if_free`, preflight
  "claims re-check the row", `tools/test_claim_race.py`).
  Every DOOR to the same action asks the same FROM question: the paste
  door refused a closed title, the search grid checked only the claim
  (which skip and complete keep) and saved onto it (v277; preflight
  "closed titles refuse new pictures", list `SAVES_ONTO_A_TITLE`).
  And a reset to a state resets EVERY field that state promises: a title
  sent back to "pending" by deleting its last picture kept the old
  worker's start time, save date and folder, so the next worker's save
  was filed under the old date (v278; `check_pending_titles_are_clean`).
  A key that acts on several things at once SETS them; it does not
  toggle each one — the review screen's title KEEP flipped a KEEP
  already made back off on a pair (v275; preflight "title key sets,
  never flips", `tools/test_title_keep.py`).
* **A STATUS THAT IS TRUE AND ANSWERS NOTHING IS STILL A DEFECT.** The
  status strip read "machine on · idle". Both words were correct and the
  owner still could not tell a finished day from a jam, because idle with a
  hundred images queued and idle with nothing queued look identical. A
  status needs the thing it is being compared against: it now says what the
  machine is doing AND what is queued behind it, so "doing nothing" beside
  "47 to paint" reads as the fault it is. Before shipping a state word, ask
  what a person would ask NEXT — and put that on the screen beside it.
* **TWO NUMBERING SYSTEMS MUST NEVER SHARE ONE SPELLING.** Diagnostics
  printed a picture's RECORD number as "#142"; the zoom prints the title's
  SHEET number as "142. Yellowstone". The owner looked up 142, found the
  wrong place, and rightly doubted a true finding (2026-09-27). Every
  number on a screen says what it counts — "title 57", "picture record
  142". Watched by `check_diagnostics_numbers_are_named`, which found 13
  more the day it was written.
* **Numbers on a button must be the numbers that will happen.** A button
  saying 627 that then does 1,543 is worse than a button with no number.
* **A BUTTON LABELLED WITH A BARE VERB HAS NO SUBJECT, SO NOBODY CAN TELL
  WHAT IT WOULD ACT ON.** The recall panel's button read COUNT THEM FIRST
  and the owner's reply was "not sure what count is supposed to do"
  (2026-09-09). "Them" pointed at a text box of numbers he had not typed
  anything into, so the word named nothing on the screen. It now reads SHOW
  ME WHAT THIS WOULD DELETE, and the panel says "12 titles ticked above"
  beside it at all times. The test: **read the label with the screen empty.**
  If it still names what it acts on, it is a label; if it only makes sense
  once you already know, it is a note to yourself.
* **A WARNING THAT FIRES ON THE NORMAL CASE IS NOT A WARNING, IT IS A
  KEYSTROKE.** The "this looks like a low-resolution preview" message asked
  whether the address was on one particular image host and called everything
  else low quality. Travel workers paste Google addresses all day, so it
  fired on EVERY save, and its advice named a film database nothing in this
  system has used for weeks. Once a confirmation is answered by reflex, the
  one save that genuinely WAS a thumbnail gets confirmed by reflex too — so
  a warning nobody can act on is worse than none. It measures the picture
  instead of the address. It began as a warn-only test that fired when the
  picture was small on BOTH sides. On 2026-09-15 the owner turned it into a
  HARD floor: refuse when a picture is under `min_image_px` (350) on ANY
  side, on every door, with no "save anyway". That is stricter than the
  "fires on the normal case" lesson would suggest — a real wide vista whose
  short side is under 350 is now refused — but it is a deliberate quality
  floor the owner asked for, not an accidental over-warning, and it does not
  fire on an ordinary full-size photo. The shared test is `_too_small` in
  `worker.py` (now "either side"), read by all three save doors.

  Before shipping any guard, ask **how often it will fire when nothing is
  wrong.** If the answer is "most of the time", it is not a guard. Same
  family as "(N/A)" beside every title: noise trains the eye to skip the
  line where the real thing eventually appears.
* **A RECORD OF WHAT HAPPENED MUST OUTLIVE THE THING THAT WAS HAPPENING.**
  The listing check's log panel was drawn only while a sweep was RUNNING, so
  it disappeared the moment the sweep finished — and "what did the worker
  machine actually do" is a question asked afterwards far more often than
  during. The owner looked for the panel and could not find it at all
  (2026-09-09). Anything that explains a run — a log, a tally, a reason —
  falls back to the LAST one and says plainly which it is showing.

This is not polish. He has no other way to verify the system is doing what
he thinks: he cannot read the database and should not have to read a log on
an unattended VPS. A screen that is confidently wrong costs him a night.

Being tightened everywhere as part of the INTERACTION AUDIT (planned item 8).

