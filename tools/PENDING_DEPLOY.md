# Not yet deployed

- **v286 — a batch sent to OpenAI is never sent twice, and SAVE no longer
  blocks the database.** Batch #52 reached OpenAI, but the site could not
  write down OpenAI's receipt while a long SAVE on Approve Artwork held the
  database. Now SAVE saves each picture's decision on its own, and a batch
  stuck on "sending to OpenAI" is looked up at OpenAI: if OpenAI has it, the
  site waits for its answers; if not, its pictures go back in the queue.
  After this deploy batch #52's 4 pictures should come in within a few
  minutes. Server only — the worker folder does NOT need copying.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
