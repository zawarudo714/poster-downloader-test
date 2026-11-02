<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3b. Ask what a mechanism SERVES, not where it appears

The most expensive misses in this codebase have all been the same shape:
reasoning about a thing by the screen it is displayed on, rather than by
what actually depends on it.

A real one: the "no worker machine is running" warning was written as a
per-project check gated on `processor == 'photoshop'`. Standing inside MUSIK
— whose processor is 'gpt' — it said nothing at all, while the very node
MUSIK's UPLOADS run through was dead. The node does two jobs for two
different reasons; the check only knew about one of them.

So for anything you touch, ask in this order:

1. **Who depends on this?** List them. Not "which page shows it".
2. **Is it shared?** Nodes, marketplace accounts, storage, selectors and
   timings are account-wide or marketplace-wide, NOT per project. A shared
   fact displayed inside one project is invisible to every other one — put
   it at master level.
3. **Does it serve more than one purpose?** The node processes AND uploads.
   A check covering one of those silently passes while the other is broken.

**NOTHING IS A FINDING UNTIL YOU HAVE TRIED TO KILL IT.** Standing
instruction from the owner, 2026-08-27, after THREE false reports in one
day. Before telling him anything is broken, produce ONE of:

  * a reproduction, or
  * a log line or database row showing it happening, or
  * the code path traced from PRODUCER to CONSUMER — the query that fills
    the value, not just the code that reads it.

If you have none of those, you have a QUESTION. Say "these two things look
inconsistent, I could not confirm it" and stop. That is genuinely useful.
A confident wrong diagnosis is not: it costs him an evening testing a
scenario that cannot happen, and it teaches him to discount the true ones.

The three, all the same shape — a pattern that looked like a finding, a
story built on it, and no attempt to disprove it, while the falsifying
evidence sat one grep away in a file already open:

  * **the flag card.** Two variables held "is this project in-page", so
    they were assumed to disagree. The list they feed is project-scoped;
    they cannot. The query was in a file being read all afternoon.
  * **"six stranded jobs".** Five, not six — one finished after midnight
    and the log rotates. And all five were killed by AGENT RESTARTS; there
    were 65 in the thirteen days of logs, each matching a job's last line.
  * **"a silent 400 loop".** The four errors begin one second after one of
    those restarts and stop at the next. Same event, self-resolved.

**Do not confuse the SHAPE of a real defect with a defect.** Two records
for one fact, work claimed and not reported, a gap in a log — these are
where bugs live, and matching one is a reason to LOOK, never a reason to
report. The same pattern-matching found four genuine defects the same day,
which is exactly why it is dangerous: it produces plausible stories fast,
and plausible is not measured.

**TWO VARIABLES HOLDING ONE FACT IS A SUSPICION, NOT A FINDING. Before
calling it a bug, NAME THE QUERY that can return a row where they differ.**
If you cannot name it, you have a code smell — say that, and do not send him
off testing a scenario that cannot happen.

On 2026-08-27 the worker screen was found asking "is this project in-page?"
from two different variables: the project being STOOD IN, and the TITLE's
own. That is a real duplication. It was reported as a live bug — "a worker
with a flagged movie poster cannot fix it while standing in MUSIK" — and the
flag list is project-scoped, so the two can never disagree and the card is
not even on screen.

The error is worth naming exactly, because the reasoning FELT like the
reasoning that had found two genuine defects the same day:

  * The scenario was INVENTED to make the two variables differ, then
    treated as though it had been observed.
  * The confirmation was `**_project_ui(...)` in the revision payload —
    "why would the server send a per-title mode unless the list were mixed?"
    It is boilerplate; the same call is in `lock_title`, `title_catalog`
    and `go_to_title`. A habit was read as a signal.
  * Reasoning ran from the CONSUMER (the JavaScript, where both names are
    visible) and never from the PRODUCER (the query that fills them).
    Whether two values can differ is a fact about the query.

The falsifying line was in a file already open, and cheap. It was not read
because by then there was a story worth keeping. **Go looking for the thing
that kills the theory, not the thing that fits it** — and the place to look
is always the query, never the variable names.

**A thing with two purposes needs two of everything that can stop it.** An
account both UPLOADS and REPORTS MONEY, and those fail for unrelated reasons
— a bot wall while reading TeePublic says nothing about whether uploading
works, a rejected upload password says nothing about the balance. One shared
`paused_until` meant either failure silenced the other, and the screen could
not say which had happened. Hence `earnings_paused_until` beside it. Before
reusing a status field, ask whether the thing it describes can fail in more
than one way INDEPENDENTLY; if it can, one field is already wrong.

**A long hold on the pipeline must belong to the WORK, not to a switch.** The
TeePublic sweep stops Photoshop and uploads for hours, and it stops them
because a RUN EXISTS — finishing, failing or abandoning the run releases them
on the way out. There is nothing to remember to turn back on, which matters
most exactly when something has gone wrong. Same shape as the quiet window,
one level up. And there must always be a STOP button reachable by the owner:
with a Photoshop backlog measured in weeks, a half-finished run must never be
something only a developer can clear.

**And a cooldown must have a way to end early.** A pause set by a failure
should be cleared by the next SUCCESS, not only by the clock. An account
signed in by hand and read successfully was still skipped by the scheduler
for the rest of its twelve hours, while the READ NOW button worked perfectly
— a disagreement between two paths with no symptom you could see.

The general form: **a per-project screen may only report a per-project
fact.** If the underlying thing is shared, the alarm belongs on the master
dashboard, and the per-project view explains what it means locally.

