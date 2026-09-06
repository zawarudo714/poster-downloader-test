# Not yet deployed

## v147 — TeePublic removed from the site entirely

**The owner's instruction, 2026-09-06:** the TeePublic mechanism causes too
many problems running on the site, so he will rebuild it as a Python GUI
tool on his laptop. Everything was saved FIRST: the full working code and a
mechanism write-up live in `../teepublic_tool/` beside this repo.

### What was deleted (~4,700 lines)

* The **TeePublic tab** — template, JS, and all of `store_admin.py`.
* The whole **store-health machinery**, server and node: scan, deactivate,
  reactivate, stages, the pipeline hold, the count check.
* The **interstitial-wall machinery**: recorded mouse paths, the recorder,
  `wall.py`, the wall panel on the Earnings screen, `clear_wall` on the node.
* The **TeePublic earnings reader**. TeePublic accounts still exist as rows
  but are skipped — the Earnings tab is FineArtAmerica only now.
* Eight **Diagnostics checks** that watched the store mechanism.
* Fifteen wall/scan/store **settings** from DEFAULTS, plus the TeePublic
  selectors map.
* `beautifulsoup4` from the node — nothing there parses HTML any more.

### What deliberately SURVIVES

* **The FAA listing check** — untouched, by instruction.
* **The FAA earnings read** — untouched.
* **The database tables** (`store_listings` and friends). They hold the only
  record of which designs were switched off and never switched back on.
  The models are gone so nothing touches them; the laptop tool will read
  them. Do NOT drop them.

### Schema

No new columns. The store-table migration rows were removed; on a fresh
database those tables simply are not created any more.

### THE NODE CHANGED — copy `worker_service/` to the Windows box

`AGENT_VERSION` bumped to **1.30.0**. The Nodes tab will confirm the copy
happened. No new pip installs are needed (a dependency was removed, not
added).

### Verified

* `preflight.py` green — and it CAUGHT a real casualty during the removal:
  cutting one node function took five neighbouring helpers with it, and the
  undefined-names check went red before anything shipped. Restored from git.
* Every touched Python file compiles; `admin_earnings.js` passes
  `node --check`; template tag balance checked by preflight.
* `tools/test_background_flatten.py` 21/21 locally.
* **Run after deploying:**
  `cd /opt/poster && docker compose exec web python tools/test_search_phrasings.py`
  (needs the container's installed packages; could not run in this sandbox).
* **NOT verified:** the Earnings page render with TeePublic accounts still
  present in the database — first click after deploy should be Earnings.

Also riding along: `TO_TEST.md` (new), roadmap renames (UI Revamp Part 1/2).

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
