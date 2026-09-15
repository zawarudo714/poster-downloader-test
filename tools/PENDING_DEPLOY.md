# Not yet deployed

## v212 — flagging no longer reloads the page or kicks you out of the zoom

Waiting to deploy. The node does NOT need copying. Version 212 (live is 211).

The problem, in plain words: on the Review Images zoom, pressing FLAG FOR
CHANGES (or CLEAR FLAG) closed the zoom and reloaded the whole day. That
threw you back to the top of the page, so after flagging you had to scroll
all the way down to where you were. It also meant a flagged image kicked you
out while an unflagged one you were just looking at did not — which is why it
felt like you "could only click on unflagged ones".

The fix: flagging and clearing a flag now happen in place.

- The zoom STAYS OPEN on the same image after you flag it. So you can flag,
  then press → to go straight to the next image — flagged or not — without a
  reload and without losing your place.
- Only the one card is redrawn, to show its new red flag border and pill.
  The rest of the page does not move, so your scroll position is kept.
- The flag panel in the zoom refreshes itself, so FLAG FOR CHANGES becomes
  CLEAR FLAG (and back) without a round trip through a reload.

Nothing else changed: the same flag and unflag endpoints are called, and the
worker still gets the flag exactly as before.

Verified: the JavaScript parses, the page hooks exist, the private helpers
are in scope, and the click-target check passes. Run in isolation because
the full preflight suite runs longer than the sandbox allows; the deploy tool
runs it in full before shipping. Only you can confirm the feel — flag a few
in a row and check the page never jumps (TO_TEST 64).

## v212 also — chat fills the phone screen

CSS only, same version. On a phone the chat used to stop short of the bottom
and leave a band of empty space, because its height was a fixed guess. It now
fills the screen: the workers list takes what it needs and the message area
takes all the rest, measured with dvh so the phone's address bar is counted.
Both the admin chat (workers list on top) and the worker chat (no list) are
handled. Nothing on desktop changed. (TO_TEST 65.)
