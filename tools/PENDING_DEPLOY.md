# Not yet deployed

- **v274 — the SWAP button says what one press really swaps.**
  - The button used to show every listing waiting for a new picture. One run only takes one account and at most 500, so the number could be bigger than what happened.
  - Now the button shows what one run takes. When more are waiting, a line under it says how many and that you press again after the run.
  - The check for "a swap is already running" now looks only at this project.
  - Nothing was pressed and no listing was touched while testing.
  - The worker folder does **not** need copying for this one.

- **v275 — the title KEEP key no longer removes a KEEP you already made.**
  - On a title with two pictures, if one was already KEEP, pressing KEEP for the title used to switch that one off and the other on.
  - Now it marks both. Pressing it again, when both are KEEP, clears both.
  - The same goes for the other title keys. UNUSABLE now asks for the reason once, not once per picture.
  - The worker folder does **not** need copying for this one.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
