# Not yet deployed

**v217 — "Edit in Photopea" responds at once, and opens faster.**

The button downloaded the full-size picture BEFORE showing anything, so on a
slow link it looked dead for several seconds. While it looked dead you could
step to the next image and click again, and when the first download finished
the editor opened the image you had ORIGINALLY clicked — the off-by-one you
reported. Two changes:

1. The editor overlay now appears the instant you click, with a spinner
   reading "Loading the picture into the editor…". It covers the screen, so
   there is nothing to step to while it loads, and the picture that opens is
   always the one you clicked. SAVE is disabled until the picture is in.

2. The editor now boots at the SAME TIME as the picture downloads, instead of
   one after the other, so the wait is the longer of the two rather than their
   sum.

Server only — the node needs no copy. NOTE: the sandbox shell is still down,
so node --check / preflight could NOT be run here by hand; the deploy tool's
preflight suite is the gate.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
