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

## 1. THE COLOUR BEHIND THE POSTER — Approve Artwork screen · v145

Every poster comes back see-through, because asking gpt-image-2 for a
transparent background is what makes it render the way he wants. A solid
colour has to go behind it before FineArtAmerica ever sees it.

**What to click**

- Open **Approve Artwork** with at least one processed image waiting.
- The worker's original photograph should be sitting to the LEFT of the
  poster. Check it is the right photograph for that place.
- Drag the colour box. The poster's background should change **while you
  drag**, with no waiting and no page reload.
- Press **EYEDROPPER**, then click a colour inside the poster itself. The
  background should become the colour you clicked.
- Press **RESET**. It should go back to black.
- Approve the image. The saved file should now have that colour baked in.

**Test Bangkok, or any poster with a hazy sky.** That is the one that goes
muddy on black and reads correctly on its own blue. If a hazy sky looks
identical on black and on blue, the see-through part is being thrown away
instead of blended, and the whole feature is doing nothing.

**What failure looks like**

- The colour changes but the saved file is still black — the choice is not
  reaching the server.
- The eyedropper picks a colour that looks nothing like where you clicked —
  it is sampling the raw see-through file instead of the finished picture.

---

## 2. RERUN AND DROPPED — Approve Artwork screen · v145

**What to click**

- Press **RERUN** on an image. It should go back to the processor and come
  back as a fresh attempt, without the worker being involved and without you
  paying again.
- Press **UNUSABLE**. A text box should appear asking why. Type a reason and
  save it.
- Check the reason is still there afterwards, on the title.

**Why this matters.** The worker has already been paid by the time you see
the picture, so neither button may ever send work back to him.

---

## 3. THE GOOGLE BUTTON — worker screen · v146

Brave's picture catalogue is thinner than Google's, so Google is the backstop
when the Brave grid comes back with nothing good.

**What to click**

- Claim a title as a worker, standing in Travel.
- Press **GOOGLE**. A new tab should open on Google Images with the place
  already searched, including its country.
- Copy a picture's address from Google.
- Paste it into the **paste-a-URL** box under the saved-image area.

**PASTE ONE ADDRESS BEFORE ANYTHING ELSE ON THIS LIST.** There is an open
question underneath it, in section 6.

**What failure looks like**

- Google opens on a blank or wrong search — the address template is wrong.
- The paste is refused — see section 6, this is the expected failure and I
  need the exact message it shows.

---

## 4. THE SEARCH WORDING — IMAGE SEARCH tab and worker screen · v144 and v146

This is his experiment, not a defect hunt. The wording cannot be reasoned
out; it has to be tried on real places.

**What to click**

- On the **IMAGE SEARCH** tab, put one phrasing per line in
  `brave_search_phrasings`. Each line becomes one more button on the worker's
  search bar.
- Use `{title}` for the place and `{kind}` for the description column. For
  example `{title} {kind} at sunset` searches "Kyoto Japan city at sunset".
- Press each button on the same place and compare what comes back.
- Try a place whose description column is EMPTY. The search should read
  cleanly, with no double space where the missing word was.

**What to watch for**

- Editing a phrasing and pressing its button should give NEW pictures, not
  the ones from before the edit.
- A phrasing with no `{title}` in it should be refused when you save it.

---

## 5. THE STYLE REFERENCE SWITCH AND THE BACKGROUND OPTION — v144 · v152

The Settings screen's IMAGE GENERATION panel now has a **Background**
choice (transparent / auto / opaque), set to transparent. Run one TEST
IMAGE GENERATION and check the result comes back with see-through areas
that flatten onto your chosen colour — that proves the parameter reaches
OpenAI.

**What to click for the style switch**

- Turn `openai_use_style_image` OFF and process one image. It should work
  with no reference picture at all.
- Turn it ON without uploading a reference. It should refuse clearly, saying
  a reference is wanted and missing.
- Turn it ON with a reference uploaded. Both pictures should go, with the
  reference first.

---

## 6. ~~THE OPEN QUESTION~~ — ANSWERED, no test needed

`AUDIT.md` already measured this on 2026-08-27: a blank `allowed_image_hosts`
means **any website is allowed**, which was chosen deliberately so nothing
changed on the day the setting was added. Only internal server addresses are
refused, and no real image lives at one. So pasting a Google address should
simply work. Item 3 still covers trying it for real.

---

## 7. THE EARNINGS PAGE AFTER THE TEEPUBLIC REMOVAL — v147

Everything TeePublic left the site, but your TeePublic account rows are
still in the database on purpose.

**What to click**

- Open the **Earnings** tab. It should load cleanly and show only
  FineArtAmerica figures.
- The TeePublic nav tab should be gone entirely.
- Check the **Nodes** tab reports agent **1.30.0** after you copy the
  `worker_service` folder to the Windows machine.

**What failure looks like**

- The Earnings page erroring or hanging — a TeePublic account row tripping
  code that no longer expects one. Send me the message if so.

---

## 8. THE NEW HOME PAGE AND PULSE STRIP — v148

**What to click**

- From the master dashboard, open the Travel project. You should land on a
  new **Home** page: a left-to-right strip of the whole pipeline with a
  count at each step, and "waiting on you" cards below it.
- Every number on that page is a link. Click a few and check each one lands
  on the screen that deals with that number.
- The thin **status strip** under the top bar should appear on every admin
  screen within a few seconds, showing the worker machine, workers online,
  and any red alarm lines.
- The master nav should now be five items. Open each dropdown. On your
  phone, the hamburger menu should show the groups as headed sections.
- **Changes Requested**, **Worker Images**, **Approve Artwork** and
  **Pipeline** should show small count badges when something is waiting.

**What failure looks like**

- The home page stuck on "Loading…" — the pulse endpoint is failing. Press
  F12, copy the red console line, send it to me.
- A count on the strip that disagrees with the screen it links to.

---

## 9. THE PIPELINE SPLIT — v148

The one Pipeline page is now three nav tabs: **Greenlight** (deciding),
**Pipeline** (watching: Overview, Needs Attention, Nodes) and **Settings**
(Image Search, Processing, Upload, Test & Debug).

**What to click**

- Open each of the three tabs and check the right sections appear on each.
- On **Pipeline**, click a number in the funnel. It should carry you to the
  **Greenlight** tab with the matching filter already applied.
- On **Settings**, change any value and save it, then reload — it should
  stick, exactly as before the split.
- In **Diagnostics**, click a finding that points at a settings section. It
  should land on the Settings tab with the right section open.

**What failure looks like**

- A section that shows the wrong content, or a button that does nothing —
  tell me which tab and which button.
- A settings form that comes up empty on the Settings tab.

---

## 10. THE NEW SKIN — v149

The whole site changes colour and shape: deep ink, violet accent, a left
sidebar on desktop, pill buttons, rounded cards.

**What to click**

- Look at five screens on your monitor: Home, Worker Images, Approve
  Artwork, Pipeline, and the master Dashboard. Anything unreadable, squashed
  or ugly — tell me which screen.
- Open the site on your **phone**. The sidebar should become the slide-out
  menu, exactly as before, and tables should scroll sideways rather than
  squeeze.
- Press the **moon/sun button** to try light mode. It was re-derived for the
  new palette and has never been looked at.
- Log out and check the **login page** looks right with no sidebar.

**What failure looks like**

- Text sitting on a background of nearly the same colour.
- Content hiding underneath the sidebar, or a page still styled in the old
  gold.

---

## 11. COMFORT AND POLISH — v151

**What to click**

- General look: the background should now read as dark grey, not black,
  and every input box should show its value clearly. Check the Listing
  check settings boxes — they were the worst offender.
- Open **Stats** for a worker. The numbers should be large gradient
  figures on raised cards, not flat text.
- **The chat toast**: sit on the dashboard as admin, send a chat message
  from the worker account (or ask the worker to). Within ~12 seconds a
  small violet notice should appear top-right — "New chat message — open"
  — and the chat badge should hop. Clicking it opens Chat. It should NOT
  appear while you are already on the chat page.

**What failure looks like**

- Any text you have to lean in to read — tell me the screen and the words.
- A toast that never appears, or appears on the chat page itself.

---

## 12. THE SPEED PASS AND THE AUDIT GUARDS — v153

**What to click**

- Browse a few pages, then open **Diagnostics**. The new **SLOWEST PAGES**
  panel should list them with their server milliseconds. Anything over
  500 ms shows red — tell me what.
- Second visit to any page should feel snappier: the styling files now
  come from your browser's cache instead of the server. (Press F12 →
  Network → the static files should say "memory cache" or "disk cache".)
- **The re-import**: when you re-import the catalogue with REPLACE, it
  should either succeed cleanly (no saved images yet) or REFUSE with a
  plain sentence naming how many saved images block it. A refusal with
  zero work in the system would be a bug — tell me.
- Run a Diagnostics scan: a new check called "Saved images whose title row
  is gone" should exist and report nothing.

---

## 13. THE THIRTEEN FIXES — v154 (retest of what you found)

Each of these is one of your own findings; check it does what you asked.

- Open a title as the worker: no 500 (if one still appears, run
  `cd /opt/poster && docker compose logs web --tail 100` and send the
  traceback — the hardening logs the true cause now).
- The outside-link button reads **Open Google image search**.
- The ALSO TRY chips read as plain words ("landscape"), tooltip shows the
  full wording.
- NOT RECEIVED asks before it reports; Cancel does nothing.
- Chat badges are red (worker sidebar and admin People button).
- Worker Images: click an image → title named large, ‹ › arrows and ← →
  keys walk the whole day, counter shows "3 / 10".
- Approve Artwork: big REVIEW EVERYTHING WAITING (n) button; equal-size
  side-by-side; click a picture → full-screen compare with arrows; the
  colour bar either works or says plainly why it cannot.
- JUST THE RERUNS shows 0 when nothing awaits, and opens fresh attempts
  when something does.
- "(N/A)" disappears after the re-import (or the cleanup command from
  chat) — new imports can never store it again.
- Home page: hovering "being processed" no longer overlaps the header.
- The in-flight explainer mentions no Photoshop.
- **Approve Artwork speed**: the second time you open the same range it
  should be near-instant, and the first time noticeably lighter than
  before. Recolour and eyedropper still work on the smaller preview.
- **On your phone**: tap a title — the screen should go to the work panel
  by itself. Scroll away: a floating "↓ TO THE TITLE" button appears; tap
  it to come back. It hides when no title is open.
- **v156**: the painted poster shows again on Approve Artwork (v155 blanked
  it); opening a title as the worker no longer 500s — the real cause is
  fixed, not just cushioned; Needs Attention header tick selects/unselects
  its whole table.

---

## 14. EVERY GENERATION IS KEPT, AND YOU CHOOSE — Approve Artwork · v163

This is the big one. Until now a RERUN wrote the new picture straight over
the old one, so the old picture was gone even though the record of it
stayed. Each generation now has its own file.

**What to click**

- Open **Approve Artwork** on a poster you are happy to experiment with.
  Press **RERUN**, then **SAVE & RELEASE** so the machine picks it up.
- Wait for the new picture, then open Approve Artwork again. Under the
  poster there should now be a row reading **generations · v1 · v2**.
- Click **v1**. The older picture should appear, with its own colour.
  Click **v2**. The newer one should come back. Press **1** and **2** on
  the keyboard — they should do the same thing.
- Settle on whichever you prefer and press **SAVE & RELEASE**. The one
  showing on screen is the one that goes to FineArtAmerica.
- Rerun a third time and check you can still reach v1.

**What failure looks like**

- Clicking **v1** shows the same picture as **v2**. That means the two
  generations are sharing one file, which is exactly the bug this fixes.
  Run a **Diagnostics** scan: a check called "Every generation has its own
  file" should report nothing for anything made after today.
- Old posters generated before today will show up in that Diagnostics
  check. That reading is correct and cannot be undone — their earlier
  pictures really were written over.

**Worth knowing.** Every generation now costs archive space: roughly a
4000-pixel print file plus its see-through original, per attempt. Nothing
is deleted automatically, on purpose, so tell me if the Storage Box starts
filling up faster than you want.

---

## 15. THE APPROVE ARTWORK REWORK — v163

**What to click**

- **The colour, without zooming.** Open a poster and drag the colour box on
  the CARD, not in the big view. The see-through parts of the poster should
  change colour as you drag. Until today they stayed near-black on the card
  and only looked right after you clicked into the big view — that is the
  thing being fixed, so this is the one to check first.
- **The keys.** With a poster on screen press **R**. It should mark RERUN,
  same as clicking the button. Press **R** again to unmark it. Then try
  **K** for keep, **U** for unusable, **C** to unmark, **E** for the
  eyedropper, **Z** for the big view.
- **The big view.** Press **Z**. The colour box, the eyedropper and the
  generation buttons should all be there, under the two pictures. Change
  the colour without leaving the big view — it should change immediately.
  Press **R** while still in the big view; the header should show RERUN.
- The list of keys is written under the buttons, so nothing has to be
  remembered.

**What failure looks like**

- A key that types into a box instead of acting — tell me which key and
  what you were clicked into at the time.
- The colour changing on the card but not in the big view, or the other way
  round. Both read the same value now, so they should never disagree.

---

## 16. THE PIPELINE PAGE OPENS WHERE YOU EXPECT — v163

**What to click**

- Click a red alarm line in the strip under the top bar. It takes you to
  the Pipeline page. Look at **Needs Attention**, then go somewhere else
  and come back to Pipeline from the menu.
- It should open on **OVERVIEW** every time. Before today it remembered the
  last section you had open and kept putting you back on Needs Attention
  for the rest of the day.
- A bookmarked address ending in `#attention` should still open Needs
  Attention directly, and the browser's Back button should still walk back
  through the sections.

---

## 17. THE LISTING CHECK LOG — v163

**What to click**

- On **Listing check**, press the start button. A new panel called **WHAT
  THE WORKER MACHINE IS DOING** should appear.
- While it is waiting it should say so in words, and say that the Windows
  machine does one job at a time. Once it starts it should show the
  machine's name, the percentage, when it last said something, and its
  actual log lines.
- Leave it running and watch: a new line should appear roughly every 30
  seconds.
- **To see the stuck case on purpose**: stop the agent on the Windows
  machine while a sweep is running. Within a few minutes the panel should
  say it has heard nothing and explain what that usually means. Start the
  agent again and it should pick up where it left off.

**Where it runs.** The Windows worker machine, never the Linux server.
FineArtAmerica refuses the server for these pages, even the public ones.
That is now printed on the panel so nobody goes looking on the wrong box.

---

## 18. THE WORKER SCREEN — v164

**What to click**

- Open a title, pick an image, press **SAVE SELECTED**. The button should
  keep its own name the whole time and go back to normal straight away. A
  small turning circle and the words "loading image…" should appear beside
  the SAVED IMAGES count, and disappear when the picture is there.
- Before saving anything, look at **DONE**. It should be greyed out and
  unclickable, and hovering it should say to use SKIP instead. Save an
  image; DONE should become pressable without reloading the page.
- Between DONE and SEARCH there should now be a gap with a strip in it
  reading **Subject: City** (or Mountain, Island, Castle…) with a small
  drawing. Open a few different titles and check the drawing changes and
  always matches the word.

**What failure looks like**

- The button still greyed out after saving — tell me and I will look at the
  spinner logic, not the button.
- A subject with a map-pin drawing rather than its own. That is the
  fallback, and it means the sheet used a word I do not have a drawing for.
  Tell me the word.

---

## 19. THE THUMBNAIL WARNING — v164

**What to click**

- Paste a normal Google image address. It should just save, with **no**
  warning at all. That warning used to appear on every single save and
  talked about a film database.
- To see it work on purpose, find a genuinely tiny image (under 300 pixels
  both ways) and paste it. The message should name the real size, for
  example "only 150 by 200 pixels", and let you save anyway.
- On the **Settings → Image Search** panel there is a new box, **Warn below
  this size (px)**, set to 300. Change it to 5000, save, and paste a normal
  picture — the warning should now appear. Put it back to 300 afterwards.

---

## 20. TIDYING UP AFTER A CHOICE — v164

**What to click**

- Take a poster with two or more generations. Choose one and press **SAVE &
  RELEASE**.
- Open the same date range again. The poster is gone from the queue, which
  is correct. On the **Storage Box**, the files for the generations you did
  NOT choose should no longer be there — only the one you kept.
- Run a **Diagnostics** scan. A check called "Every current picture still
  has its file" should report nothing.

**What failure looks like**

- That Diagnostics check reporting anything at all. Stop and tell me before
  uploading — it would mean the picture heading for FineArtAmerica has been
  deleted.

---

## 21. THE LISTING CHECK — v164

**What to click**

- Open **Listing check**. The panel **WHAT THE WORKER MACHINE IS DOING**
  should be there even with no sweep running, showing the last one and
  saying plainly that nothing is running now.
- Run a sweep and let it finish. Under **WHAT DOESN'T ADD UP** there should
  now be a section called **NOT CHECKED YET** listing by name anything we
  believe is live that no sweep has reached. If everything was reached, the
  section is absent, which is also correct.
- On any finding, press **I CHECKED IT — STOP ASKING** and write what you
  found. It should move to **YOU HAVE ALREADY DEALT WITH THESE**, with your
  note and the date beside it.
- Run the sweep again. That listing should NOT come back into the problem
  lists.
- Press **REPORT IT AGAIN** on it. It should return to the list above.

**The one worth understanding.** Your note is stored against the ANSWER the
marketplace gave, not against the listing. So if a listing you settled as
"gone, I checked, that is fine" ever starts loading again, it comes back on
its own and tells you. Nothing is hidden for ever and nothing needs
clearing.

---

## 22. THE COLOURED SIDEBAR — v166 (replaces the v165 version)

Each job is now a shaded BAND, the way you drew it, rather than a colour
that only showed up when you hovered.

**What to click**

- Look at the menu inside the Travel project on a monitor. There should be
  four shaded blocks with small headings: **FINDING PICTURES** in gold,
  **YOUR DECISIONS** in rose, **THE MACHINERY** in blue, **JUST LOOKING**
  in mint. Home sits on its own violet block above them.
- Each block should have a coloured line down its left edge and a small dot
  beside every link.
- Open a page. That row should fill with its band's colour and its dot
  should light up. The band's heading should brighten too.
- Go out to the master menu. **Money** gold, **Marketplace** blue,
  **People** rose, **System** mint, **Dashboard** violet — same blocks,
  same shape.
- Open the site on your **phone** and pull out the menu. Same bands, with a
  little space at the sides.
- Press the **moon/sun** button for light mode and look again. This is the
  one I am least sure about, because I cannot see a rendered page.

**What failure looks like**

- A band that is too dark or too heavy to read the labels through. Tell me
  which colour and I will take the wash down — every one is under a tenth
  opacity now, and it is easy to go lower.
- A grey row sitting between two coloured blocks. That means a link was
  left out of a band, and there is now a check that fails the deploy for
  exactly that, so it should be impossible.
- Bands that look like stripes rather than blocks. That would mean the
  spacing between them is too tight.

---

## 23. THE NEW STATUS STRIP — v165

The thin bar under the top of every admin screen.

**What to look at**

- It should now read as small bordered chips rather than plain text, with a
  quiet label and a bold figure in each.
- With nothing happening it should say **worker machine · on**, **doing
  now · nothing**, and **workers online · 1**. There should be a time in the
  far right corner.
- Greenlight some work. A **waiting** chip should appear in blue saying how
  many are queued to paint or upload, and **doing now** should start
  counting while the machine works.
- Hover any chip. A tooltip should explain in plain words what that figure
  means and where it is counted from.
- Every chip except the last two is a link. Clicking should land you on the
  screen that deals with it.

**The one that matters.** If the machine ever says **doing now · nothing**
while the **waiting** chip shows a number that does not move, something is
stuck. That pairing is the whole reason the strip changed — before this it
only said "idle", which looks the same on a finished day and on a jam.

**What failure looks like**

- The time in the corner stopping while the page is open. That means the
  strip has stopped asking, so every figure on it is stale.
- A chip that should be red showing grey.

---

## 24. THE SIGNATURE — v167

**Do this first, before anything else on this list.** Nothing is painted
until the file is uploaded.

**Setting it up**

- Go to **Pipeline → Settings → Processing**. There is a new **SIGNATURE**
  panel. Upload your transparent PNG. It should appear on a dark square, so
  you can see white strokes.
- Try uploading a JPEG on purpose. It should refuse and tell you why — a
  picture with no transparency would paint a solid box on every poster.
- The five boxes under it are the DEFAULTS: 16.8% wide, 0.5% gap, 35%
  opacity, position 91.1. Leave them.

**Judging it**

- Approve one poster and open the finished file from the Storage Box.
- On a 4000 × 6000 poster the mark should be **672 pixels wide, 20 from the
  right, 20 from the bottom**. I measured that here by building the same
  file with and without the mark and comparing them, so the numbers should
  land exactly — but I have never seen it on a real poster, and whether it
  LOOKS right is yours.

**Moving it**

- On **Approve Artwork**, drag the mark left and right with the mouse. It
  should move smoothly and refuse to go closer than the gap to either edge.
- Use the three sliders: across, size, solid.
- Press **B**, or the WHITE/BLACK button. On a pale poster the black version
  should be the readable one.
- Press **RESET**. It should go back to the defaults from the Settings page.
- Press **Z** for the big view. The mark and its sliders should be there too.
- Save, then open the file and check what you dragged is what got painted.

**What failure looks like**

- The mark in a different place in the file from where you dragged it. That
  would mean the preview and the builder disagree, which is the one thing
  the design is meant to make impossible — tell me straight away.
- A solid rectangle instead of your strokes. That means a picture with no
  transparency got through the check.

---

## 25. THE PRINT FILE IS BUILT LATER NOW — v167

This is the biggest change under the surface, and it is worth understanding
before you test it.

The machine used to make the 4000-pixel file the moment it painted a poster.
It does not any more. It saves the see-through original and the small
preview, and the big file is built when you press SAVE & RELEASE — with your
colour and your signature together, in one go.

**What to check**

- Let the machine paint something. Painting should feel a little quicker.
- On **Approve Artwork** the poster should look exactly as it always did.
  The preview comes from the flattened picture now rather than from the big
  file, which should make no visible difference.
- Approve it. Then look on the Storage Box: the 4000-pixel file should
  appear only at that point.
- Run a **Diagnostics** scan. A new check called "Every approved image has a
  print file" should report nothing.

**What failure looks like**

- That Diagnostics check reporting anything. Stop and tell me — it would
  mean something was released for upload with no file behind it.
- An error when you approve. That is the intended behaviour if the build
  fails, and it is meant to refuse rather than half-succeed. Send me the
  message.

---

## 26. SENDING WORK BACK TO THE START — v167, rebuilt in v169

The testing tool you asked for. **Greenlight tab, at the BOTTOM now**, directly
under the Title Browser, because it works off that browser's tickboxes.

**What to click**

- Scroll to the **TITLE BROWSER**. Choose a stage such as "Uploaded" or
  "Processed" and press **LOAD**. Tick a few rows.
- As you tick, the panel below should count them out loud — "3 titles ticked
  above". That is the thing the old COUNT button never told you.
- Press **SHOW ME WHAT THIS WOULD DELETE**. It should say how many pictures
  and how many painted versions would go, and change nothing. This is the
  button that used to give you a 500.
- Press **SEND BACK TO GREENLIGHT**. It shows the same numbers, warns about
  duplicate listings, and asks you to type `SEND BACK`. Type something else
  and it should cancel.
- Type it properly. Those titles should reappear in the Greenlight queue,
  with no painted versions and no upload record. Their worker photographs
  should be untouched. The browser reloads itself and the ticks clear.
- Try the same thing from the sticky bar at the top of the browser —
  **SEND BACK TO THE START**, next to GREENLIGHT SELECTED. It is the same
  action, put where your hand already is while scrolling a long list.
- Greenlight them again and let the machine repaint them.
- The three bulk options in **WHICH** still work without ticking anything:
  everything uploaded, everything painted but not uploaded, everything
  painted at all.

**Read this before using it on anything real.** If a title is genuinely live
on FineArtAmerica, delete it there first. Uploading the same title twice does
not fail — the marketplace quietly renames the second one to "Title #2", at
an address we never computed, and the listing check then reports the first
one as missing.

---

## 27. THE APPROVE SCREEN'S COUNTS AND SPEED — v167

**What to check**

- Mark three posters with **KEEP** and one with **RERUN**, then hover
  RELEASE WITHOUT FINISHING THE SKIM. It should say one fewer will be
  released, not four fewer. That number was wrong whenever KEEP was used.
- Press **SAVE & RELEASE** on a batch. It should now count out loud —
  "saving 5 of 40", "saving 10 of 40" — instead of sitting still.
- When it finishes it should say how many were released and, if any old
  generations were cleared, how many files were deleted from the archive.

**On KEEP, since you asked.** It does the same thing as not touching the
poster at all. It is a way to mark "I have looked at this one" while
skimming, and pressing it again unmarks it. Nothing else.

---

## NOT ON THIS LIST, ON PURPOSE

**Re-importing the catalogue.** The database still holds 88,970 places and
the files now hold 88,876 — 44 war and grave sites were cut, then 50 bare
country names (2026-09-06). A job to do, not a thing to test, so it lives
in `ROADMAP.md`.
