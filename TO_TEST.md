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

## 52. THE SCREENS SAY WHICH SEARCH FOUND EACH IMAGE — v195

Do one save each way, then look for the word in four places:

- Save one image from the in-page grid (SAVE SELECTED), one from the phone
  add-on (SEND TO SITE on Google), and one by pasting a link.
- Activity Log: the three "saved" lines should read "found on Brave",
  "found on Google" and "pasted link" beside the filename.
- Review Posters gallery: each card should carry a small blue pill —
  Brave, Google or pasted. Click a card: the lightbox line at the bottom
  should say the same.
- Approve Artwork (after the three are painted): the caption under "what
  the worker found" should carry the word, on the card AND in the zoom
  compare view.
- Images saved BEFORE this deploy show no word anywhere. That is correct —
  the record simply does not exist for them, and a blank is honest where a
  guess is not.

---

## 53. DELETING A FLAGGED IMAGE LEAVES A RECORD, NOT A QUESTION — v196

- First, clear the leftover: the Troy, Turkey flag still says "awaiting
  approval" from the old flow. Approve it once on Changes Requested.
- Flag any image with a comment. As the worker, DELETE that image. The
  worker should see a toast saying the flag is closed and they can carry
  on — no "awaiting admin approval" anywhere, and the title returns to
  the pool immediately.
- On Changes Requested, the deletion should appear under RECENT
  DELETIONS with the worker's reason. ACKNOWLEDGE makes it disappear;
  SEND BACK (with a note) re-flags the title and pins your note to it.
- The dashboard's "needs your attention" digest should count it under
  pending deletions until you acknowledge it.
- Flag an image and have the worker REPLACE it instead — that must still
  wait for your approval exactly as before. Only deletion changed.

Also in v196, the full back-and-forth on Changes Requested:

- Flag an image, then as the worker SWAP it from the search grid (tap a
  new picture, confirm the replace). The worker should see a toast that
  the new image went to the admin. On Changes Requested the card must
  show the NEW image — not a deleted-file placeholder — with a REPLACED
  pill. Approve and reject both act on that new image.
- Every PENDING COMPLETIONS card now has a "THE TITLE NOW HOLDS" strip
  with the live images, clickable to full size. Approving should never
  require opening Worker Images to cross-check.
- RECENT DELETIONS cards show the same "title now holds" line — images
  if the worker re-did the title, or "nothing yet" if it went back to
  the pool untouched.
- Run a Diagnostics scan after playing through the above: "change
  requests on deleted work" must report nothing (once the old Troy row
  is approved away).
- The thumbnails on Changes Requested: a LANDSCAPE photo must show whole
  (letterboxed, not cropped to a portrait sliver), and clicking any
  thumbnail — change cards, awaiting cards, open flags, "title now
  holds" — opens the full-size image in a new tab.

---

## 54. REVIEW ORDER IS YOURS TO PICK, AND COMING BACK WALKS BACK IN — v197

- On Approve Artwork's start screen there is now an "order" dropdown:
  oldest saved first / freshest painted first / by sheet number. Pick
  one, open a review, and the designs should come in that order. Reload
  the page — the dropdown must still show your pick.
- Open a review, step to a design, open the ZOOM compare view, then
  leave the page entirely (close the tab or click another screen). Click
  Approve Artwork again: it should walk straight back in — same door,
  same design, zoom already open.
- Now press CLOSE inside a review, then reopen Approve Artwork: this
  time it must show the normal start screen, not jump back in. Same
  after SAVE AND RELEASE finishes — next visit starts at the picker.
- "Freshest painted first" should put a rerun that just came back at the
  front of the queue.

---

## 55. WORKER IMAGES: ORDER PICKER, WALK-BACK-IN, AND NO MORE CROPPED
## THUMBNAILS — v198

- Worker Images now has an "order" dropdown: by sheet number / newest
  saved first / flagged first. The numbers must come in real numeric
  order (2 before 10 — before this fix the text sort put 10 first).
  Reload: the dropdown keeps your pick.
- Click an image so the enlarged view opens, then leave the page. Coming
  back to Worker Images must reopen that same image enlarged. Close the
  enlarged view with ✕ first instead, and coming back shows the plain
  gallery.
- Thumbnails across the app now show the WHOLE photo instead of a
  portrait crop: the worker's flag card, the Peek page flags, the
  worker's own catalogue list, and the style-reference preview on the
  Pipeline page. A landscape photo must be letterboxed, never cropped.

---

## 56. A REFUSED PAINTING CAN BE SENT BACK TO PAINTING — v199

- Open Pipeline → NEEDS ATTENTION. The San Francisco row should now
  carry a "refused at output" pill (hover it for the explanation), say
  "refused" instead of "999" under TRIES, and offer SEND BACK TO
  PAINTING next to MARK UNUSABLE.
- Tick it and press SEND BACK TO PAINTING. After the confirm, the row
  leaves the list and the painter picks it up on its next pass — watch
  it arrive in Approve Artwork if the repaint passes, or return to this
  panel if the filter refuses again. Repeat as many times as you deem
  worth the cost; MARK UNUSABLE when done trying.
- The Activity Log should show a "pipeline retry" line for the send-back
  (this was silently unlogged before).

---

## 58. PHOTOPEA EVERYWHERE, AND DELETING LETTERED VERSIONS — v201/v202

(The v200 heal brush was REMOVED in v202 before anyone tested it —
Photopea does its whole job better. Its old test item is gone with it;
the lettered-version machinery it introduced lives on underneath
Photopea and is tested here.)

- The 🖌 EDIT IN PHOTOPEA button must appear BOTH on the normal review
  card and in the zoom view. There is no HEAL SMUDGES button anywhere
  any more — gone on purpose, not broken.
- Press it. The editor should open over the page with your picture
  already loaded a moment later (it needs the internet in your browser;
  expect their ad panel). Fix a smear with your usual tools — pen
  selection, spot heal, clone stamp.
- Press SAVE BACK AS NEW VERSION. The overlay closes, the screen lands
  back on the same title, and the generations bar shows the new letter
  (v1b, or v1c if v1b existed). The colour and signature controls must
  still work on it, and approving it must build the print file as usual.
- If you flattened the image in Photopea, saving must still work and
  the print must come out full size — say if anything looks small.
- Pick a lettered version and press 🗑 DELETE. It should vanish from the
  bar, the parent version takes its place, and Diagnostics stays clean.
  Trying to delete v1b while a v1c made from it exists must refuse with
  a sentence naming v1c. There is no delete on paid generations —
  correct, not missing.
- Close the editor WITHOUT saving: it must ask first, and nothing
  changes on the site.
- Numbering after an edit: with v1 and v1b on a poster, press RERUN —
  the new paid generation must arrive as v2, not v3. (An edit is a row
  but not a generation; the counter was fixed to know the difference.)

Added in v203:

- The Approve Artwork badge in the sidebar must NOT go up when you make
  an edit — it counts decisions waiting, and an edit does not add one.
  (Before the fix each edit added one; your current inflated number
  corrects itself the moment the page reloads after deploy.)
- Double-click EDIT IN PHOTOPEA fast: exactly one editor must open.
- In the generations bar, v1b/v1c must be GREEN while v1/v2 keep the
  normal colour — paid and free telling apart at a glance.

---

## 59. RELEASE ONLY APPROVES WHAT YOU SAW — v204

- Open a review batch, look at a few designs, then (as the worker or by
  waiting for a rerun) let a new image become pending. Trigger a reload
  mid-sitting — save a Photopea edit, or refresh the page and let it
  walk back in. The tally at the bottom should now say "… · N arrived
  unseen, staying".
- Press SAVE AND RELEASE: the confirmation must repeat that the unseen
  ones stay waiting. After the save, they must still be pending — check
  the WAITING ON YOU count and the next batch.
- Step through every design in a batch and release: nothing should be
  held — "arrived unseen" only ever names images that were never on
  your screen.
- The cleanup from the incident: recall or accept today's uploads whose
  filename carries _v2 or higher (they are the repaints released before
  this fix).

---

## 60. A RE-SENT TITLE GETS THE NEXT LETTER — v205

- Take a title that is already live on FineArtAmerica. Send it back with the
  recall panel, let the machine repaint it, and let it upload again. The new
  listing should go up as "<name> B", not "<name>". For example, "Kyoto"
  becomes "Kyoto B".
- Send that same title back a second time and let it upload again. It should
  now go up as "<name> C".
- Check the address matches: the listing check should find "<name> B" live
  and should NOT report the title as missing. If it reports missing, the name
  we stored and the name FAA shows have drifted apart — that is the whole
  thing this was built to stop.
- The clean case: a title that was painted but NEVER uploaded, then sent
  back, should still go up under its plain name with no letter. The letter
  only appears for a place that has actually been live before.
- Panel wording: open the SEND TITLES BACK panel and read the note. It should
  now say a live title is safe to send back, and that the old listing stays
  live on FineArtAmerica until you delete it there.

---

## 61. THE PLACE CHECK — v206

- First, the key: make an API key in Google Cloud with the Vision API
  turned on, and paste it into the KEYS panel on the Pipeline page
  ("Google Vision key"). This is the one step only you can do.
- Save a fresh image as a worker. The save itself should feel exactly as
  fast as before. Refresh the Worker Images screen a few seconds later:
  the new box should carry a small round dot — green "place ✓" if Google
  agrees with the title, amber "CHECK PLACE" if it does not.
- Press PLACE CHECK THIS DAY on the Worker Images screen. The panel should
  list the day's images beside "Google sees: …", with the amber ones at
  the top. Press the CHECK THE N UNCHECKED button and watch older images
  on that day fill in a few at a time; closing the panel stops it.
- On an amber row, press CHECKED, IT'S FINE — the row should drop down the
  list and the box's dot should turn to a dashed green "checked ✓" after
  you close the panel.
- The honest failure: with the key box EMPTY and the toggle on, a fresh
  save should show a red "check failed" dot (hover it), the panel should
  say plainly that the key is missing, and Diagnostics should carry a
  warning called "place check(s) could not run". Nothing anywhere should
  pretend it is fine.
- The wrong-place case that started this: save a picture of one city on a
  different city's title — it should come up amber with Google naming the
  real place, which is the whole feature.
- The bill: the panel header should count "images checked this month" as
  you go. Google's first 1,000 each month are free, then about $3.50 per
  1,000.

---

## 62. IMAGES IN CHAT — v210

- On the admin chat, open a worker thread, press the image button (🖼) by the
  message box, press CHOOSE FILE, pick a screenshot, and send. It should show
  inline in the thread, and clicking it should open the full image in a new
  tab.
- Send one with a typed message too — both the text and the image should
  appear together.
- Paste an image link into the "…or paste an image link" box and send — it
  should render inline the same way.
- As a worker (log in as a worker, or have one do it), send you an image back.
  It should appear on your admin side.
- The privacy check that matters: a worker must only see images from their
  OWN thread. This is enforced in code, but worth one look — nothing in a
  worker's chat should ever show another worker's picture.
- Bad input: try sending a non-image file (a PDF) — it should refuse with a
  plain message, not break. Try a very large image (over 12 MB) — it should
  refuse for size.

---

## 63. HARD 350px SIZE FLOOR ON EVERY SIDE — v211

- First, if you ever set the size box on the Pipeline page by hand, open it
  and confirm it reads 350 (the label now says "Reject below this size"). A
  stored value wins over the new default.
- Save a small image (under 350 on a side) from the in-page grid — it should
  be refused outright, no "save anyway".
- Do the same from the phone add-on (Google) — also refused, and the add-on
  should show the refusal, not save it. The extension itself needs no update.
- Paste a small image link — refused, with a plain message and NO "save
  anyway" button or prompt.
- Replace an existing image with a small one — refused the same way, and the
  original image should stay untouched.
- The deliberate strict case: try a genuine wide, short picture (for example
  1600 wide but 320 tall). It WILL be refused now, because one side is under
  350. Confirm that is what you want; if it is too strict, the number is on
  the dashboard.
- A normal full-size photo (well over 350 both ways) should save exactly as
  before.

---

## 64. FLAGGING NO LONGER RELOADS OR CLOSES THE ZOOM — v212

- On Review Images, scroll well down a day, open an image in the zoom, and
  press FLAG FOR CHANGES. The page should NOT reload and should NOT jump to
  the top. The zoom should stay open on the same image, now showing CLEAR
  FLAG.
- Press → (or the next arrow) straight after flagging — you should move to
  the next image with no reload, whether the next one is flagged or not.
- Close the zoom. The image you flagged should show its red flag border and
  "flagged" pill, and the page should still be where you left it.
- Press CLEAR FLAG on an already-flagged image — it should clear in place,
  the pill and border should go, and again no reload.
- Flag several in a row and confirm you never have to scroll back down.

---

## 65. CHAT FILLS THE PHONE SCREEN — v212

- Open the chat on your phone. The message area should reach down near the
  bottom of the screen, with the SEND box just above the bottom edge — no
  large band of empty black space below it.
- Check it with only one worker in the list (a short list) and with the
  keyboard open — the chat should still fill the space, not stop short.
- Check the worker's own chat page on a phone too (log in as a worker).
- On a desktop, the chat should look exactly as before.

---

## 66. THE SLIMMED FLAG CARD AND THE LAST-WORKER-ACTION CHIP — v213

- As a worker, open Changes Requested. Each card should show the thumb, the
  paste box and REPLACE FILE side by side — no DELETE FILE, no big SEND FOR
  APPROVAL. Under the card is a small underlined line: "Nothing to change?
  Send it back to admin with a note." Clicking it asks for a note and sends
  the flag back to you unchanged.
- On a phone, REPLACE FILE should sit beside the paste box on the same row,
  not below it. FIND A REPLACEMENT sits on its own full row below the pair
  (fixed in v215 — in v214 all three crowded one row and the paste box had
  almost no space).
- Delete a flagged image from the saved-images panel (via FIND A REPLACEMENT
  or GO TO TITLE) — the flag should resolve with a record, same as before.
- The fixed defect: after a worker deletes a flagged image, their card
  should show the dashed placeholder picture AND the line "You deleted this
  image. Admin will review the deletion…" — before this it showed a bare
  card with nothing.
- On any admin screen, the status strip should show "last worker action"
  with the newest thing a worker did, like "humphrey saved Cortina 1.jpg ·
  09-15 09:16" — never your own admin actions. Clicking it opens the
  Activity Log. Have a worker save something and watch it update within ~15
  seconds.
- The two audit fixes, same version:
  · Replace a flagged image by pasting a URL. A few seconds later its
    place-check dot should reflect the NEW picture (it re-checks), not the
    verdict the old picture had.
  · Try adding a small image (under 350 on a side) through YOUR own + ADD
    box on Worker Images. It should refuse with the size message — the
    admin door now has the same floor as every worker door.

---

## 67. THE CHIP NAMES THE TITLE AND LINES UP — v214

- On any admin screen, look at the "last worker action" chip after a worker
  completes a title. It should now name the place, like "humphrey completed
  Cortina d'Ampezzo, Italy · 09-15 09:16" — before this it just said
  "humphrey completed" with nothing after it. Claiming and skipping should
  name the place too.
- The chip should sit neatly in line with its neighbours. A very long title
  is cut off with "…" — hover over the chip to read the whole sentence.
- The date and time part should look smaller and greyer than the action
  itself.

---

## 68. FLAG TAGS CLEAR, AND SUBMIT-AS-IS ASKS FIRST — v216

- On Worker Images, open a flagged title's image in the zoom and clear the
  flag. The image's own red border goes, AND the title's red left-outline
  should go too if that was its last flag — with no page reload. If the title
  has another flagged image, the outline should stay. Flagging the first image
  of a clean title should add the outline live as well.
- As a worker, on a Changes Requested card, click "Nothing to change? Send it
  back to admin". It should now ask you to confirm before it sends, so a stray
  tap cannot fire it. The note is still optional.
- Was the "still flagged after I approve" problem really the Worker Images
  outline lingering? If you ever approve a flag and STILL see the red tag on a
  FRESHLY loaded Title List or Worker Images page, tell me — that would point
  at a different cause, and the new Diagnostics check below is there to catch it.
- Open Diagnostics. There is a new check, "Every title's flag tag matches its
  open flags". It should read green. If it ever lists a title, that title's
  stored flag is out of step with its real flags.

---

## 69. "EDIT IN PHOTOPEA" RESPONDS AT ONCE — v217

- On Review Images, click EDIT IN PHOTOPEA. The editor overlay should appear
  straight away with a spinner reading "Loading the picture into the editor…",
  even before the picture has finished downloading. It should not sit there
  looking dead.
- Because the overlay covers the screen, you cannot step to another image
  while it loads. The picture that opens should always be the one you clicked
  — the old "it opens the previous image I clicked" behaviour should be gone.
- The SAVE button should be greyed out until the picture is actually in the
  editor, then become clickable.
- It should feel a bit quicker to open, because the editor now boots while the
  picture is still downloading. On a slow connection the download itself is
  still the floor — but now you can see it is working.

---

## 70. AFTER THE FAMOUS BATCH, THE QUEUE FALLS BACK TO NORMAL — v218

You already tested the first half on 2026-09-17: GET as the worker brought
the famous landmarks (Great Wall #87836, Eiffel Tower #87837…) ahead of
Almaty #251. The half nobody has seen yet only becomes testable later:

- **When**: once the worker has finished (or you have returned) all 146
  famous titles.
- **What to click**: log in as the worker and press GET.
- **What should happen**: the queue goes back to plain number order — the
  next titles are the ordinary ones from #251 upward (Almaty, Omaha…), with
  no gap and no repeat of a famous one.
- **If it does not**: a famous title reappearing means its priority or status
  did not clear on completion; ordinary titles being skipped past means the
  ordering clause is wrong — say which you saw.

---

## 71. A MISSING PICTURE FILE NOW SAYS SO — v219

- **Where**: Changes Requested (as admin) and the flag panel (as the worker).
  Atlanta, Georgia (#36) is the live case.
- **What to click**: open Changes Requested. The Atlanta card should no
  longer show a broken thumbnail — it should say the picture file is
  missing and offer a DELETE THIS RECORD button.
- **Then**: press the button. The card disappears, the flag closes, and
  Atlanta returns to the worker queue as an ordinary pending title. The
  worker then saves a fresh picture for it like any other.
- **If it does not**: a still-broken thumbnail means the detection failed —
  say which screen you were on.

## 72. A FAILED DOWNLOAD LEAVES NOTHING BEHIND — v219

- **Hard to trigger on purpose** (it needs a download to die part-way), so
  treat this as a watch-item: if Diagnostics ever again shows a 0-byte file
  under "files on disk with no database record", that is this fix failing —
  say so. The Ostankino leftover from 2026-09-18 predates the fix and is
  cleaned separately.

## 73. THE K MARK ON WORKER IMAGES — v219

- **What to click**: open Worker Images on a day with pictures. Zoom into
  one and press **K** — the picture gets a green outline, a REVIEWED ✓
  pill, and its box in the grid and its title box turn green too. Press K
  again — it all clears. In the plain grid (no zoom), K marks the
  highlighted title, the one the ‹ › arrows stand on.
- **The count**: the line at the top ("N titles · M images total") now ends
  with "X NOT YET REVIEWED", falling as you press K, and "all reviewed ✓"
  when you finish.
- **The sort**: reviewed titles do NOT jump around as you press K. They
  sink to the bottom the next time the day loads or you touch the order
  dropdown — unreviewed ones then sit on top in your chosen order.
- **What it must NOT do**: nothing else changes — no flag, no approval, no
  pipeline movement. A worker replacing a marked picture clears its mark
  (you have not seen the new one).
- **If it does not**: say which of the three places (grid box, title box,
  zoom) missed the green, or whether the count disagreed with your eyes.

---

## 74. THE CHAT BADGE FINALLY SHOWS UP OUTSIDE CHAT — v220

- **The live test comes free**: the next time a worker messages you while
  you are anywhere else on the site, within ~12 seconds you should see the
  red count on the **Chat** row AND on the **People ▾** heading, plus a
  pop-up top-right reading "New chat message — open".
- **The pop-up now stays** until you click it (goes to the chat) or press
  its ✕. It also clears by itself once you have read the messages.
- **If a worker message ever again sits unseen** with no red count in the
  sidebar, say so — that is the exact failure this version fixes.

## 75. "SEEN" UNDER YOUR CHAT MESSAGES — v220

- **What to look for**: open Chat, pick the worker, look under the LAST
  message of yours that they have read — a small right-aligned "Seen HH:MM".
- **What it means**: the worker had the chat open after that message
  arrived. Messages below it, they have not seen yet.
- **It moves on its own** while the page is open: when the worker reads,
  the line jumps down to your newest message within a few seconds.
- **If it sits under the wrong message** or never appears although the
  worker clearly replied, say so.

---

## NOT ON THIS LIST, ON PURPOSE

**Re-importing the catalogue.** No longer needed at all — corrected
2026-09-17, when this note had gone stale. The database and
`IMPORT_titles.csv` now both hold exactly 83,882 places and are kept in
step directly: the Israel-area cut (369), the low-view and memorial cut
(3,743) and the 146 famous additions were each applied to the DATABASE by a
guarded script and to the CSV in the same sitting. Re-importing would
renumber everything and is the one move that must never happen.
