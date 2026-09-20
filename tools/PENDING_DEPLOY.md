# Not yet deployed

**v221 — some kinds search without their word (waiting to deploy).**
- New dashboard setting `search_kind_omit` (Settings page, beside the
  SEARCH/GOOGLE query boxes), default "island": kinds listed there have
  their {kind} word left out of every built search — SEARCH button,
  phrasing buttons and GOOGLE button alike, applied once inside
  `build_queries()` so the three can never disagree. "Mallorca Spain
  island" was finding anonymous landmass shots.
- Note for testing: the search CACHE keeps old results for up to 24 hours —
  press the refresh/re-search to see the new words at once. The query line
  printed above the grid should show no "island" on island titles.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
