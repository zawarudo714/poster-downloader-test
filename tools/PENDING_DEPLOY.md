# Not yet deployed

**v216 — flag indicators, submit-as-is confirmation, and a flag watcher.**

1. Worker Images: clearing a flag in the zoom rebuilt only the one image box
   and left the title's red outline behind until a full reload. Now the title
   outline recomputes live from the title's images, both when the last flag is
   cleared and when the first is added.

2. Worker's "Nothing to change?" link now asks a real confirmation before it
   sends the image to the admin unchanged, so a stray tap cannot fire it. The
   note stays optional.

3. New Diagnostics check "needs_revision_matches_open_flags": catches any title
   whose stored flag disagrees with its live flags — a lingering red tag with
   nothing behind it, or a real flag the tag is hiding. This is the watcher for
   the "I approved it and it still shows flagged" class.

Server only — the node needs no copy. NOTE: the sandbox shell was down this
session, so node --check / py_compile / preflight could NOT be run here by hand;
the deploy tool's own preflight suite is the gate and will refuse if anything
is wrong.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
