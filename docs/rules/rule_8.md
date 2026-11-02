<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 8. Claimed work must always end in a reported state

Anything that takes work off a queue owns it until it says what happened. The
pipeline is a chain of claims — a node claims images, marks them 'processing'
or 'uploading', and the server waits. Silence is not a state.

So for any claim/report pair, check all THREE exits, not just the happy one:

1. it succeeded → reported
2. it failed in a way you anticipated → reported
3. **it threw somewhere you did not wrap** → reported, or the item is
   stranded and nothing will ever say why

Number 3 is the one that keeps happening, and it has a signature: work that
is claimed BEFORE the risky setup step. `run_batch` claimed a whole batch,
then called `start()` and `login()` outside any handler. Chrome failing to
launch left every item at 'uploading' with the reason written only to a
console on an unattended VPS. The reaper released them, the next cycle
claimed them, and it stranded them again — forever, looking alive the whole
time. The mirror image: **a SETTING that fails every item alike is checked
once, before anything is claimed, never recorded as each item's fault.**
A missing style picture was raised per photo while building a send, so
every photo of it was marked failed for good (attempts 999) for one
missing file (site audit, 2026-10-29). Since v279
`gpt_images.settings_problem` is asked first by the batch send and the
one-at-a-time loop; while it answers, nothing is claimed, the reason is on
the AI panel, and `check_style_picture_is_there` is red.

**A MARK THAT DECIDES WHETHER WORK IS PICKED UP IS SPENT WHEN THE WORK
ENDS, NEVER WHEN IT IS TAKEN.** With step 1 off, `paint_requested_at` (the
"named by hand" mark) is the only thing that lets a picture be painted.
v266 cleared it at the claim — the live call and the batch claim both. So
every exit that is not "painted" — a temporary error, a refused or stopped
batch, a restart, RELEASE — put the picture back in the queue WITHOUT the
mark, where it waited silently until step 1 was switched on, while the
screen said it "went back in the queue". Found by the v267 site audit; the
95 pictures of TO_TEST 113 would have hit it the first time OpenAI refused
a batch. Fixed in v268: the mark is cleared only in `file_painting()` and
at a FINAL `record_paint_failure()`; preflight's "hand-named mark ends with
the painting" fails on any other place that clears it. The test to apply:
**list the exits that lead back to the queue, and ask what each one leaves
the item carrying.**

**Catching an exception into a COUNTER is the same defect wearing a third
hat.** The store scan wrapped each account in try/except, added 1 to
`errors`, and then reported the stage finished regardless. A missing library
therefore produced a job that logged one red line, said "Job finished", and
advanced the run to "waiting for you" with zero designs checked — so the
screen would have said "nothing is missing" and been believed. A count is
not a report. If a thing that was supposed to happen did not, the caller
must be TOLD, and any state machine downstream must not move on.

**And a REPLY that is ignored is the same defect wearing a different hat.**
The earnings read posted each page to the server and read one key out of the
answer — "is there another page" — discarding the error and the stored count.
So a page the server could not parse produced a run that fetched, stored
nothing, and reported "Job finished". The screen stayed empty and no line
anywhere connected the two. If you asked something a question, say what it
answered.

**And when SEVERAL workers share a stage, one of them finishing is not the
stage finishing.** Deactivation runs one job per account. The first account
to report ended the stage for all of them: the run advanced to reactivating
while a second account was still switching designs OFF, so those designs
were never on the reactivate list — live listings left hidden, earning
nothing, with nothing on screen saying so. The test to apply: **if this
work is split N ways, does the state machine know N?**

**And the better answer to that question is usually "do not split it, and
do not count".** Counting the workers in and out was the first fix and it
worked, but a counter has to be kept correct by every path that touches it,
and there are always more paths than you listed. The version that replaced
it asks the CATALOGUE instead — is there any account with work left? — and
sends one account at a time. Nothing to increment, nothing to lose, and a
node that dies cannot leave a total unreachable for ever. **Prefer a
condition you can DERIVE over a number you have to MAINTAIN**; same reason
the quiet window is a window rather than a switch, and `scan_incomplete` a
query rather than a flag.

**And the same preference decides where a SCREEN opens.** The Pipeline page
saved the last section you looked at and restored it. That sounds harmless
and was not: a red alarm in the status strip links to that page, clicking
it put you on NEEDS ATTENTION, and from then on every visit to Pipeline
opened Needs Attention for the rest of the browser session — whatever you
had actually come to do. The owner: *"when I click it should not auto take
me to Needs Attention. It keeps doing this when there is an alert. Just
remove that mechanism."* Removed 2026-09-09.

The shape, and it fires far beyond navigation: **remembered state cannot
tell "I chose this" from "something put me here", so it repeats the second
one for ever.** The derived version was already there and cost nothing —
the address bar. A hash is set only by something the person did, it can be
bookmarked, shared and gone back from, and it forgets by itself.

**AN ACKNOWLEDGEMENT BELONGS TO THE OBSERVATION IT ANSWERED, NEVER TO THE
ROW.** The listing check's LEAVE IT button wrote a sentence into
`removed_reason` and changed nothing else, so the next sweep asked the
marketplace the same question, got the same answer, and put the row back on
the list — every sweep, for ever. The owner could not finish with anything,
which is how a findings list stops being read and a real takedown sits in
it unnoticed.

The fix is not a "hide this for ever" flag, because that is a second edge
somebody has to remember to clear, and a listing that is hidden while it
quietly changes is exactly what this feature exists to catch. What is
stored instead is WHICH ANSWER was acknowledged
(`UploadTracking.listing_ack_status`). While the marketplace keeps saying
the same thing the row stays quiet; the day it says something different,
the row reappears on its own with nothing to clear and nothing to expire.

Generally: **when you silence something, store what you silenced, not the
silence.** Same shape as the quiet window being a window, and as
`scan_incomplete` being a query. The same defect shipped a second time on
the SKIPPED titles: the home page counted every skipped title as "waiting
on you" and the only button was SEND BACK, so a skip the owner had read and
accepted raised the count for ever (his find, 2026-09-10). Fixed with the
same design — `skip_acked_at` compared against `skipped_at`, one spelling
of the comparison shared by the count and the page. **The test to run on
any "waiting on you" number: for each thing it counts, name the action that
makes it stop counting.** If the only actions are "do the work" and
nothing, the count is a treadmill, not a to-do list.

**AN IMPLICIT DECISION MUST RANGE OVER WHAT WAS SEEN, NEVER OVER A LIST
THAT CAN GROW.** The review screen's SAVE approves every unmarked image
in the loaded batch — safe while the batch was frozen at load. Then
mid-sitting reloads arrived (an edit saves, a version is deleted, the
page refreshes and resumes), each one re-fetching the queue — and a
rerun that finished painting meanwhile slipped into the batch behind the
owner's position. SAVE silently approved and UPLOADED repaints he had
never looked at (his find, 2026-09-14). The fix: the screen records
which titles were actually RENDERED, and silence only means approval for
those; an unseen arrival is not sent at all and keeps waiting. The
general shape: wherever absence-of-a-mark carries meaning, pin down the
set it ranges over — any mechanism that can add members between the
person's look and their commit turns their silence into a decision they
never made. **And the strongest fix is to stop silence meaning anything.**
On 2026-09-28 the owner asked for exactly that: working in batches, one
forgotten picture could still go to the marketplace, so v244 removed
approve-by-default from Approve Artwork altogether — every picture needs a
mark, SAVE & RELEASE refuses a batch with a bare one and jumps to it, and
the rendered-titles patch went with the rule it patched. When a patch
keeps an implicit decision safe, ask whether the decision should be
implicit at all.

**A MARK KEPT ONLY IN THE BROWSER CAN DECORATE A LIST, BUT IT CANNOT
SHORTEN ONE.** v236's LEAVE FOR PHOTOSHOP lived in localStorage, so the
server still counted those pictures as waiting — and REVIEW NEXT 20 is the
server's first 20. With twenty left for editing, every batch loaded the
same twenty and no new work could be reached (owner, 2026-09-27). v238
stores it as `review_status = 'held'`, with its own door. The test: if a
mark is meant to change what the server HANDS OUT — a batch, a count, a
queue — it must be stored where the server reads, or it only changes the
paint on the thing being handed out. And the same door-by-door care
applies as ever: a new status is invisible to every query that lists the
old ones, so `grep review_status` and decide each hit (keeping or
rerunning a sibling now settles a held one; a Photopea edit of a held
picture stays held; the badge deliberately still counts `pending` only).
The same held true for a MARK beside a status: PROMPT TEST MODE's pile was
`greenlit` plus `rerun_hold_at` (v249), and five counts of "waiting to be
painted" — the status strip, the node banner, the worker-not-running alarm
— would have read the pile as a stuck machine until each learned the mark.
And a switch that must stop work arriving by several doors (RERUN, RETRY,
RETURN TO PIPELINE) belongs where the work is TAKEN, not at each door: the
pile was filled in the painter's claim, the one place every repaint passes.
(The mode was REMOVED in v264, 2026-10-23 — prompts are now tested on the
laptop, `../site_prompt_tester`. Removing it meant un-teaching those same
five counts, which is the cost of a mark: every reader that learned it
must forget it too. The dead columns and tables stay in old databases,
unread, so a picture still wearing a pile mark simply paints.)

**A GATE MUST ASK ABOUT THE FACT IT PROTECTS, NOT ABOUT THE MECHANISM
THAT USUALLY CARRIES IT.** The worker's DONE held a title for review only
while a FLAG was open — standing in for "has the owner seen what this title
now holds?". But a flag also closes when the worker deletes the picture, and
the owner acknowledging the deletion cleared the last trace: the new picture
that followed completed silently, filed under the title's first day on
Worker Images, and could be painted unseen (owner, 2026-09-27). The fix asks
the fact itself — `utils.pictures_awaiting_your_look` — so every way the
mechanism can close (delete, acknowledge, SEND BACK, reopen-and-swap,
reopen-and-add) is covered by one question, watched by
`check_finished_titles_hold_no_unseen_replacement`. Before trusting a gate,
list every way its condition can become false and ask whether each one
means the thing it guards is really done. **And the list of "ways the
owner has already acted" is itself a list that must be complete.** v237's
version matched the WORKER's deletion wording only, so Atlanta — flagged,
deleted by the ADMIN, completion rejected three times, redone — finished
without his look (found 2026-09-29, while he checked why four pictures
were payable). The Diagnostics watcher could not see it either, because
it picked its candidate titles with its own copy of the same narrow rule:
a watcher that shares the definition it watches shares its holes. The
marks are now any flagged picture taken off the title, by anyone, and a
stored `completion_rejected_at`; the watcher reads the same marks.
**And a gate protects EVERY step that follows, not just the one it was
built beside.** The look gate held DONE, but payment never asked it, so
those same four redos sat in "everything before this week" as payable —
and nothing stopped a title being paid a second time for its replacement
(owner, 2026-09-29: "a title is never paid twice"). Payment now asks
`payments.unpayable_reasons`, which reads the look gate in bulk
(`utils.awaiting_your_look_by_title`) and the per-title limit. Building it
turned up a THIRD copy of the pay rules, in the worker's own history, which
lacked both the admin-added exclusion and retire-with-pay — so a worker
could be shown money the run would never send. Every screen that counts
money now asks the one function; `check_titles_are_paid_once` reads the
payment runs themselves. When a gate is added, list every later step that
acts on what it guards (paying, painting, uploading) and ask whether each
one consults it. **And money sent AHEAD of the gate must be recorded
against everything it paid for, not only what had passed.** The owner paid
for two days before checking them; the record held only the 116 good
pictures, so each later fix came back as "unpaid" and he typed custom
amounts by hand (Padua, 2026-10-12). A PAY AHEAD run now covers every
picture on its days (`payments.advance_poster_ids`), the existing "paid" and
"title paid" rules do the rest, and `check_paid_ahead_pictures_settled`
lists money that bought a picture later removed without pay. **And money
taken BACK must stop counting as paid.** A deducted picture still sat in
its run's list, so a redo two weeks later was "title already paid" and the
worker was never paid for it (traced 2026-10-26 while designing the K
rule); `payments.deducted_advance_ids` now leaves it out of every "is this
title paid" count, and `check_titles_are_paid_once` reads the same.
**And money taken back is recorded only as far as the payment covered
it.** The box took the whole debt off, the amount stopped at 0 (a
`Math.max`), and every owed picture was recorded as repaid — a debt
bigger than the week's pay was partly forgiven, silently (site audit,
2026-10-29). Since v272 MARK PAID settles owed pictures in the box's order
only while they fit the run's own value; the rest stays owed. The page
(`settleNow`) and the server compute the same list, and
`check_deductions_fit_their_payment` reads the runs. The general shape: a
clamp that hides a remainder must also keep the remainder somewhere. A
settlement also DEPENDS on the payment that paid ahead: deleting that one
left the later run charging for money never sent, so since v276
`payments_delete_run` refuses until the settling run is deleted first,
and `check_settled_pictures_were_paid` warns on a settled picture no run
paid. Undo payments newest first.
**Since v265 the gate is the fact itself: painting follows the owner's K
mark, not the payment** — "not flagged" had been standing in for "looked
at", which is the mechanism-for-the-fact shape this whole rule is about.
One query decides (`pipeline.pictures_ready_to_paint`), run by payment and
by a minute loop, watched by `check_kept_pictures_reach_painting`.

**A RULE THAT LEAVES SOMETHING OUT OF ONE STEP LEAVES IT OUT OF EVERY STEP
THAT STEP STARTS.** The admin's own pictures were rightly never paid for —
and painting was started BY payment, so the old + ADD box's pictures were
never painted either, unless something else on the same title happened to
be paid. Nothing was broken in any single line; the exclusion simply
travelled down a trigger nobody listed (traced 2026-09-27, while replacing
that box with USE MY OWN PICTURE, which sends its pick to painting itself).
When a thing is exempted from a step, list what that step SETS OFF and give
the exempt thing its own way to each of them. Watched by
`check_admin_picks_reach_painting` — which caught the next hole itself on
2026-10-09: USE MY OWN PICTURE set the title to "complete" IN MEMORY and
then called `greenlight_titles`, whose query filters on "complete" IN THE
DATABASE. The session runs with `autoflush=False`, so 16 of 38 picks (every
one on a title not already finished) were never sent. **A function that
QUERIES by a state its caller may just have set must flush first** — the
fix lives at the top of `greenlight_titles`, so every caller is covered,
and `send_stranded_admin_picks_to_painting` repairs the rows already left
behind at startup. The same box also saved the admin's
picture under the WORKER's user_id, so the worker's delete button accepted
it; v239 saves it under the admin's own id, which makes the wrong owner
impossible rather than guarded (rule 5), and keeps a guard only for the
title-level doors that never look at owners.

**Creating all the work up front is what makes stopping impossible.** The
five deactivation jobs — one per account — were queued together, so
STOP THIS RUN ended the run, released the pipeline, and did nothing at all
to them: the node claimed the next one and switched live listings off for
two more hours while the tab read "abandoned". Before queueing N pieces of
work, ask what CANCELS them, and whether the person pressing stop can reach
every one. One at a time costs nothing when the worker is serial anyway.

**"The job ended" is not "the work finished", and only the worker knows
which.** Pausing the store scan ended its job cleanly — every design
reported, no failure — which the server read as a completed scan and
advanced the run to the review gate. On an automatic run the next step
would have been deactivating 102 designs off a sweep that had covered 199
of 1,543. So the node now says whether it was cut short, and the server
also re-derives it from the data: are there designs this run never looked
at? Deriving it repaired a run already stuck in the wrong stage, which a
flag could not have done.

**The same reply is also how a long job gets STOPPED.** The store scan posted
one result per design and threw the answer away, so pressing STOP ended the
run on screen while the node cheerfully scanned for another twenty minutes.
A node cannot hear a button; it can only hear an answer to something it was
already asking. Before adding a polling endpoint for "should I stop", check
whether the work already talks to the server on a loop — it almost always
does, and the answer rides along for free.

**Fixing that in ONE loop is not fixing it.** The scan learned to read the
reply; the deactivate and reactivate loops kept discarding theirs, so PAUSE
worked on the stage that took hours and did nothing on the two stages that
switch live listings — the ones where continuing costs money. It looked
fixed from the screen, and the gap only showed up when a run had to be
stopped at the wrong moment. **When a mechanism has several instances, list
them and say which ones you changed**; "the loop now checks" is a claim
about one loop. The same shape struck the server's own PAUSE (found
2026-09-11): `intake_open()` said in its docstring "one question asked in
one place, by every stage" — and the JOBS queue was a hand-out stage that
never asked, so a "paused" machine still picked up earnings reads and
listing sweeps. The list of instances must come from asking **"every path
that hands out work"**, not from the paths the docstring remembers; a
docstring claiming full coverage is prose asserting an invariant, the
softest evidence in the repo. (The jobs queue is deliberately gated by
`machine_paused_on_purpose()` rather than `intake_open()` — the quiet
window closes intake precisely so the nightly earnings read can run, and
that read arrives through the jobs queue. One gate per intention.)

**And a GUARD is the same defect wearing its worst hat, because a missing
guard looks like nothing at all.** Switching a design OFF checked for the
interstitial wall on every page; switching one back ON had never been taught
about it. On 25 Aug the wall arrived partway through a reactivation and 79
designs in a row loaded a wall, found no publish button, and were written
down as broken DESIGNS — three seconds each against twenty for real work,
and the stage then reported "Job finished".

Two things in that are worth carrying past this bug:

  * **The give-up guard could not fire.** It counts failures marked "this
    was the wall", and the mark is set inside the check that was never
    called. A live protection, correct on its own terms, structurally
    incapable of triggering. Whenever a guard depends on a flag, ask who
    sets the flag and whether every path reaches them.
  * **"Whose fault was it" decides whether money is lost.** A failure caused
    by something in the WAY says nothing about the thing you were acting on,
    and recording it against that thing takes it out of the work list. Here
    it would have left live listings hidden while the screen blamed healthy
    designs. Blocked and broken are different answers; a system that has
    only one of them will use it for both.

**The same holds for a LESSON applied at one door.** The replace flow had
learned "the new picture arrives before the old one leaves" in September;
its twin, the search-grid swap, still deleted the old file first — so a
failed or refused swap rolled the record back to live with its file gone
(found 2026-09-27 while adding the same-picture check). And the only
duplicate guard lived in the paste box alone, comparing addresses, so the
same file reached two titles through the grid. When a door learns a rule,
list its sibling doors and teach them all in the same edit. The LIMIT was
the next lesson to stay at one door: the grid swapped when a title already
had its picture, while the paste box asked "Save another?" and added a
second on OK — 100 travel titles held 102 images (owner, 2026-10-09). Both
doors now call `_image_limit` and `_stand_down_for_swap`, and a GUARDED row
fails any new save door that does not ask the limit. **And a limit asked
BEFORE a slow step is a minute old after it**: five titles still held two,
one PASTED and one GOOGLE, the second file named "2" while the first row
was not yet saved — a slow paste and a phone send racing, both counting
zero. `_limit_reached_meanwhile` asks again just before the row is
written, and has its own GUARDED row. Three admin
doors take pictures off a title — RETIRE, the admin DELETE and USE MY OWN
PICTURE — and by v239 retire had forgotten the paintings (a withdrawn
picture's painting stayed waiting on Approve Artwork) while DELETE had
forgotten the queued uploads and the "is it being painted" check. They now
share one `_withdraw_pictures` and one `_picture_cannot_leave`, and a
GUARDED row makes any new caller ask.
The screen twin: flagging on Worker Images learned "update the one card,
never reload the day" on 2026-09-15, but USE MY OWN PICTURE on the same
screen kept calling the full reload — blanking the gallery, re-sorting the
picked title to the bottom and shutting the zoom — until the owner found it
on 2026-10-09 (v250, `refreshTitleInPlace`). When one control on a screen
stops reloading, grep the same file for every other `loadList()`.

Now enforced mechanically: `preflight.py` has a GUARDED table — (file glob,
the risky call, the calls that protect it) — so a new function that
navigates the browser without consulting the wall, or looks a title up by
`external_id` without scoping it to a project, fails before deploy. Add a
row whenever a protective call has to accompany a risky one, and use a GLOB
rather than a filename: a rule usually belongs to a kind of code, and a list
of files is another thing somebody has to remember to extend.

**A BUSY STATE ON A SCREEN IS A CLAIM, AND IT NEEDS THE SAME THREE EXITS.**
SAVE SELECTED disabled itself, renamed itself to "LOADING IMAGE…" while the
thumbnail downloaded, and was never re-enabled on the SUCCESS path — so the
button the worker had just pressed sat greyed out wearing different words
until something unrelated re-rendered the box. The failure path had been
thought about; the happy one had not, which is the same blind spot as rule 8
one level up.

Two rules came out of it, and both generalise past this button:

  * **Whatever enters a busy state must leave it on every path, including
    the one where nothing went wrong.** Put the exit in a `finally` if there
    is any branching at all. **And while it is busy, a second press must do
    nothing.** Chat locked only the text box while sending; the SEND button
    and Enter still worked, nothing showed for seconds on a slow link, and
    the worker sent one message six times while the owner sent one image
    three times (2026-10-18). A send needs an in-flight guard, a visible
    "Sending…" where the message will land, and a server that drops an
    exact repeat (`chat._is_repeat`) — the page guard covers one client,
    the server covers all of them.
  * **Show progress NEXT TO THE THING THAT IS CHANGING, not on the control
    that started it.** The picture was arriving in the saved-images panel,
    so that is where the spinner belongs. A control that renames itself
    mid-press is a control the person can no longer identify, and the owner
    called it "finicky" for exactly that reason (2026-09-09).
  * **The feedback comes BEFORE the slow step, never after it — and a
    control that opens a view must open that view FIRST, then load into
    it.** "Edit in Photopea" downloaded a several-megabyte picture and only
    THEN revealed the editor overlay, so on a slow link the button looked
    dead for seconds. Worse, while it looked dead the owner stepped to the
    next image and clicked again, and the click that finally landed opened
    the image he had ORIGINALLY clicked — because the handler had captured
    that image up front (2026-09-17). Two shapes generalise. First, if a
    handler does a slow thing before showing anything, put the container and
    its spinner on screen at the very start; a full-screen overlay also
    stops the person navigating onto a different target mid-load. Second,
    when a slow action captures WHICH thing it acts on at click time, show
    that thing at once so what the person sees cannot drift from what will
    happen — same family as "an implicit decision must range over what was
    seen". And overlap independent waits (here the editor boot ran AFTER the
    download; booting them together makes the wait the longer of the two,
    not their sum). The RESPONSE side of the same capture is just as real:
    when the slow answer finally arrives, it must check whether the person
    is still looking at that thing before repainting the screen around it.
    The zoom's FLAG handler re-opened the zoom on ITS poster when the
    server replied — so an admin who had already arrowed on was yanked back,
    and the previous flag's comment read as the newest one (owner's
    long-standing report, root-caused 2026-09-23; nothing mechanical could
    have caught a race only a slow network shows). Guard every late
    repaint with "is this still what is on screen?".

**AND A CONTROL THAT REFUSES MUST SAY SO — SILENCE IS INDISTINGUISHABLE FROM
A DEAD PAGE.** The search grid greys out every picture once a title has its
full complement of images and the click handler returns without doing
anything. The cap is correct. But `images_per_title` is ONE for travel, so
**every** title enters that state the moment the worker saves — it is the
normal case, not a corner — and the only clue was a small line reading
"0 selected · 1/1 saved". The owner met it on his phone and read it as
broken: *"the images are there but greyed out, I click them and nothing
happens"* (2026-09-10).

Two questions to ask of any control that can refuse:

  * **How often will it refuse when nothing is wrong?** If the answer is
    "every time, after the first success", the refusal is the normal state
    and it needs a sentence, not a subtle style.
  * **What is the way out, and is it on the same screen?** Swapping the
    image meant leaving the grid, finding the saved-images panel and
    deleting from there. A refusal that does not name its own remedy sends
    the person hunting.

Same family as a status that is true and answers nothing, one level down: a
count is not an explanation, and a greyed control is not a reason.

**AN EARLY EXIT WRITTEN FOR THE OLD JOB WILL SILENTLY SKIP THE NEW ONE.**
`upscale_to_width` began with "if it is already wide enough, return" — right,
when its only job was enlarging. Adding the signature gave the same function
a SECOND job, and that exit would have returned before painting: a poster
that happened to arrive at print size already would have shipped unsigned,
with nothing anywhere saying so.

So whenever a function gains a responsibility, **read its early returns
again and ask what each one now skips.** The condition that made a shortcut
free is a statement about the old job, not the new one. Same family as
moving a call out of a shared function (3c) — the code that did not change
is where this hides.

Concretely: **an exception must never be able to escape past the point where
the work was claimed.** If setup can fail, either claim after it, or catch it
and report the claim as failed with the real error text. And an error that is
only written to the node's local log does not exist — the owner cannot read
that machine.

