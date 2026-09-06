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

## 5. THE STYLE REFERENCE SWITCH — Pipeline settings · v144

**What to click**

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

## NOT ON THIS LIST, ON PURPOSE

**Re-importing the catalogue.** The database still holds 88,970 places and
the files now hold 88,926, because 44 war and grave sites were cut. That is a
job to do, not a thing to test, so it lives in `ROADMAP.md`.
