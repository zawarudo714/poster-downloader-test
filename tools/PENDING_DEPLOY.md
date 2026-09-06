# Not yet deployed

Nothing. Everything written is on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v146 — Google as the backstop, {kind} in the search, deep search gone

### Schema — ONE NEW COLUMN

`projects.has_source_link`, integer, default 0. In `NEW_COLUMNS`, added at
startup. Every existing project gets 0, which is correct: none of them had
an outside link except through the old derivation, and travel's spec sets
its own to 1.

### THE FIELD THAT WAS ANSWERING TWO QUESTIONS

The code worked out "does this project have an outside link" from "does it
NOT search in-page". One field, two questions — fine while every project was
one or the other, wrong the moment travel needed both.

Brave's picture catalogue is thinner than Google's (MEASURED by the owner,
2026-09-06, by using both), so travel now has the Brave grid as the first try
AND a Google button as the backstop. `has_source_link` says exactly that and
nothing else.

Four places decided by the old derivation and all four are corrected:
the source link, the paste-a-URL box, replace-by-URL on a saved image, and
replace-by-URL on a flagged one. **Nothing new was built for the paste box —
it already existed and was being hidden.** Finding that saved building a
second one beside it.

### {kind} — the sheet's description in the search

`{title}` is the place with its country: "Chicago Illinois USA".
`{kind}` is what it IS: city, island, mountain, beach.
So `{title} {kind}` searches "Chicago Illinois USA city", and adding a word
gives "Chicago Illinois USA city scenic".

**A placeholder, not glued on automatically**, and that was the owner's call
after I put the choice to him. Automatic would have taken away his ability to
test with the kind against without it, and he is still settling the wording.

The Google button builds its term the same way, from `google_query`, so both
searches ask for the same thing in the same words. A separate spelling there
would mean the Google button quietly looking for something else, which is the
hardest kind of difference to notice.

### DEEP SEARCH REMOVED

It fired two queries and merged them. The phrasing buttons do that job better
because each one is wording the owner chose. Gone from the settings, the
worker screen, the endpoint, the query builder and the cache.

**THE PAID BRAVE KEY STAYED.** It was never only for deep search — it is the
fallback when the free key is inside its one-per-second window or has spent
its monthly two thousand. Removing both together would have left a worker
looking at a rate-limit error instead of pictures. There is now a test that
fails if anybody removes it.

### Two settings that had no box now have one

`google_query` (new) and `source_search_url` (existed since the beginning,
never had a field anywhere). The boxless backlog is down from 18 to 17.

`source_search_url` is where the Google button goes — a setting rather than a
constant because it is somebody else's address and they can move it.

### Also

"No images found. Try DEEP SEARCH." now names what to actually do: try
another button, or open Google and paste an address, and if Google has
nothing either then SKIP and say why.

### Verified

* `preflight.py` green on all 21 checks — and it CAUGHT the missing box for
  `google_query` before I noticed, which is the check earning its place.
* Every touched Python file compiles; `user.js` and `admin_pipeline.js` pass
  `node --check`; tag counts balance in the worker template.
* `build_queries` run for real on seven cases: the kind lands where it is
  asked for, a template that does not ask for it is untouched, a MISSING kind
  leaves no double space, and curly apostrophes are still cleaned up.
* `tools/test_search_phrasings.py` extended — **run it after deploying:**
  `cd /opt/poster && docker compose exec web python tools/test_search_phrasings.py`
* **NOT verified:** the Google button has never been clicked, and no image
  has been pasted back from Google. Nothing has run against a real Brave key.

### The node

`worker_service/` is UNCHANGED. **No copy needed.**

---
