<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 5. Prefer impossible over detectable

1. **Impossible** — the wrong state cannot be represented. Best.
2. **Loud** — it fails immediately, at the right moment, saying why.
3. **Detectable** — Diagnostics or Needs Attention finds it later.

**A MAGIC WORD MEANING "UNKNOWN" IS TRUTHY, SO IT DEFEATS EVERY EMPTINESS
TEST DOWNSTREAM AT ONCE. ABSENCE MUST BE NULL.** The `year` column was
`nullable=False, default="N/A"`, and that one default poisoned 88,970 travel
titles at the door. Travel places have no year, so every screen guarded with
`year ? draw(year) : draw(nothing)` — and every one of those guards passed,
because two letters count as an answer. The owner saw "(N/A)" beside titles
that could never have a year, reported it twice, and had to ask whether it was
in the DATA or in the CODE (2026-09-09). It was in both: the column default,
a folder-name builder that baked `(N/A)` into permanent paths on disk, a dead
parser, and the seed data.

Three things generalise:

  * **The word is a value pretending to be an absence.** A NOT NULL column
    forces you to invent one, so the column type IS the defect. Make it
    nullable and let nothing mean nothing.
  * **One default reaches every screen at once, and no screen is wrong.**
    Each guard was correctly written; there was nothing to find by reading
    them. Look at the PRODUCER — the column, the importer — before reading
    the twenty consumers. Same instinct as 3b.
  * **Cleaning it up is not fixing it.** He asked whether a reset would clear
    it. A reset clears the DATA and reinstalls the CODE, so it would have put
    every one of those four back. When somebody asks "will resetting fix
    this", the real question is whether the bad value is being SAVED or being
    WRITTEN, and only the second one survives a reset.

Now mechanical on both rungs: `check_no_magic_absent_value` in `preflight.py`
fails on a NOT NULL column defaulting to such a word and on any `x or "N/A"`
fallback (sabotage-tested three ways), and `year_is_a_year_or_nothing` in
`diagnostics.py` watches the live rows, because preflight is a claim about the
code and only an invariant is a claim about the data.

**AND THE THIRD RUNG WAS MISSING FOR A WEEK, WHICH IS THE MORE USEFUL
LESSON.** Fixing the DATA and the PRODUCER left the CONSUMERS unexamined. I
guarded the two screens the owner had pointed at, wrote "the guards were
correct everywhere", and moved on. Seven more were drawing a year with
nothing checking it, so the next screen he opened printed "(null)" —
JavaScript's spelling of nothing — and one panel printed empty brackets
(2026-09-10). Changing the stored value from "N/A" to NULL had made every
one of them WORSE, because "N/A" at least looked deliberate.

The rule is rule 3c, and I broke it while writing about it: **fix the class,
never the instances somebody pointed at.** The moment you find two of
something, the next move is to enumerate ALL of it with a script, and the
script is the deliverable. `check_years_are_guarded_before_drawing` is that
enumeration; it reads every line that draws a year and fails on any without a
test beside it, in JavaScript and in Jinja alike. The word list is
deliberately short — "unknown", "-" and "?" are ordinary English and flagging
them would fire on every healthy log line, which is a keystroke, not a guard.

**A destructive instruction must be checked by the thing carrying it out,
not only by the thing issuing it.** The profile cleanup names one folder,
decided server-side — and the node still refuses any path that is a
CONTAINER (the profiles folder, the scratch folder, a drive root). A test
pointed an account's own `chrome_profile_dir` at the profiles folder and the
first version cheerfully deleted every account's saved session in one go.
"The caller would never send that" is not a guard.

**COPYING DATA COPIES INTENTIONS, and they are executed by whatever picks
them up first.** The migration tool carries the TeePublic catalogue and past
sweeps across, so months of "has this been missing three sweeps running" is
not thrown away. But a sweep is not only history: a row reading
`reactivating` is a live instruction. On 25 Aug the promoted box came up,
the worker machine polled thirty seconds later, and started switching real
listings on a real store by itself.

Note WHY the existing protection missed it. Every account is disabled on
import — which stops UPLOADS, and this was not an upload. One thing with two
capabilities again (rule 3b), and only one of them was fenced.

So: **anything imported that means "do this next" must arrive neutralised.**
Not detected afterwards — a run that has already dispatched a job cannot be
un-dispatched. The fix at the time was `_park_runs()`, which ended every
unfinished run on the way in, releasing the pipeline hold with it. (That code
was deleted with the migration tool on 2026-09-01; the rule outlives it and
applies to ANY import, restore-from-backup included.)
Note what is deliberately NOT undone — designs recorded as switched OFF keep
that record, because they really are off on the marketplace and clearing it
would look tidy while losing the only list of hidden live listings.

Every check on tier 3 is an admission that 1 and 2 were skipped. He would
rather have well-formed flows than a growing list of warnings. Before adding
a diagnostic, ask: why can this state not simply be impossible? Sometimes the
honest answer is "the marketplace is outside our control" — that is fine.
Often it is laziness.

