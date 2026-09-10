# Not yet deployed

## v177 + v178 — the five things from your afternoon on the test box

Both versions are waiting; deploy once and you get both.

### 1 · THE RESET WAS ABOUT TO DELETE YOUR SIGNATURE (v177)

`reset_workflow.py` deleted every folder under the workspace, including
`_signature/travel.png` and `_style/travel.png`. The SETTINGS naming those
files are not work, so a reset kept them — leaving a setting pointing at a
signature that no longer existed, and the poster builder REFUSES in that
state. The pipeline would have stopped on the first poster after the reset.

Fixed, and the script now prints what it kept. A preflight check enforces
the underscore convention that makes the skip correct.

### 2 · THE DELETE POPUP

The three reason buttons are gone, exactly as you asked. An ordinary delete
now asks once and offers REPLACE as a hint. **A deletion the ADMIN asked for
still takes a note**, because there a real person reads it.

### 3 · THE MOVIE WORDS — far more than the popup

Your instinct was right and the surface was bigger than either of us thought.
**Thirty-one sentences** across the admin screens still said "poster",
including your Activity Log labels and every payment message. All fixed.

Worse, the movie word was the **fallback in eleven places** — the database
column, the template layer, the project context, and six templates. The day
a value failed to arrive, the whole site would have said "poster" again. The
fallback everywhere is now "image", the one word true of every project.

A new preflight check fails the deploy on any dead niche's word appearing in
a sentence, and on the fallbacks drifting apart. It found all thirty-one.

### 4 · THE GREYED-OUT SEARCH

Not a phone bug. With one image per title, **every** title enters that state
the moment you save — so it was the normal case, not a corner. The cap was
right; the silence was the defect.

Now the grid says *"You already have your image for this title"* with a
**SWAP IT FOR ANOTHER** button beside it, and tapping a greyed picture says
why instead of doing nothing.

### 5 · WASHINGTON, D.C.

Worse than I first said. Linux keeps a trailing dot, Windows silently drops
it — so the server wrote a folder over SFTP that the node could not find.
Removed at the one place folder names are built. Runs of spaces are collapsed
there too, so a removed character cannot leave a hole.

### 6 · BRAVE (v178)

Measured by you: **Brave ignores the minus operator** — `caspian sea -map`
returned nothing but maps. So the exclusion is ours, applied to what comes
back, which costs nothing because every result already carries its own title.

- **Dropped**: results whose own title says map, flag, clipart, vector, icon,
  logo, stock photo, infographic, diagram or chart. A dashboard setting, and
  whole-words-only so Flagstaff and Mapungubwe survive.
- **Ranked**: results naming the whole place first, then those sharing a
  distinctive word, then the rest. Accents fold, so "San Ginés" finds
  "San Gines", and "New York City" outranks "York Minster".
- **Hidden with an escape**: *"14 more hidden that do not mention Kisumu —
  SHOW THEM"*. Nothing is thrown away for failing to name the place.
- **No backfill**, your decision. One search stays one call.

The grid now says what each filter did, so a thin set of results can be told
apart from a bad search.

---

**Deploy this.** The Windows node did NOT change, so nothing to copy and
`AGENT_VERSION` stays where it is.

**One new setting to look at** once it is live: Settings → Image search →
*Words that mean "not a photo of the place"*. It ships with a sensible list;
add to it as you see what comes through.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
