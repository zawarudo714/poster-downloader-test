# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v143 — the two names a rule cannot rebuild

The titles table held ONE name. A title is three in practice: what the
worker reads, what gets searched for, and what the marketplace lists. The
movie niche could derive the last two, travel cannot — "Niagara Falls"
searches as "Niagara Falls USA" and "Taj Mahal" as "Taj Mahal Agra India",
decided by different rules across the whole catalogue, so no single pattern
reproduces both. The answer has to travel with the row.

* **Two nullable columns on `master_titles`** — `search_query` and
  `marketplace_title` — with their entries in `NEW_COLUMNS`.
* **`listing_name()` and `search_text()` in pipeline.py.** ONE place each
  for the fallback. Inlining `x.marketplace_title or x.title` at every use
  is how two callers end up disagreeing, which happened here before with
  the Chrome profile folder — one function defaulted it, another did not,
  and the cleanup never ran for any account for months.
* **Both fall back to the plain title**, so a sheet without the columns
  imports and behaves exactly as it did. That is the third-project test:
  a niche that does not need them is untouched.
* The importer reads them. The in-page search searches `search_text(t)`
  rather than `t.title`. `{title}` in the marketplace template renders the
  listing name.
* **APP_VERSION 143.** The last deploy shipped as a SECOND v142 against a
  different commit; the tool warned at the time and the log still shows
  both.
* Repo cleaned: the LibreOffice lock file is gone and `.gitignore` now
  covers those plus the catalogue CSV. `travel_titled.csv` — 21 MB of DATA
  that was going into every Docker image as a second copy of a file living
  one folder up — **still needs deleting by hand**, the mount refused.

## The invariant that ships with it

`check_sheet_columns_all_or_nothing`: within one project the two columns
are on every title or on none.

Both are optional and both fall back silently, and that is precisely what
makes a lost column invisible. Rename a header, or re-export a sheet that
drops one, and every title quietly searches on the bare name and lists
under the wrong one — no error, and the first sign is the listing checker
calling healthy listings missing, weeks later. A half-filled project is the
only tell there is.

**SABOTAGE-TESTED ONLY IN PART, and that matters.** The decision was lifted
out of the shipped source and exercised: it fires on a partial fill and
stays silent on a whole or empty project. The QUERY was not tested — it
needs sqlalchemy, and the machine this was written on has none.

    docker compose exec web python tools/test_sheet_columns_check.py

Run that before believing the check. Note that its own first version
pointed at `DB_PATH`, which is DERIVED from `DATABASE_URL` rather than read
from the environment — so it would have created and deleted rows in the
live catalogue. Its guard caught it. That is the argument for the guard.

## Deliberately not done

The worker screen still carries the movie-era columns. Year, content type,
votes and rating are dead for travel and must not appear; the kind
("city", "lake") belongs where the film description sat. Left for its own
change rather than bundled into a schema one.

## The node

`worker_service/` is UNCHANGED in this release. **No copy needed.**
