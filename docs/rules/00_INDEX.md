<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## HOW TO WORK HERE — the standing rules

These are not optional and they are not something the owner should have to
ask for. He is not a coder; his only way to check your work is to click
around afterwards, which means every miss costs him a deploy cycle and an
evening. So the burden is on you to make your method visible BEFORE it is
expensive, and to speak in plain terms rather than implementation detail.

**These rules are a NET, not a checklist.** Each one was written after a real
defect, but the point is never the defect — it is the SHAPE. Before starting
anything, read the shapes below and ask which could apply here, including the
ones that look like they belong to a different part of the system. A rule
learned from a deleted HTML panel is what catches a broken JS hook; a rule
learned from an upload stall is what catches a stuck backup. If a rule seems
irrelevant, say why in one line rather than skipping it silently.

| # | The shape it catches | Related |
|---|---|---|
| 1 | Built without enumerating what is already on the screen | 3b |
| 1b | Built exactly what was asked and silently lost a capability | 1, 2 |
| 2 | Fixed the asked-for thing, left its neighbours broken | 1, 6 |
| 3 | Said "verified" without naming a test | 3c |
| 3b | Reasoned from where a thing is SHOWN, not what depends on it | 3d, 6 |
| 3c | Trusted the edit you meant to make over the file that exists | 3, 5 |
| 3c-bis | Invented an external value instead of looking it up or asking — including a DURATION guessed in a comment that a whole design then rested on | 3c-ter |
| 3c-ter | Built a second path to something that already works — or theorised about a difference instead of reading the thing that works, or retried something the far side counts | 3b, 3d |
| 3d | Debugged the screen instead of the machine doing the work | 3b, 8 |
| 4 | Shipped without saying what else could break | 2 |
| 5 | Added a warning where the state should have been impossible | 8 |
| 5b | Turned one marketplace's measured behaviour into policy for all of them | 6, 3c-ter |
| 5c | Moved a call out of a shared function and dropped it on the way | 3c, 3c-ter |
| 5d | Shipped a mechanism with no invariant watching it — and a test that shared the bug's assumption, or an invariant that only ever compares our records with our records | 5, 8 |
| 5e | Fixed a bug and left the OWNER as the only thing that could have found it | 5d, 7, 3c |
| 6 | Correct for two projects, wrong for the third | 1, 3b |
| 7 | Fixed a bug and left no rule behind | all |
| 8 | Claimed work with an exit that reports nothing — including catching an error into a counter, queueing work nothing can cancel, fixing one loop of several, or a guard that only one of several paths calls | 3d, 5 |
| 9 | Shipped a dependency the node was never told to install, or changed the node without bumping the one number that proves the copy happened | 3c, 8 |

