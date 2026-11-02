<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 5d. NAME THE INVARIANT, THEN MAKE IT A DIAGNOSTICS CHECK

**Standing instruction from the owner, 2026-08-24.** Every new mechanism
ships with at least one invariant check. Say so when you deliver it, so he
knows it happened.

**Why this rule exists, and it is worth being blunt.** The stage-counting
bug — one account finishing ended the stage and left 178 live listings
switched off — was written, tested and shipped in one session by the same
mind. The dispatch function had a heading in capitals saying **"ONE JOB PER
ACCOUNT"**, and the handler forty lines away ended the stage on the first
report. And the verification test made ONE run and reported ONE stage,
because that is how the author pictured it. **The test could not fail: it
encoded the same assumption as the bug.**

The owner found it by noticing a number going up instead of down.

A test double shares the blind spot too, when it is unfaithful in exactly
the property under test: a hand-rolled fake browser kept an element's old
classes after `className` was overwritten, so the simulation of the zoom
passed while the real page crashed on that precise behaviour (2026-09-23).
When a simulation passes and the real thing fails, list what the double
simplifies and ask which simplification the bug lives in. The same double
misled a SECOND time that day in the opposite direction: its
`innerHTML = ''` cleared nothing, so the zoom's buttons looked doubled and a
stale one was clicked — a false FAILURE. Write the list of simplifications
BEFORE trusting a double either way: removal, class writes, clearing.

So be honest about what each rung actually buys:

| | catches | fails when |
|---|---|---|
| a rule in this file | what you remember to check | you do not remember |
| a test | what you imagined going wrong | **it shares your blind spot** |
| **an invariant** | what nobody imagined | it was never stated |
| **a Diagnostics check** | it happening for real | nothing — it runs unattended |

Tests assert about FLOW, which is imagination. Invariants assert about
STATE, which is not. "No design should be switched off while no run is in
progress" would have gone red within minutes, automatically, with nobody
watching a log — and it needs no knowledge of how the bug happens.

**The question to ask of every action you build:** what must be true after
this, that could stop being true? Especially where the answer involves the
outside world — a listing switched off, an upload half-done, an account
paused, money owed. Those are the ones that cost real money while looking
fine.

**AN INVARIANT OVER OUR OWN DATA CANNOT CATCH OUR OWN DATA BEING WRONG,
and that is the blind spot the whole table above shares.** Every check on
the Diagnostics page compared our records with our records. So on 25 Aug a
reactivation recorded 80 designs switched back on, one of them was still
sitting on TeePublic's inactive tab, and nothing internal disagreed with
anything — because our single record of it said "done". The owner found it
by opening the store in a browser.

**TWO WRITERS STORING ONE FACT DIFFERENTLY NEED ONE READER.** The two
REJECT buttons save a send-back note in different fields — one appends it
to `comment`, the other writes only `admin_verdict` — and the Changes
Requested cards printed `comment` raw, so after a completion reject the
admin saw only his FIRST flag, never his newest instruction (owner,
2026-09-24). Unifying the WRITERS would have shown the worker the note
twice, because his screen reads both fields. The fix was ONE reader
(`flag_history` in `templating.py`) that derives the history from both,
drops repeats, and is the only thing any card prints. When storage cannot
change, make the read side singular. And then COUNT the readers: v230
fixed the cards and left the zoom printing the raw `comment`, so the card
showed the latest note while the zoom beside it showed the first — the
owner found it the day after deploy. "One reader" is only true once every
screen that shows the fact has been enumerated; `check_admin_flag_text_
uses_one_reader` now does that enumeration for every admin surface.
The same audit found the lookup twin of this shape: the zoom picked a
picture's card by FIRST MATCH on the page, and one picture can sit on two
cards, so clicking it in the second card got the first card's buttons. A
lookup by an id that can appear in several places must say WHICH place —
here, the one the person clicked.

**THE SAME BLIND SPOT SITS BETWEEN A RECORD AND THE FILE IT POINTS AT.**
Rerunning a poster kept the old `ProcessedImage` row and set
`is_current = 0`, and two separate comments said the rejected picture was
"superseded, never deleted". The filename had no generation number in it,
so the rerun wrote the new picture straight over the old one. The ROW
survived; the PICTURE did not. No invariant could see it, because the two
rows were perfectly consistent with each other and both correct about a
file that holds one image — and no screen ever asked for an old one, so
nothing looked wrong for weeks. It surfaced only when the owner asked to
CHOOSE between generations (2026-09-09), at which point picking v1 would
have shown him v2.

**AND THE OPPOSITE ERROR IS REAL TOO: SOMETIMES TWO COLUMNS ARE TWO FACTS.**
This rule is quoted so often that the reflex becomes "never store the same
thing twice", and that reflex would have shipped a silent bug on 2026-09-09.
The Approve Artwork screen needed to remember a colour the admin had picked
but not yet released. The obvious move was to write it into
`background_color` — and that column means "the colour already flattened
into the print file", which `_build_print_file` COMPARES AGAINST to decide
whether it has work to do. Writing a preference there would have told the
builder the job was done, and the old colour would have gone to the
marketplace with nothing on any screen saying so.

So `background_chosen` sits beside it, exactly as `signature_json` (chosen)
sits beside `signature_applied` (painted). **The test is not "are these two
values usually equal" — it is "does anything COMPARE them".** If something
does, they are two facts and the comparison is the feature; the duplication
rule does not apply. If nothing ever does, they are one fact stored twice and
it will drift. Ask which before merging two columns or splitting one.
The same test runs the other way: when the background grew a TOP and a
BOTTOM colour (2026-09-29), nothing ever compared one plate's top with
another's, only whole plates, so it stayed ONE value ("#top/#bottom") with
ONE spelling (`imagefetch.normalise_background`) — two columns would have
been two things every comparison had to remember to read together.

The reverse of that mistake is worth stating too, because the fix used it.
When the print file moved to approval time (2026-09-09) there was a choice
between a flag saying "not built yet" and simply leaving `storage_path`
EMPTY until a file exists. The empty path won: **a path is a promise that
something is there, so the absence of one is the honest way to say nothing
is.** A flag beside a path is two records of one fact, which is this very
rule wearing its usual clothes — and the emptiness is also what the rebuild
reads to decide whether it has work to do, so there is one thing to keep
correct rather than two.

And the mirror image, found while building step 2 (v266): approval DELETES
the losing generations' files, and two rows of one poster can name the SAME
path — an edit built at approval takes its family's print-file name, which
is its parent's own. Delete the loser's copy after building the winner and
the file just built is gone; for one of the 417 that path was the LIVE print
file. Approval now never deletes a path the kept row uses, and step-2
pictures get their own family of names (`X_step2.png` / `.jpg`). When a
clean-up deletes "the other rows' files", ask whether any of those paths is
also the survivor's.

Since v282 a step-2 title shows THREE pictures: photo, the step 1 painting
it came from (`step2_from`), and step 2 (owner, 2026-10-29). The middle one
is a plain `<img>` on purpose — no `data-poster-img`, no `data-zoom-open`,
no signature layer — because the colour plate, the signature preview and
the zoom all find "the picture being decided" by those hooks, and a second
picture carrying them would be recoloured, signed and zoomed as if it were
the one going live.

**A painting never goes live while step 2 is on** (owner, 2026-10-29). So
since v283 a picture with a step-2 version has its versions SPLIT:
`deciderVersions` (step 2 only) feeds the right-hand picture, KEEP, 1-9 and
V, and `shownVersion` can no longer land on a painting — before that,
clicking V4 and pressing KEEP released the painting. The paintings have
their own bar under the middle picture (`chosenPainting`), and REDO STEP 2
is sent on that painting. The buttons sit under the picture they redo
(UNUSABLE left, REPAINT middle, the rest right) but every one still sets
the picture's ONE mark. Watched by `tools/test_review_split.py`, which
lifts the helpers from the shipped file.

**Number pad 4 and 6 are the owner's MOVE, and a key setting must never
sit on them.** `PDKeys.matches` makes a digit setting match its number-pad
key too (so 7/8/9 work with Num Lock off), which means REDO STEP 2 on 6
(v266) took number pad 6 whenever Num Lock was on: "move right" marked a
redo. Since v284 REDO STEP 2 is 3 and the page moves on `e.code` Numpad6 /
Numpad4 BEFORE any decision key; the top-row 4 and 6 still pick versions.
v284 made the top-row 3 REDO STEP 2 too, so version 3 could not be picked;
since v287 a digit REDO STEP 2 key answers on the number pad only. And a
key setting equal to another key on its screen silently takes that key's
press, so `pipeline.key_clash` refuses it — checked over the WHOLE save
after it is written (a swap must go through, and so must the save that
fixes a clash already stored), never key by key. Watched by
`tools/test_review_keys.py`, which runs the page's own keydown handler with
Num Lock on and off, and `tools/test_key_clash.py`.

**Every mark has its own outline colour, and the tally is at the top.**
REDO STEP 2 (v266) was added to `MARK_WORD` with no `.review-img.is-step2`
rule, so a redo looked exactly like an unmarked picture. Since v285 it is
magenta (border and `.btn-step2`), and the tally ("N will be released …
N NOT MARKED YET", the unmarked count in red) is the first line of the
review panel in 16px instead of 11px print by UNMARK THIS TITLE (owner,
2026-10-29). `tools/test_review_tally.py` fails if any mark in `MARK_WORD`
lacks a colour or two marks share one, and runs the shipped counting
functions — until v287 it stubbed them with fixed numbers, so it tested
the wording and never the counts.

**SKY GRADIENT (v288) is a choice for ONE redo, so every redo writes it.**
The tick sends a painting's next step 2 with `step2_prompt_sky` instead of
`step2_prompt`. It travels as `sky` on the REDO STEP 2 decision and is
stored on the PAINTING as `ProcessedImage.step2_sky`. SAVE sets that column
on every REDO STEP 2, to 0 when unticked, because a flag that is only ever
SET follows the painting into every later redo. The batch line and the live
call both read it through `request_fields(..., sky=)`, where `sky` is
required with no default. The step 2 row copies it, which is the "· sky"
label. A blank sky prompt is refused at SAVE before any decision is saved,
and per picture after that, never swapped for the normal prompt. Its message
avoids the words "prompt is empty", which `_send` reads as "stop every
send". Watched by `tools/test_step2_sky.py`, which was broken five ways on
purpose and went red each time.

So: **keeping the record is not keeping the thing.** Whenever you write to
a path built from a rule rather than from a unique key, ask what else in the
database claims that same path. Fixed at the source — the generation number
is in the filename now — and watched by `generations_share_a_file` in
`diagnostics.py`, which compares rows against each other's PATHS rather than
against their contents. The tell to remember: a deterministic filename plus
a table that keeps history is a contradiction, and `review_cache.clear()`
existing at all was the visible symptom of it nobody read.

The fix is not a cleverer invariant. It is **one number from outside**, and
there is usually one available for nothing:

  * FineArtAmerica prints a Current Balance, so our sales minus our payouts
    has something to land on.
  * TeePublic prints how many designs are switched off, so a switching turn
    has something to be checked against.

Two rules for using one, both learned immediately:

  * **Compare a CHANGE, never a total.** One account holds 379 designs the
    owner switched off himself; their total and ours are not supposed to
    match and never will. What belongs to us is how their number moved
    across our turn. The first draft of the screen coloured the row red
    whenever the two totals differed, which would have flagged every
    healthy account for ever.
  * **"They disagree" and "we could not read it" are different answers.**
    Same shape as 410 vs 404 and as the header-logo test. Collapsing them
    invents findings out of a failed page load.

And **the outside number needs its own watcher**, because it silently stops
working if the site moves it: TeePublic's `check_count_check_is_running`
reported turns that produced no reading at all. (That check was deleted with
TeePublic in v147 — the rule is what stays: a cross-check that has quietly
gone blind reports agreement for ever, so give any new outside number its own
watcher on day one.)

So for anything that changes the outside world, ask: **what does the far
side already tell us, for free, that we could be held to?**

**Write it into `diagnostics.py`, not into a comment.** That module is
read-only by design and already runs on demand; a check there is the only
form of vigilance that survives this session ending.

This also reframes the INTERACTION AUDIT (planned item 8). Tracing every
interaction is an act of imagination and will inherit whoever's blind spots
wrote it. Naming every invariant is finite, and each one holds whether or
not the failure path was anticipated. Do both — but the invariants are the
part that actually protects him.

