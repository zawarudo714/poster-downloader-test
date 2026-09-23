# Not yet deployed

**v227 — one list, one number, plus the two console-error fixes.**
- CHANGES REQUESTED IS NOW TWO BANDS. "Waiting on you" holds everything
  your buttons can finish — completions, replaced pictures, deletions to
  review — in one list, newest first, each card keeping its action word
  (REPLACED / DELETED / COMPLETION / SUBMITTED AS-IS). "Waiting on the
  worker" holds the untouched flags below it.
- THE SIDEBAR BADGE FINALLY MEANS SOMETHING: it now counts exactly the
  cards in "waiting on you", from the same server helper the page builds
  from — it used to count single fixes only, so completions and deletions
  waited invisibly.
- "THE TITLE NOW HOLDS" only appears when it adds information (a picture
  different from the one above, or "nothing left") — no more double image.
- The zoom's "second click does nothing" crash: fixed (one erased class);
  new preflight check guards the class of mistake, proven red pre-fix.
- THE SIDEBAR CHAT BADGE NEVER WORKED, and now should: its address was
  declared below /api/chat/{worker_id} and read as a worker number (422).
  Route moved; new preflight route-order check, also proven red pre-fix.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
