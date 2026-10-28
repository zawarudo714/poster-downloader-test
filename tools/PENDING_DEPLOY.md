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

- **v276 — deleting a payment can no longer leave a later payment charging for nothing.**
  - When you pay ahead, and a later payment takes money back for some of those pictures, the later payment depends on the first one.
  - Deleting the first one used to leave the later one still taking that money back, for money that was no longer recorded as paid.
  - Now the delete refuses and says which payment to delete first. The page shows that reason instead of "Delete failed."
  - A new Diagnostics check warns if a payment ever settles pictures that no payment paid for. Live today: no payment has taken money back, so nothing is affected.
  - The worker folder does **not** need copying for this one.

- **v277 — the search grid can no longer save onto a skipped or finished title.**
  - Skipping or finishing a title keeps the worker's name on it. The grid only checked the name, so an old open tab could still add a picture, and that picture counted for pay.
  - Now the grid refuses, with the same words as the paste box: "This title is marked skipped. Reopen it to add posters."
  - Fixing a flag is not affected. Going to a flagged title reopens it first.
  - Live today: this has never happened.
  - The worker folder does **not** need copying for this one.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
