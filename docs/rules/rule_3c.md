<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3c. Check the FILE, not your intention

**RUN `python tools/preflight.py` BEFORE SAYING ANYTHING IS DONE.** It is
every mechanical check in this section, automated, and the deploy tool runs
it too. Each one is there because it caught something real: a `log_activity`
call with wrong argument names, a missing dictionary key, a renamed `data-`
hook that silently killed a panel, an orphan closing tag that reparented a
modal into a hidden section, a settings key absent from DEFAULTS.

`--map` prints every button on every screen and what it calls — the
enumeration rule 1 asks for, done mechanically rather than from memory.

**A check too slow to finish is a check that stops being run.** On
2026-09-27 four new GUARDED rows pushed the guard check to 133 seconds
(it re-split each file for every function, for every row) and the whole
preflight past the time a session's command may run, so it died silently
with no verdict. Each file is now parsed once per run: 2 seconds. When a
check grows with the number of rules, make sure it does not grow with
rules × functions × file size.

**When you add a check to it, SABOTAGE-TEST the check.** Break the thing on
purpose and confirm preflight goes red. Two of the first eight checks were
written, looked correct, and could not fail: the hook check searched the JS
for the hook name, and the QUERY contains the name, so it matched itself.
The same failure as the test that missed the stage-counting bug. A green
light from a check that cannot go red is worse than no check at all.

**Sabotage the EXISTING checks too, not only the new one, and sabotage the
thing you actually care about.** Adding the jobs CANCEL button turned up two
holes in checks that had been passing for weeks:

  * the hook check knew `q` and `querySelector` but not `querySelectorAll`,
    which is how that button collects itself — so a whole class of control
    was never checked at all. A check with a hole reads as coverage.
  * `page hooks exist` compared each template against each script it loads,
    so a shared script's hooks had to appear on EVERY page loading it.
    Three false failures at once from `admin.js`, whose lightbox hooks live
    on one page of the three.
  * A third hole surfaced 2026-09-23, when the zoom's markup moved into a
    shared `{% include %}` partial: the same check read only the including
    file, so every hook in the partial counted as missing — and once
    taught to follow includes, the fix opened the OPPOSITE gap, because
    per-script-across-all-pages means one page carrying the markup
    satisfies the check for a page that forgot the include. That needed
    its own two-lists check (`the shared zoom ships with its markup`).
    The general shape: when markup becomes SHARED, re-ask every check
    that maps a page to its own markup — both directions break at once.

And the first attempt at sabotage tested the wrong thing: renaming the
button's `data-` attribute proved nothing about "endpoints have buttons",
because the fetch was still there. **Say which check you expect to go red
before you break anything**, or you will confirm a check that was never
looking.

**A TEST OF A REFUSAL NEEDS A POSITIVE CONTROL FIRST.** v266's test that
the uploader refuses a row whose picture went to step 2 stayed green with
the guard DELETED: the quiet window had closed intake at 22:00 in the
sandbox, so nothing at all was claimable and "nothing was claimed" proved
nothing. Sabotage found it. Before asserting that X is refused, assert in
the same test that an ordinary Y IS accepted — otherwise the test cannot
tell a working guard from a closed door.

**A TEST MUST NOT BE HANDED THE RULE IT IS CHECKING.** The archive tests
lift two functions out of the shipped file and were passed `FOLDER_ID` — the
pattern that decides which title a path belongs to — as a convenience. Break
that pattern on purpose so every title number past 9 reads wrong, and all
eleven checks stayed green: the test was comparing the file against a copy
of the rule it had supplied itself. `_exec` now lifts module-level values
out of the file too. Anything a rule depends on comes from the shipped
source, never from the caller.

**AND PROSE MUST NOT SATISFY A CHECK EITHER.** Ending an entry in `(` stops
a bare word matching a parameter name; it does nothing about a COMMENT. On
2026-08-27 a guard was added to `go_to_title`, along with a comment reading
`# See _may_touch().` — and deleting the call left the check green, because
it substring-matched the raw source and the sentence about the call was
still there. Every entry in the table had been exposed to this, not just the
new one. `check_guards_are_called` now takes the guards from the CALLS the
function actually makes, read out of the syntax tree, where a comment does
not exist. **Sabotage found it; reading the check did not — and the first
three attempts at the sabotage silently failed to apply, which is its own
lesson: verify the sabotage landed before believing a green light.**
**And verify the RESTORE landed exactly, with a pattern as narrow as the
sabotage's.** On 2026-09-20 a sabotage removed one word from one tuple with
a unique anchor — and the restore put it back with a BROADER pattern that
matched four innocent tuples in the same file, silently opening a bulk
door and a status-reset that undid retirement. A scripted restore is an
edit like any other: unique anchor in, unique anchor out, then grep the
whole file for the word you were juggling before believing it.

**A NAME THAT EXISTS IS NOT A NAME YOU KNOW HOW TO CALL, AND THE GAP
BETWEEN THOSE TWO IS A 500.** `check_module_attributes` proved that
`P.project_scope` existed. It said nothing about the SHAPE of the call, and
the recall panel shipped calling it as `P.project_scope(db.query(...),
project.id, default_project_id=...)` — that function takes ONE positional
argument and hands back a filter CONDITION rather than a query, so every
press of the count button raised TypeError. Python compiled it, no name was
undefined, the attribute really was there, and every check stayed green
(owner's find, 2026-09-09).

The lesson generalises past arity, and it is the same lesson as reading a
working precedent before theorising about it (3c-ter): **when you reach for
a function you did not just write, open its `def` line before you type the
call.** Read what it TAKES and what it RETURNS. Guessing the shape of a
signature is `3c-bis` wearing local clothes — inventing a value you could
have looked up in four seconds, in a file already in the repo.

**AND A NAME THAT EXISTS CAN STILL BE UNREACHABLE, BECAUSE DECLARATION
ORDER DECIDES WHO ANSWERS.** `/api/chat/_summary` existed, was correct, and
never received a single request: `/api/chat/{worker_id}` was declared above
it, captured the word "_summary" as a worker number, and answered 422 — so
the sidebar chat badge was dead from the day it was built, while two
sessions "fixed" the badge's JavaScript around it (found 2026-09-23, from
the owner's console). Literal addresses go ABOVE parameterised ones, and
`check_literal_routes_before_param_routes` in `preflight.py` now fails any
route a parameterised sibling above it would capture. The same shape in
JavaScript: a CLASS the code finds an element by is a load-bearing name,
and writing `el.className = '…'` without it erases it — the zoom's flag
pill was findable exactly once, so the second open of any flagged picture
died silently (same day, same console). Watched by
`check_finder_classes_survive_classname_writes`.

Now mechanical: `check_call_arity` in `preflight.py` compares every call
into our own module-level functions against the definition. It asks a
deliberately narrow question — module-level only, so `self` never arises,
nothing decorated, nothing whose name is declared in more than one visible
place, and calls using `*` or `**` skipped, because the count is not
knowable. Sabotage-tested in three directions on 2026-09-09: too many
positional arguments, a misspelt keyword, and a missing one.

**MATCH A CALL, NEVER A WORD — this is the commonest way a check reads as
coverage while looking at nothing.** The guard check written on 25 Aug
accepted the bare word `html_markers` as evidence that a function consulted
the wall. That word is also the name of a PARAMETER, so putting the original
bug back on purpose left the check green: the sabotaged function still
mentioned it while doing nothing whatever with it. Every entry now ends in
`(` and names something actually invoked. Same family as the hook check that
searched the JS and found its own query. **The sabotage is what found it —
without it this would have shipped as a protection that could not fire,
guarding a bug that had just cost an evening.**

**A HELPER BORROWED FROM THE WRONG SCOPE IS THE QUIETEST FAILURE IN THIS
WHOLE FILE, BECAUSE `async` EATS THE ERROR.** `admin_pipeline.js` holds two
wrappers: the big one declares `q`, the small GPT panel declares `$`. The
signature UPLOAD handler was written in the second and called `q(...)`. The
owner pressed UPLOAD and the page did nothing whatever — no message, no
change, nothing in front of him at all. The same file had a second instance:
`toast` was declared inside that wrapper while Approve Artwork's script
called it too, and those two never load together, so the eyedropper's
message had never once appeared and the line that reports an unreadable
pixel was itself throwing.

**An error inside an `async` handler becomes a rejected promise nobody
awaits.** That is why this class is worse than an ordinary crash: a crash at
least stops something visibly. Whenever a handler is `async`, assume any
mistake inside it will be perfectly silent, and lean harder on the
mechanical check than on trying it once.

`check_js_helpers_are_in_scope` now asks the narrow question both defects
answer yes to: is this name somebody's PRIVATE helper, called from outside?
A name only counts if it is declared inside some wrapper, so no list of
browser globals is needed. Two lessons from building it, both general:

  * **The first version tried to be a scope analyser and had to be thrown
    away.** It needed a real JavaScript lexer — template literals nest,
    regex literals hold quotes — and the hand-rolled string-blanker silently
    ATE REAL CODE, reporting `jobTone` as undefined while `function jobTone`
    sat forty lines below. **A check that must be right about lexing to be
    right about anything is the wrong check.** Ask a narrower question that
    needs no parsing.
  * **Getting the narrow version right still took four holes, every one at
    the edge of the pattern**: `async function` unrecognised, `//` comments
    unstripped so "every 4th tick (~12s)" read as a call, declarations
    nested deeper than two spaces unseen, and names the browser also
    provides colliding. When a check reports something you can see is wrong,
    the hole is nearly always in the variant you did not enumerate — and
    **prefer being permissive about what counts as "already defined here"
    and strict about what counts as "private", because both directions make
    a check quieter and neither can invent a false alarm.**

**BEFORE A CHECK CALLS SOMETHING UNOWNED, ENUMERATE EVERY WAY IT COULD BE
OWNED.** `check_orphan_files` built its list of known files from `SavedPoster`
rows and nothing else, then reported everything left over as unknown — under
a heading reading "the app has no idea they exist". On the owner's screen
that included his SIGNATURE and his REFERENCE PICTURE. Deleting them on that
advice would have stopped the poster builder dead, because a signature
switched on with no file is a hard refusal.

A file in this system can be claimed three ways: a ROW points at it, a
SETTING names it, or a COLUMN somewhere else holds its path. The check knew
one. **One-of-three is not coverage, it is a confident wrong answer** — and
this one was attached to a delete button, which is the worst place for one.

The general shape, and it is not about files: whenever a check reports
"nothing refers to this", the check is only as good as its list of the ways
something can be referred to. Write that list down and ask what is missing
BEFORE trusting the finding. Same family as the hook check that knew `q` and
`querySelector` but not `querySelectorAll`.

**A NAME CHOSEN IN ONE FILE AND DEFINED IN ANOTHER FAILS SILENTLY, SO
COMPARE THE TWO LISTS.** The sidebar marks each section
`data-nav-tint="money"` and the status strip asks for `chip('warn', …)`;
both look the colour up in the stylesheet. A name with no rule behind it
breaks in the quietest way there is — an undefined variable inside `rgba()`
makes the whole declaration invalid, so the border, the wash and the marker
are simply never drawn, and a red "machine OFFLINE" chip renders grey.
Nothing else can see it: the template is valid, the JavaScript parses, every
hook exists, and this environment cannot render a page.

`check_colour_names_have_rules` reads the names out of the file that CHOOSES
them and fails on any the file that DEFINES them is missing. Sabotage-tested
in both directions on 2026-09-09. The shape is bigger than colours: whenever
one file names something another file must provide — a CSS class, an icon
key, a settings key, a job kind — those are two lists and a script can
compare them in a second. The third audit (2026-09-09) added two more of
these comparisons after finding both broken by hand: every TABLE in
models.py must be either wiped by `reset_workflow.py` or named in its
KEPT_ON_PURPOSE list (five tables were in neither, so a "reset to zero"
kept the test shop's money), and every key in DEFAULTS must be READ by
something (`allowed_download_hosts` was not — a control nobody consults,
the `brave_daily_query_cap` shape again). Both are preflight checks now.
When two things must stay in step, ask what script compares them — a rule
in this file is the weakest rung.

The same check also asserts that **every `/admin/` link in `base.html` sits
inside a coloured band**, because a visual scheme only works if it covers
everything: a link added later and left outside would render as a plain grey
row between coloured blocks, which does not break and does not look
deliberate either. Note how the scope is decided — by the HREF, not by a
list of exempt links — so the "All Projects" exit link and the whole worker
menu stay outside without anybody having to maintain an exceptions list.

**A RULE THAT CARRIES ITS OWN EXCEPTIONS IS ONE SOMEBODY MUST REMEMBER TO
EXTEND; DERIVE THE SCOPE INSTEAD.** The specimen is worth naming because it
cost the owner a whole screen. `data-zoom-open` sat on the entire Approve
Artwork card, so the click handler carried a list of what NOT to treat as a
click on the picture: not the colour bar, not the version buttons. The
signature bar arrived months later, nobody extended the list, and every
nudge of a slider threw a full-screen overlay in his face (2026-09-09).

Three things generalise:

  * **The exception list IS the defect, not the missing entry.** Adding
    `.review-sig` to it would have fixed the symptom and guaranteed the next
    control repeats it. The attribute moved onto the pictures themselves,
    and now there is nothing to exclude.
  * **A click target that covers a REGION will swallow every control that
    region ever grows.** Ask what the target will contain in a year, not
    what it contains today. Same question as "can this column ever be
    plural" (rule 6), asked about space instead of data.
  * **The cursor and the click must be the same shape.** `cursor: zoom-in`
    was on the card too, so the sliders truthfully advertised the bug. When
    a handler and a style disagree about what is clickable, one of them is
    already wrong.

Now mechanical: `check_click_targets_do_not_swallow_controls` fails on any
whole-region click target containing an `input`, `button`, `select` or
`textarea`. Sabotage-tested by putting the original bug back, and it reports
blindness rather than passing if the hook is ever renamed.

**A TEST CAN MEASURE THE WRONG THING AND STILL GO GREEN — AND A GREEN
NUMBER IS MORE CONVINCING THAN A GREEN TICK.** The signature had to land 20
pixels from the edge of a 4000-wide poster. The first test built the print
file, thresholded on brightness, and read off where the bright pixels
stopped. It printed "0px from the edge", which looked like a bug in the
placement and was a bug in the TEST: the flattened poster was itself bright,
so the threshold was measuring the background. A mark painted anywhere at
all would have produced the same reading.

The version that works builds the file TWICE, with and without the mark, and
looks at where they DIFFER. That measures the thing the change is
responsible for and nothing else. Generally: **when a test reports a
suspicious number, suspect the test before the code**, and prefer a
DIFFERENCE against a known-good run over an absolute reading of the result —
the same reasoning as comparing a change rather than a total when checking
ourselves against a marketplace (5d).

**INSERTING ABOVE A DECORATED FUNCTION STEALS ITS DECORATOR.** A new
helper placed directly above an endpoint lands BETWEEN `@router.get(...)`
and the `def` it belonged to, so the decorator silently moves onto the
helper. On 2026-09-23 this made `_changes_waiting_sets(db: Session, …)`
the /revisions route; FastAPI cannot turn a Session into a web parameter,
raised at import, and the live site crash-looped after deploy — the same
slip had happened hours earlier on `api_browse` and was caught by eye,
which is exactly why eyes are not the guard. Insert helpers ABOVE the
decorator line. Now mechanical: `check_route_decorators_sit_on_routes`.

**CUT BY THE SYNTAX TREE, NEVER BY LINE NUMBERS YOU READ OFF A SCREEN.** On
2026-09-09, removing the spend feature meant deleting one endpoint and two
blocks from `pipeline_admin.py`. The line numbers were read from `sed` output
and three ranges were deleted in one go. Two of the three were wrong: the
endpoint actually ended at 2540 rather than the 2572 that had been eyeballed,
so the cut ran through the middle of two other functions and left the file
unparseable. It was restored from git and redone by asking `ast` for each
node's `lineno` and `end_lineno`, one cut at a time, **parsing before every
write rather than after**.

Three things worth carrying:

  * **A function's end is a fact the parser knows and you are guessing.**
    Never derive it from "the next `def` I can see".
  * **Batching deletions hides which one broke it.** One cut, one parse.
  * **Parse BEFORE writing.** Writing first and checking after means the
    broken file is what exists while you work out what happened.

**A hole is normally at the edge of the pattern you wrote, so enumerate the
variants.** Every way this codebase asks for a hook. Every way a caller
writes a URL parameter — `${id}`, `{{ u.id }}`, `' + id + '`. A regex that
handles one of three is two-thirds blind and silent about it.

Every deletion and every move is a structural edit, and the thing you must
check is the file that now exists — not the change you meant to make. These
are not code review opinions, they are mechanical checks a script can do in
seconds, and skipping them has already cost a deploy cycle.

Whenever you cut, move or reorder a block, run the check that matches the
file type before saying you are done:

| You edited | Prove |
|---|---|
| a Jinja template | `<div>`/`<section>`/`<form>` opens == closes, and the template still parses |
| a JS file | it parses (`node --check`) and every `data-` hook it queries still exists in the template |
| Python | no undefined names (AST or `pyflakes`), not just `py_compile`, AND that every call passes the arguments its function actually takes |
| a settings key | it is DECLARED in `pipeline.DEFAULTS`, not merely read and written |

**A new settings key is a schema change, not a string.** `get_setting` and
`set_setting` REFUSE any key not declared in `pipeline.DEFAULTS` — on purpose,
so a typo cannot resolve to None. The consequence is that an undeclared key is
an instant 500 on the first page that touches it, and it will pass every
static check there is: the code parses, the names are defined, the hooks
exist. The Earnings tab shipped this way and the page was blank white on the
first click. Before saying a feature is done, grep every `get_setting` /
`set_setting` call you added and confirm each key is in `DEFAULTS`. Note that
`payments.py` has its OWN `get_setting(db, key, default)` which is unrelated
and needs no declaration — check WHICH function is imported before believing
a hit.

**AND THE VALUE'S TYPE IS PART OF THE SCHEMA, NOT ONLY THE KEY.** The door
checked the key and let any STRING through, while roughly twenty readers
trust `int(get_setting(...))` bare — so one stored "abc" (or "") in a
number setting would have 500'd whichever page read it next, including the
worker's entire state call. Found by the 2026-09-11 audit, not by a crash.
Fixed at the door, where the wrong shape can be refused once and loudly:
`_reject_a_value_the_key_cannot_hold` in `pipeline.py` reads the key's own
DEFAULT to know what type it must hold, and `check_number_settings_hold_
numbers` in `diagnostics.py` watches for garbage that predates the door.
The general shape: **when a gate validates identity, ask what it lets
through about CONTENT** — twenty defensive readers is the wrong fix; one
refusing door is the right one.

**Deleting is the dangerous edit, not adding.** Removing a panel means
removing its opening tag, its body AND its closing tags — a slice that starts
at the right place and ends one tag early leaves markup that still renders,
still parses as a template, and quietly reparents everything below it.
Browsers do not report this; they "repair" it by closing containers early,
so the visible symptom lands somewhere unrelated to the edit. That is exactly
how removing finished instructions from the NODES tab made the ADD ACCOUNT
box on the UPLOAD tab stop appearing.

**A SHARED CONTROL read by some handlers and not others is the same defect
wearing a fourth hat.** The AUTOMATIC tickbox on the TeePublic tab was read
by three buttons and silently dropped by the fourth, which created its run
without it. Nothing was disconnected — the checkbox existed, the hook
matched, the button had a handler, the endpoint was called — so no check
could fail. Unattended it would have switched 226 live listings off and then
parked at a gate until morning.

The general form: **when a screen has a setting that several actions share,
list the actions and confirm each one carries it.** `preflight.py --map` now
prints the request body beside each endpoint for exactly this, because the
odd one out is only obvious side by side:

    start              -> /start              {auto, mode}
    deactivate-missing -> /deactivate-missing {}          <- this one

**Moving a call OUT of a shared function is a deletion, and needs the same
proof.** `open_work_tab()` was correctly taken out of `login()` so earnings
reads would stop running upload hygiene — and never added to `run_batch`. The
method still existed, still had its docstring, still compiled, and nothing
called it: every upload since ran in the login tab, the one tab the legacy
tool always abandoned because Chrome puts a dialog over it. After moving a
call, grep for it and count the CALL SITES, not the definition. Zero callers
of a public method is a defect, not a style question.

**Identify a page by what it SHOULD contain, not by what is wrong with it.**
The TeePublic wall could have been detected by its own markup — and its class
names are randomised, so that check would have worked in testing and failed
on their next deploy while pointing at something else. Detecting it as "the
labels we came for are absent" survives the wall being redesigned, renamed or
replaced, and doubles as the proof we got through afterwards. A positive test
for the thing you want beats a negative test for the thing in the way, and it
is usually the same amount of code.

**Matching a word in raw HTML is not detection.** A page's markup is full of
vendor names, class names and script sources that say nothing about what the
page is DOING. The bot-wall check searched page_source for "captcha", and
TeePublic's ordinary sign-in page mentions "recaptcha" fifteen times in a
dormant widget — so a page whose visible text read "Welcome Back!" parked the
account for three hours. Match the VISIBLE TEXT, and match a sentence the
user would actually see, not the name of a product that might merely be
loaded.

**A page that loads is not a page that works.** There is no compiler for HTML.
The only honest check is to parse the tree and assert the thing you care about
is where you think it is — in particular that no modal, panel or control has
ended up inside a `hidden` ancestor, because unhiding a child of a hidden
parent does nothing and produces the perfect silent failure: a button that
responds to every click by doing nothing at all.

**A CHILD'S OWN STYLE CAN CANCEL WHAT THE PARENT WAS SET TO, AND NOTHING
ANYWHERE IS WRONG.** The Approve Artwork screen shows a see-through poster
on a plate whose colour the admin picks; the browser composites the two,
which is the same arithmetic the server does when it flattens. The picture
also carried `background: #111` from a rule written months earlier. A
background on the child paints ON TOP of the plate, behind the see-through
pixels — so the sky stayed near-black whatever colour was chosen, while the
handler fired, the value was stored and the plate really did change. The
zoom overlay had no such rule, so the owner reported it as "the colour
preview only works when I zoom in" (2026-09-09).

The general form: **when JavaScript sets a visual property on an element,
list what is DRAWN on that element and confirm none of them sets the same
property.** Nothing else can find this — the CSS is valid, every hook
exists, and this environment cannot render a page. Now mechanical:
`check_no_background_on_composited_img` in `preflight.py` finds the plates
from the code, parses the markup to see which pictures sit on one, and
fails on any rule that gives such a picture a background of its own. The
FIRST version of that check compared selector TEXT against the plate's
class and stayed green with the original bug put back, which is the sabotage
rule earning its place twice in one afternoon.

**A PERCENTAGE IS MEANINGLESS UNTIL YOU SAY "OF WHAT", AND THE TWO SIDES
PICKED DIFFERENT ANSWERS.** The same screen, later the same day. The server
places the signature against the PICTURE — `W, H = img.size`. The preview
placed it against the PLATE, which since v158 was deliberately WIDER than
the artwork on a card and TALLER than it in the zoom. So the mark sat out on
the coloured bar outside the poster, and came out a different size in the two
views. The owner reported those as three separate faults; they were one.

Three things in that generalise well past this screen:

  * **When one number is computed on both sides of a boundary, write down
    what each side measures it against.** Not the formula — the DENOMINATOR.
    Both sides here used `x_pct` and both were "correct"; they simply
    disagreed about the box, and nothing named the box anywhere.
  * **A unit can betray you inside one side too.** The bottom margin was
    `bottom: 0.5%`, and a CSS percentage on `bottom` resolves against the
    container's HEIGHT — while the server computes `W * margin_pct`, the
    WIDTH. On a 4000x6000 poster the preview showed 30 pixels where the file
    put 20. Nobody had reported it, and it had been wrong since v167.
    Whenever a percentage crosses into CSS, check which axis CSS resolves it
    against; `bottom`, `top`, `translate` and `padding` do not all agree.
  * **A comment claiming the preview IS the arithmetic is a claim to test,
    not a reassurance to trust.** The stylesheet said in capitals that
    drawing the mark any other way "would be a second copy of the placement
    rule, and two copies drift". It was already a second copy and had already
    drifted. **Prose asserting an invariant is the softest evidence in the
    repo** — the same lesson as a comment satisfying a guard check.

Now mechanical, in the half that can be: `check_overlay_sits_in_its_
measuring_layer` asserts the mark is built inside the layer that is measured
to the picture, at every place markup is built. What it deliberately does NOT
claim is that the layer covers the picture on screen, because that is a fact
about a rendered page. **Say which half a check covers, or the green light
gets read as covering both.**

And moving the colour from the plate onto the picture silently invalidated
the check above it: its question was "does the picture paint over the
plate?", and once the picture legitimately carried the colour that question
answered nothing. It stayed green because the new background is set inline
from JavaScript, which it never looked at. **When you move a fact from one
element to another, go and read the checks that were watching the old
place** — a check whose premise you just removed does not fail, it agrees
with you for ever.

**When the owner reports "X does nothing", suspect the last structural edit to
that page BEFORE suspecting X's logic.** The handler is usually fine. Check
what shipped most recently against `tools/DEPLOY_LOG.md` and diff the region
you touched.

