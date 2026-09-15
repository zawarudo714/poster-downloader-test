# Not yet deployed

## v207 — on/off boxes save again

Waiting to deploy. The node does NOT need copying. Version 207 (live is 206).

The bug, in plain words: the settings safety-check that stops letters going
into a number box was also refusing a plain on/off box when it was UNticked.
Unticking a box sends true/false, and the check turned "false" into the text
"False" and then complained it was not a number. The owner hit it on the new
"Check each image is the right place" box (2026-09-15), but the same fault
sat under every on/off box on the Pipeline settings page — the signature
toggle, the review toggle, and the rest. None of them could be switched off
and saved.

The fix is one place: the check now lets a real true/false through, because
a ticked or unticked box is always a valid 1 or 0 and is stored as such two
steps later. Garbage in a real number box is still refused exactly as
before.

Why it hid: the first version of the check tried to exempt on/off settings
by looking at the DEFAULT value, but every on/off default is written as the
number 1, not as a true/false, so the exemption never actually fired. The
right thing to look at is the value being saved.

Verified: preflight green, and the check itself was run against eight cases
against the shipped file — a box switched off now saves, a box switched on
saves, and "abc" or an empty string in a number box is still refused.
