<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3c-bis. NEVER invent a value you could look up or ask for

A value you cannot verify is not a default, it is a guess wearing a
default's clothing — and it ships looking exactly like working code.

The incident: the earnings reader needed FineArtAmerica's sign-in page. I
wrote `LOGIN_URL = f"{BASE}/loginpost.php"` and field names `email` /
`password` / `rememberme`, none of which I had ever seen. Two deploy cycles
were spent on HTTP 403 before the owner supplied the real page. Worse, the
correct value already EXISTED in the system: `login_url`, in the selectors
map, dashboard-editable, used by the uploader to log into the very same
account every day.

So, in order, before typing any external constant — a URL, an endpoint, a
form field name, a header, an ID format:

1. **Is it already in the codebase?** Something else almost certainly talks
   to this marketplace already. `grep` the settings map before writing a
   literal. Two copies of one fact are two chances to drift, and the copy
   that breaks is always the newer one, silently.

   **In CSS the two copies are two rules for ONE selector, and the LATER
   one wins — so a fix applied to the earlier one silently loses.** The
   v213 "REPLACE FILE beside the paste box on a phone" fix edited the
   `.rev-actions` phone rule near its desktop rules, while a second phone
   rule for the same selector 900 lines further down still stacked the
   controls into a column; the owner's screenshot found it (2026-09-15).
   `.pulse-row` is defined twice the same way. Before changing any style,
   grep the stylesheet for EVERY rule on that selector and edit the one
   that actually wins — or better, collapse them into one.

   **The same applies to a value DERIVED in two places, and that version is
   harder to spot** because neither copy looks like a hardcoded constant.
   The Chrome profile folder was computed as "the account's setting, or this
   default" where it launched the browser, and read as "the account's
   setting" where it killed leftover browser processes. Accounts normally
   have that setting blank, so the launcher used the default while the
   cleaner saw an empty string and returned immediately — the one function
   whose entire job was clearing a stuck profile had never run for any
   account, for months. A fallback default belongs in ONE function that
   everything calls, never inlined at each use.

   **And one setting in TWO STORES is the same defect.** The pay rate moved
   to the pipeline cascade (`pipeline.pay_rate_kes`) so it could differ per
   project, while the Payments page's SAVE kept writing the old bare
   `pay_rate_kes` row — which nothing read any more — so raising the rate to
   7 snapped back to 5 on every save, and the fallback carried a THIRD
   default of its own, "10" (owner, 2026-10-11). The legacy
   `payments.get_setting/set_setting` now refuse any key the pipeline owns,
   and the box carries `data-setting` so `check_settings_are_reachable`
   watches it.

   **AND THE SAME SHAPE COST A WHOLE SCREEN AGAIN ON 2026-09-10, THROUGH AN
   OPTIONAL ARGUMENT RATHER THAN A DEFAULT VALUE.** Every function in
   `storage_remote.py` took `project=None`. Writes passed the project;
   reads did not. The Settings page saves everything as a PROJECT override,
   so the credentials lived at `pipeline.travel.storage_sftp_host` — writes
   found them and put painted posters on the Storage Box, reads looked at
   the blank global row, concluded there was no Storage Box configured, and
   fell back to a local folder that had never held anything. The review
   screen showed an empty pane beside a photograph that loaded fine, and the
   same unnamed read sat inside APPROVE, so flattening the print file would
   have failed identically.

   Three things generalise, and the third is the fix worth copying:

     * **An OPTIONAL argument is a default wearing different clothes.**
       `project=None` reads as "this is usually fine to omit". It never was.
     * **The asymmetry lived inside ONE function** — a read without the
       project on one line and a write with it twenty lines below. Nothing
       about either line looked wrong on its own.
     * **The fix was to delete the default, not to fix the call sites.**
       `project` is required now, so forgetting it is a TypeError at the
       call site instead of a wrong answer at runtime. That is rule 5 —
       impossible beats detectable — applied to a function signature. When
       a parameter must always be supplied, say so in the signature and let
       Python enforce it; a preflight check on top is belt to that braces.

     The SAME shape struck again the SAME DAY, hours after this entry was
     written: `_source_search_url` took `kind=""`, three call sites passed
     it and the one for the OPEN title did not — so the Google button's
     `{title} {kind}` template silently searched the title alone, on the
     one button the worker actually presses. Fixed the same way: the
     default is deleted, `kind` is required, and `check_call_arity` now
     fails the deploy if a call site forgets it. When you add an argument
     to a function with several call sites, grep the call sites and count
     them — the one that predates the argument is the one that misses it.

     And again on 2026-10-26: the Greenlight tab saves `greenlight_mode`
     as a PROJECT override, while the payment hook read it with no project
     — so choosing MANUAL there never stopped payment from starting
     painting, for as long as the tab existed. Found while building the K
     rule, which reads the mode per project.

     Closed on 2026-10-29 (v270) for the whole class, not per call:
     `pipeline.SITE_WIDE` names every key that has ONE value for the whole
     site (claim timeout, schedule, upload gap, earnings, listing check).
     `get_setting`/`set_setting`/`clear_setting` force those keys to the
     global row whatever project is passed, so the Settings page (which
     always sends `scope: 'project'`) can no longer hide them in a project
     row nobody reads. Every OTHER key read with no project is a defect,
     and preflight's "settings read without a project are site-wide" fails
     the deploy on one. A new site-wide key goes into SITE_WIDE; a new
     per-project key is read with `project=`. The same day: RESET on the
     GPT prompt sent no scope, so it cleared the empty global row and the
     owner's project prompt survived while the screen said "reset" —
     "setting resets name their tier" now requires a scope on every reset
     call, and the reset logs the value it removed.

2. **Can I read it from the source at runtime?** Parsing the form off the
   login page beats hardcoding its action, because it survives a redesign.
3. **Can I ask the owner?** He has the account open in a browser. One
   question costs a minute; a guess costs a deploy cycle and his evening.
4. Only then, and say plainly that it is unverified.

**Never present a guessed value as if it were researched.** If it was not
verified, the sentence to write is "I guessed this, here is how to check
it", not silence.

The general form: **for anything outside this codebase — a marketplace, an
API, an OS behaviour — the code must either read the value at runtime, take
it from a setting, or be told it. Inventing it is not an option, and neither
is inferring it from a plausible-looking pattern.**

**A PER-READ TIMEOUT IS NOT A TIME LIMIT.** `download_bytes` had
`timeout=20`, which `requests` applies to each read, so a website that
trickles a little every few seconds could hold USE MY OWN PICTURE on
"Fetching the picture…" for minutes (owner, 2026-10-09). A total deadline
was added (`imagefetch.TOTAL_S`) — and the first version could not fire in
time either, because `iter_content(64 KB)` blocks until all 64 KB arrive:
measured against a local trickling server, one piece took over 30 seconds.
Pieces are 8 KB now. When you cap how long something may take, check how
OFTEN the cap is looked at, not only its value.

**A TIMEOUT is a guessed duration wearing a number, and it decides whether
healthy work gets killed.** `claim_timeout_min` is 45 minutes and both the
claim reaper and the stalled-run sweeper compared it against `started_at` —
so ANY job running longer than 45 minutes was declared abandoned, however
well it was working. A TeePublic switching job takes about an hour and
reports once per design; at minute 45 a live job with 8 designs left was
cancelled, those 8 were handed out again, and the second attempt failed with
"already inactive". Both the measurement and the timeout were written down
in this repo; nobody compared them.

So: **ask a job whether it is ALIVE, never how long it has been going.**
A picture sent in an OpenAI BATCH (v266) may honestly take 24 hours, so its
claim carries NO claim time at all — the reaper only frees claims with one
— and its liveness is the batch row instead, watched by
`check_batch_claims_have_an_open_batch` and repaired at start-up.
**And no BUTTON may free it either.** Until v269 RELEASE on the In-flight
list and RETRY on Needs Attention took any picture id they were given, so a
batch picture — whose row looked "stuck" next to help text promising "a
minute or two" — could be sent again and paid for twice, its first answer
dropped. Both now leave a batch claim alone (its way out is STOP on the
batch); the test drives both endpoints, and
`check_batch_pictures_stay_in_their_batch` watches every other path. Any new
button that changes a picture's state checks the state it is LEAVING.
Liveness is the last thing it SAID — `PipelineJob.last_report_at`, stamped
by `append_job_log`. `started_at` is only the fallback for something that
never reported at all. And whenever you add a timeout, find the longest
thing it applies to and check the two numbers against each other.

**A send that may have arrived is ASKED about, never sent again — and no
request holds SQLite's write lock across slow work.** On 2026-10-29 OpenAI
took batch #52 (4 step-2 pictures), then the site could not save the
receipt: SAVE on Approve Artwork had written its first decision and then
held the one write lock through every print-file build in its chunk of 5
(10–17 s), and the painting thread gives up after `busy_timeout` (5 s). The
row stayed 'sending' with no OpenAI id, and the start-up repair would have
put its pictures back in the queue — paid twice, first answers lost. Since
v286 SAVE commits after EACH decision, and a batch still 'sending' is
settled by `poll()` (`_settle_unsent`), which walks OpenAI's batch list back
to our row's time and matches the custom_id on the first line of each
input file: found, it is watched like any other; not found, its pictures go
back; OpenAI unreachable, it waits. `recover_unsent` only counts them now.
`tools/test_step2_batch.py` drives all three cases and checks that a second
connection can write during every build. **The same rule holds for a
picture already PAID for and for a listing already LIVE** (v287): a painting
whose filing fails (Storage Box down, database locked) is kept in
`_gpt_tmp/paid/` and filed again by `gpt_worker.file_kept` instead of being
painted twice (`tools/test_paid_kept.py`); a batch start with no answer is
settled the same way as a send cut short; and the node sends its "done"
receipt OUTSIDE the upload's try, keeping it in `unsent_receipts.json`
until the site takes it, while the server ignores a failure for a row it
has as uploaded and RETRY skips them (`tools/test_upload_receipts.py`). A
worker's save likewise downloads BEFORE its first database write
(`tools/test_save_lock.py`).

**A guessed DURATION is the same defect, and it is easier to miss because it
usually lives in a comment rather than in code.** `dispatch_stage` said
"serial is fine, this stage is minutes not hours". Measured, it is about an
hour per account — wrong by a factor of thirty, and the whole design rested
on it: no stop check, no resume, all the jobs queued up front, because none
of that matters for something that takes two minutes. The owner found out by
watching it run all day.

So: if a design decision depends on how long something takes, either measure
it or write down that the figure is a guess and what breaks if it is wrong.
And whenever a real measurement contradicts a comment, **correct the comment
in the same edit** — a stale estimate is trusted exactly as much as a
verified one, because nothing distinguishes them on the page. This applies
to advice given in CHAT too: I told him parallel accounts would save four
minutes when the true answer was six hours, and he made a decision on it.

