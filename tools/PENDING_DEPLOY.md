# Not yet deployed

## v159 — clear-search-cache button (the CSV work happened in files, not code)
### v159 details

* **CLEAR SEARCH CACHE button** on the IMAGE SEARCH settings panel: forgets
  every stored search result so the same wording genuinely re-asks Brave.
  Confirms first, names the quota cost, reports how many rows it forgot.
  (Editing a phrasing already missed the cache — this is for re-running the
  SAME words.) Preflight caught the button before its handler existed,
  which is that check doing its job.
* **Catalogue files** (not code): 7 map-scale entries cut by the owner's
  "search gives only maps" test — Greenland, Guam, Borneo, Sahara, New
  Guinea, Sumatra, Tasmania — with Curaçao, Hong Kong, Gibraltar, Sicily,
  Sardinia, Crete and friends deliberately kept and written down. Then the
  ONE-TIME renumber closed every gap: 1..88,869 in traffic order.
  `renumber_catalogue.py` refuses to ever run casually again — after the
  coming import, gaps are correct and renumbering is forbidden, as the
  standing rule says.

No schema change, no node copy beyond v158's pending one (agent 1.31.0).


Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
