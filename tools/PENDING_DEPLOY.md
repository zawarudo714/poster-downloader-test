# Not yet deployed

**v214 — the last-worker-action chip now names the title, and lines up.**
The strip entry used to read "humphrey completed · 09-15 00:00" with no
title, because a completed/claimed/skipped log row carries no name in its
details — only an id. The server now looks the title up from the row the
action points at (master title, saved image, or flag). The chip itself is
tidied: the sentence is capped with "…" so a long title cannot push the
strip out of line (hover shows the full text), and the timestamp is shown
smaller and greyer beside it. Server only — the node needs no copy.

Also in v214: the v213 "REPLACE FILE beside the paste box on a phone" fix
did not take, because a SECOND phone rule further down the stylesheet was
still stacking the card's controls into a column — and the later rule wins.
That second rule no longer stacks them.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
