# Not yet deployed

## v264 — PROMPT TEST MODE removed (2026-10-23)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- The owner keeps his current prompt; prompts are tested on the laptop
  (`../site_prompt_tester`). PROMPT TEST MODE is gone from Approve Artwork,
  the painter, the routes, Diagnostics and two settings.
- A RERUN paints straight away again, as before v249. Pictures that were
  waiting in the old pile now count as waiting to paint and paint once the
  pipeline is on RUN.
- No schema change. The old columns and tables stay, unread.
- To test: TO_TEST item 111.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
