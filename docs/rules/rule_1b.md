<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 1b. If his instruction would break something, SAY SO BEFORE BUILDING

His words, 2026-08-24: *"if i give you a direction that messes up something,
mention it first before building. Dont just blindly implement it if you think
it would contradict something, then I will give you confirmation or
alteration or decide not to implement."*

He describes the OUTCOME he wants, in his own words, from what he can see on
the screen. He is not describing the mechanism, and he cannot see what else
depends on it. So an instruction that is perfectly clear can still conflict
with something he is not looking at.

The incident: an overnight sweep died at 11pm, and in the morning pressing
FULL SWEEP restarted from design 1 — three accounts already scanned were
scanned again. He said: make it never scan from scratch, skip anything
already marked visible or missing.

Taken literally that breaks the tool. A design's status is a fact with a
DATE on it. "Skip anything already marked" means a design marked visible
last week is never checked again, the catalogue freezes, and the whole point
— noticing that a design has newly dropped out — quietly stops working.

What he actually wanted was narrower: an INTERRUPTED sweep should carry on
rather than start over. That is a property of the RUN, not of the design.

So when an instruction conflicts:

1. **Say what it would break, concretely**, in terms of something he cares
   about — money, a screen going wrong, work repeating. Not "that violates
   an invariant".
2. **Offer the version that gets him what he actually wanted.** He is
   describing a symptom; find the cause.
3. **Then stop and ask.** Do not build either version until he answers.

The failure mode this prevents is worse than a bug: building exactly what
was asked and quietly losing a capability nobody notices is missing until
the day it matters.

**HIS WORD IS NOT THE COLUMN THAT HAPPENS TO SHARE ITS NAME.** He asked for
"images which haven't been reviewed" first; there is a `reviewed_at`
column, so v239 sorted by that alone and flagged titles stayed on top. He
meant neither reviewed NOR flagged — anything he has already acted on
(2026-09-27). When his words line up neatly with a field, that is exactly
the moment to ask what he sees on the screen, because he is describing
the screen and the field only describes one of the marks on it.

