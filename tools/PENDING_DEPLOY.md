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

- **v278 — deleting the last picture on a title now returns it to the pool cleanly.**
  - Before, the title went back to the pool but kept the first worker's start time, save date and folder.
  - The next worker could not press RETURN UNWORKED on it, and their picture was filed under the old date. That date could be in a week you had already paid.
  - Now everything from the first worker is cleared.
  - A finished title emptied this way is not changed. Diagnostics already shows it in red, and you choose to send it back or skip it.
  - A new Diagnostics check warns if a title in the pool still carries an old worker's start. Live today: none.
  - The worker folder does **not** need copying for this one.

- **v279 — a missing style picture no longer fails your photos for good.**
  - If the prompt is set to use a style picture and that file is missing, every photo in the send used to be marked as failed, permanently, as if each photo were the problem.
  - Now painting simply waits. Nothing is sent and no photo is marked failed.
  - The reason shows on the AI panel, and a new Diagnostics check turns red until the picture is uploaded again or the setting is switched off.
  - This applies to sends in batches and to one-at-a-time painting.
  - The worker folder does **not** need copying for this one.

- **v280 — two workers can no longer be handed the same title.**
  - If two workers pressed GET at the same moment, both could be told the same titles were theirs. Only the later one really kept them.
  - The same could happen when picking titles from the list.
  - Now each title is checked and claimed in one step. The worker who comes second gets fewer titles, or none, and can press GET again.
  - The worker folder does **not** need copying for this one.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
