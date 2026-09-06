# Not yet deployed

## v160 — {LOCATION} was never filled in · rerun showed a stale image

* **{LOCATION} is now substituted before the prompt is sent.** It never was
  — the generation prompt went to OpenAI verbatim, so the model captioned
  the PHOTOGRAPH instead of your data and guessed the poster text ("JAPAN"
  for Mount Fuji). The full `title` column (the owner's choice) now replaces
  {LOCATION} (and {location}) in the prompt, on both the real run and TEST
  IMAGE GENERATION. This also stops the poster text drifting from the
  listing title.
* **Rerun showing the same picture was a stale cache, not a failed rerun.**
  The rerun really does generate a fresh image and overwrite the file — but
  the filename is deterministic, and the server's review cache is keyed on
  that path, so it kept serving the OLD bytes. No stale orphan file is
  created (the path is reused, overwritten in place); the only staleness
  was the cache. The review cache moved to its own module `app/review_cache.py`
  and is now CLEARED whenever a processed file is rewritten — by the
  generator on every rerun, and by the colour re-flatten (which already did
  a narrower version of this).

No schema change, no node copy.

**Verified**: preflight green; substitution unit-checked; both writers call
the shared clear(). **NOT verified**: a real rerun rendering fresh on screen
— that is the click to make after deploying.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
