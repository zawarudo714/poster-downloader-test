# Not yet deployed

- **v287 — the fixes from the 29 October audit, all eight groups.**
  **The worker folder DOES need copying this time** (AGENT_VERSION 1.33.0).

  1. **A listing is never uploaded twice.** The Windows machine now sends
     its "done" message on its own, and keeps it in a file to send again if
     the site does not answer. It never turns an upload that worked into a
     failure. The site also ignores a failure report for a picture it
     already has as uploaded, and RETRY skips uploaded pictures.
  2. **The site checks the step 2 rules itself.** SAVE on Approve Artwork
     refuses KEEP on a painting while step 2 is on, refuses REDO STEP 2
     while step 2 is off, and skips a picture from another project. The
     page already did this; a page opened before the catch-up did not.
  3. **A worker's save no longer locks the database during a download.**
     The site downloads the picture first and writes to the database after.
  4. **A picture OpenAI was paid for is never thrown away.** If filing it
     fails, the picture is kept and filed again on the next pass, instead
     of being painted and paid for a second time. A batch whose start
     request got no answer is looked up at OpenAI, never resent.
  5. **Zero means zero.** Smallest picture size 0 switches the check off,
     daily start hour 0 is midnight, rotation order 0 stays 0, and the
     listing check's pause 0 means no pause. Diagnostics no longer calls a
     daily limit of 0 a fault.
  6. **The counters agree.** Both NEEDS ATTENTION badges count the same
     things as the Needs Attention page, including step 2 giving up and a
     batch OpenAI refused. "Waiting to upload" on the home page counts only
     pictures with an upload queued. Unusable pictures have their own box
     on the Pipeline tab.
  7. **Keys.** On Approve Artwork the top-row 3 picks version 3 again;
     REDO STEP 2 is the number pad's 3. The Settings page refuses a key
     another key on the same screen already uses, and refuses 4 and 6.
  8. **Tests that could not fail now can.** The search-wording, tally,
     split and guard checks run the real code, and each was broken on
     purpose to prove it goes red.

  Titles already showing "partial" because their picture was retired stay
  that way until something on them changes again.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
