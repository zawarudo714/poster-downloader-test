# TO TEST — built, deployed, never actually clicked

This is the owner's list, not a developer's list. Everything here is code
that is live on the test box and has passed its automatic checks, but that
**no human being has ever used**. A passing test proves the wiring is
connected. It does not prove the screen does what he wants.

---

## HOW THIS FILE WORKS — read this before adding to it

**The owner asked for this on `2026-09-06`.** His instruction: keep the
things he has to test in their own separate list, away from the build notes,
and leave each one there until he says he has done it.

Three rules, and the third one is the whole point:

1. **A new item goes in the moment something is built that only a person can
   judge.** If the only way to know it works is to look at it, it belongs
   here.
2. **Each item says what to click, what should happen, and what it means if
   it does not.** "Test the Google button" is useless to him. He should never
   have to guess what a correct result looks like.
3. **DELETE an item when he says he has done it. Never tick it and leave it
   sitting there.** A list that only grows stops being read, which is the same
   defect as a Diagnostics count that never goes down.

**Why a file and not a message in the chat.** A future session starts with no
memory of this one. If this list lives only in a reply, it is gone, and the
next session either re-tests things he already tested or, far worse, treats
untested code as proven.

---

## 38. THE SUBJECT DRAWING ON YOUR REVIEW SCREENS — v179

The kind icons from the worker page — City, Castle, Waterfall and the rest —
now appear where you judge the images too, so you know what the title asked
for while looking at what the worker found.

**What to click**

- Open **Worker Images**. Beside each title's name there should be a small
  round chip: the drawing plus the word, for example a castle drawing and
  the word "Castle". Hovering it explains what it is.
- Click an image to open it large. The same chip should be in the header
  there, and the title should now show its NUMBER in front of the name —
  that number was silently missing before, which was a small bug found
  while building this.
- Open **Approve Artwork**. The same chip should sit beside the place name
  above the two pictures, and it should change as you arrow through titles.

**What failure looks like**

- A chip showing a map pin instead of a proper drawing. The pin is the
  fallback for a word I have no drawing for — tell me the word.
- No chip at all on a title. That means the sheet's description column is
  empty for that row, which is allowed — only worry if it is missing on
  EVERY title, which would mean the kind is not reaching the screen.

**Also worth a glance while you are there:** a handful of leftover
"poster" sentences were found and fixed — the Worker Images count line, the
two payment pop-ups, and two messages on the pipeline test panel. Everything
should say "image" now.

---

## 39. CAPTIONS ON THE SEARCH RESULTS — v180

Every tile in the worker's search grid now has a strip under the picture
saying what the picture's own page calls it, with the pixel size in small
beneath.

**What to click**

- Search a place as the worker. Under each picture there should be up to two
  lines of text in the normal site font. A very long page title should end
  in "…" rather than stretch its tile.
- A result with no title at all should show just the size, with no empty gap
  where the words would be.
- Clicking the text should select the picture, exactly like clicking the
  picture itself.
- The captions should make stray results explain themselves — a tile
  captioned "Physical map of China" now says why it slipped past the filter.

**What failure looks like**

- Captions that read like web addresses. The words come from each page's own
  title, and a few sites use their address as their title — tell me if that
  is common enough to be ugly and I will trim those.
- Words sitting ON TOP of a photograph. The strip is below the image on
  purpose; overlap means the old styling is cached — force-refresh once
  before reporting it.

---

## 40. THE WRONG NEWCASTLE IS HIDDEN — v181

Your Newcastle, South Africa example. A result whose caption names the place
AND a different region — "Newcastle Beach Australia" — is now folded away
with the hidden ones.

**Press CLEAR SEARCH CACHE first** (Settings → Image Search). A search you
have already run replays its saved answer, old ranking included, so without
the clear you would be testing yesterday's code.

**What to click**

- Open Newcastle, South Africa as the worker and search. The Australian
  beach pictures from your screenshot should be gone from the top. The line
  under the grid should say how many were hidden and that some of them
  "name a different place". SHOW THEM still shows everything.
- Captions naming South Africa should sit first. Captions saying just
  "Newcastle" with no country stay visible — those words carry no evidence
  either way, so hiding them would be guessing.
- What this cannot catch, so you are not surprised: an Australian photo
  captioned only "Newcastle beach" shows no evidence in its words, so it
  stays visible. The caption under each tile is what lets you catch those.

## 41. THE GOOGLE BUTTON'S {kind} — v181

- Keep the GOOGLE BUTTON phrasing as `{title} {kind}`. Open a title and
  press OPEN GOOGLE IMAGE SEARCH. The Google tab should now have the kind
  word in its search box — for example "... South Africa town" — where
  before the kind was silently missing.
- The button also uses the search-words column now, like the in-page search
  always did, so the two searches finally ask for the same thing.

---

## 42. THE SKIP QUESTION — v182

- As the worker, press SKIP. The dialog should say "Type the reason in your
  own words", the typing box should already be open with the cursor in it,
  and there should be no TYPE OWN REASON button. One less click on every
  skip.

---

## 43. SKIPS STOP WAITING ON YOU — v183

Your question from the home page: does a skip sit in WAITING ON YOU for
ever? It did. Now it waits only until you have read it.

**What to click**

- Open **Skipped**. Your two skips should be in a WAITING ON YOU section,
  each with three buttons: SEND BACK, and I'VE READ IT — LEAVE IT SKIPPED.
- Press I'VE READ IT on one. The row should move to an ALREADY READ section
  below, and the home page's "titles a worker could not do" card should
  drop from 2 to 1 (the strip's WAITING ON YOU total drops too).
- Press ASK ME AGAIN on it. It should come back to the waiting section and
  the card should go back up.
- The real test of the design: mark one as read, then as the worker send
  that title back to yourself... actually simpler — leave one marked as
  read, and if a worker ever skips that same title again later, it should
  reappear in the waiting section on its own. Nothing to clear.

---

## 44. THE PASTE BOX CATCHES A BAD LINK — v184

- As the worker, open a title and paste a Google preview link into the
  paste box — one starting with `encrypted-tbn` or `gstatic.com/images`.
  The box should turn red and tell you to open the picture full-size first,
  with a SEND IT ANYWAY button beside it.
- Paste a normal photo link. It should save as always, no message.
- Paste a Google page link (a search or results address). It should say it
  is a page, not a picture.

---

## 45. THE ONE-ROW ACTION BAR + GOOGLE-TERM BUTTONS — v186

**On the site, as the worker:**

- Open a title. The bottom controls should be ONE row: the "(optional)
  reason" box, then SKIP, a small gap, then DONE on the far right. SKIP and
  DONE should look the same size as the SEARCH button.
- There should be NO "note for the admin" box any more. Press DONE on a
  title with its image saved — it should just complete, no note asked.
- Press DONE on a title with NOTHING saved — it should still ask for a
  reason (that prompt is kept).
- On your phone, check the row stays side by side and does not stack, and
  that opening a title now leaves the OPEN GOOGLE button easier to reach.
  If it is still too high, tell me and I will lower where the scroll lands.

**On the dashboard + phone add-on (v1.6):**

- Pipeline -> Settings -> Image Search: a new box "Google extra-term
  buttons (phone add-on)". It should hold aerial / skyline / street / at
  night / old town. Change it and save.
- On the phone add-on, open a title, press OPEN GOOGLE. Above SEND there
  should be a green "+ aerial", "+ skyline" button for each word. Tapping
  one re-runs the Google search with that word added. It is a normal search,
  one per tap.

---

## 46. APPROVE ARTWORK WON'T SKIP A LOADING POSTER — v188

- Open Approve Artwork on a range. Step through titles fast with the arrow
  keys or NEXT, double-pressing on purpose. You should NOT be able to jump
  past a title before its poster shows — the extra press is ignored while
  a small amber "waiting for the poster to load..." note is up, and the step
  lands the moment the poster appears.
- KEEP / RERUN / UNUSABLE should still work instantly, even while a poster
  is loading — only stepping waits.
- If a poster is genuinely broken and never loads, stepping should free
  itself within about 4 seconds so you are never stuck.

---

## 47. REVIEW PAINTED IMAGES IN BATCHES — v189

- Pipeline -> Settings -> Processing: set "Review batch size" to 20 and save.
- On Approve Artwork with more than 20 painted images waiting, the big
  button should read "REVIEW NEXT 20 · N waiting". Press it — only 20
  should load, and the header should say "batch of 20 · N more waiting".
- Inspect them, mark a couple RERUN, press SAVE & RELEASE. Only those 20
  should be released/queued; you land back at the start screen.
- The button should now show the reduced count and say "REVIEW NEXT 20"
  again. Press it for the next 20.
- Set the size to 0 and confirm it goes back to loading everything at once.
- Reviewing a single DATE, or JUST THE RERUNS, should still load the whole
  set (batching is only on the "everything waiting" button).

---

## 48. THE AUDIT'S TWO GUARDS — v191

- On Pipeline -> Settings, try saving a number box with text in it — for
  example put "abc" into "Review batch size" (you may need to paste it,
  since the box itself resists letters). The save should REFUSE with a
  plain sentence naming the box, and nothing should be stored.
- Run a Diagnostics scan. A new check called "number settings hold numbers"
  should exist and report nothing.
- The review screen, the worker page and the Skipped page should all look
  and behave exactly as before — everything else in v191 was cleaning,
  not behaviour change.

---

## 49. PAUSE NEW WORK NOW ALSO HOLDS BACK SIDE JOBS — v192

Before this, the pause stopped image batches but the machine could still
pick up an earnings read or a listing sweep while "paused" — so a reboot
around 22:00 could kill an earnings read halfway.

- Press PAUSE NEW WORK on the Pipeline page, then press "READ EARNINGS NOW"
  on the Earnings page (or queue a listing sweep). The job should appear in
  RECENT JOBS as queued and just SIT there — the machine must not start it.
- Press RESUME. The waiting job should start within a minute, on its own.
  Nothing should need re-queuing.
- While paused, a TEST job (test upload, test process) should still run —
  tests are you debugging and go through on purpose.
- The button's help bubble now says all of this, including "once RECENT
  JOBS shows nothing running, the machine is safe to reboot".

---

## 50. THE REVIEW SCREEN RESUMES WHERE YOU LEFT, AND THE COUNTER JUMPS — v193

- Open a review, step to some design in the middle, press CLOSE (or leave
  the page). Open the same review again — it should land on that same
  design, with a small message saying it picked up where you left off.
- The "23 / 25" counter at the top right: the first number is now a box.
  Type a number, press Enter, and you should land on that design. A number
  too big just takes you to the last one.
- Release a batch fully, then reopen — it should start at design 1 with no
  message, because the design it remembered is gone. That is correct, not
  a fault.
- The arrow keys and every decision key should behave exactly as before,
  including while the counter box is NOT focused. While you are typing in
  the box, letters and arrows only affect the box.

---

## 51. RERUNS OPEN ON THE NEW VERSION, CHOICES SURVIVE LEAVING, AND A
## CUT-SHORT SAVE EXPLAINS ITSELF — v193

- Open any poster that has more than one version. It should open on the
  NEWEST version, not v1 — in every door: whole date, reruns, everything
  waiting. (This was a real bug: the screen always started at v1.)
- Pick an older version on purpose (press 1, or the v1 button), leave the
  page, come back. It should still show the version you picked.
- Mark a poster RERUN and release it. When the new painting arrives and
  you review it, it must open on the NEW version even though you had an
  older one picked before — a fresh painting always outranks the memory.
- Press SAVE AND RELEASE and try to close the tab mid-save. The browser
  should ask if you really want to leave. Stay, and it finishes normally.
- Leave anyway mid-save. Reopen the review page: an amber note at the top
  should say the save was cut short, how many were released, and that the
  rest are waiting below with your marks. Press SAVE AND RELEASE to finish
  — the note disappears on its own. GOT IT also dismisses it.

---

## NOT ON THIS LIST, ON PURPOSE

**Re-importing the catalogue.** The database still holds 88,970 places and
the files now hold 88,876 — 44 war and grave sites were cut, then 50 bare
country names (2026-09-06). A job to do, not a thing to test, so it lives
in `ROADMAP.md`.
