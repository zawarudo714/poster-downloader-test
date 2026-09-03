# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v144 — the owner's own search phrasings, and a switch for the reference image

Both of these exist so the owner can run experiments himself, in a browser,
without waiting for a deploy. Neither changes what any existing project does.

### The search phrasing buttons

* **New setting `brave_search_phrasings`.** One phrasing per line, and each
  line becomes ONE MORE BUTTON on the worker's search bar, in that order.
  Blank is the normal state and adds no buttons at all, so a project that
  never wanted this looks exactly as it did before.
* **The count is DERIVED from the lines, not stored.** He types three lines
  and gets three buttons; he deletes one and gets two. There is deliberately
  no separate "how many" field, because two records of one fact drift and the
  newer copy is the one that breaks.
* **The button shows the phrasing itself**, not a nickname. These exist to be
  compared against each other, and a button reading "TRY 2" tells you nothing
  about why its results differ from button 1.

### Three things that would have failed silently, and what stops them

* **A phrasing with no `{title}`.** It does not error — it searches the same
  literal words for every place in the catalogue and returns a full grid of
  real photographs of somewhere else, charging for each one. `set_setting()`
  now REFUSES it, and the refusal lives there rather than in the settings
  route so the import scripts cannot walk around it.
* **A cache keyed on the button NUMBER.** Editing phrasing 2 would leave
  yesterday's results sitting under today's wording — the grid fills, nothing
  errors, and the comparison he is running is against the phrasing he just
  replaced. The cache is keyed on a HASH OF THE WORDS instead, so an edit
  misses the cache by itself and there is nothing to clear.
* **The admin editing the list while a worker has the page open.** Button 3
  would then search whatever moved into slot 3. The server checks the index
  is still in range and answers "refresh the page" rather than searching
  something the worker did not press.

### The Brave settings had NO BOX ON THE DASHBOARD AT ALL

`brave_query_normal` and `brave_query_deep` were in the code defaults and
nowhere else. The one thing he most needs to experiment with was the one
thing he could not touch without a deploy — exactly the defect the standing
rule about the dashboard is about. There is now an **IMAGE SEARCH tab** on
the Pipeline page holding all five search settings.

It is its own tab rather than a panel inside PROCESSING because searching is
SOURCING, not post-production, and it only renders for projects whose
workers search inside the site.

### The style reference switch

* **New setting `openai_use_style_image`, default ON** — which is what every
  existing project already does. A default that changes behaviour on upgrade
  is not a default, it is a bug.
* Whether a reference is wanted is a property of the PROMPT, not the project
  and not the model. "Transform the style of the second image to the style of
  the first" needs two pictures; a prompt that describes the look in words
  needs one. He rewrites the prompt when a new model appears, so the switch
  has to sit next to the prompt where he can reach it.
* **`generate()` reads the switch itself.** The caller does not decide how
  many pictures go in the request, because two places holding one rule is the
  shape that produced the Chrome-profile bug.
* The "no style reference uploaded" error now only fires when a reference is
  actually wanted, and says which of the two things to fix.

### Two new Diagnostics invariants

* **`search_phrasings_name_a_place`** — every configured phrasing contains
  `{title}`. Watched as well as refused, because the refusal is new and the
  settings table is old: a value written before the guard existed walks past
  it.
* **`prompt_matches_style_toggle`** — the prompt and the switch must agree
  about how many pictures are being sent. A switch turned off while the
  prompt still says "the first image" produces a finished, paid-for picture
  that is quietly wrong rather than an error. Worded as worth a look rather
  than certainly wrong, because a prompt is prose and cannot be judged by a
  rule.

### Verified

* **By running:** `preflight.py` green on all 20 checks. Every touched Python
  file parses. Both JavaScript files pass `node --check`. Tag counts balance
  in both edited templates. The refusal guard and the cache-key rule were
  lifted out of the SHIPPED source and exercised on 7 cases plus 5 —
  all correct, including that the key is 14 characters against a 16-character
  column.
* **New file `tools/test_search_phrasings.py`**, 25 checks with 3 sabotages.
  **He needs to run this one** — it imports the app, which needs the
  container: `docker compose exec web python tools/test_search_phrasings.py`
* **Found by its own test:** the first version of the file list used a
  line-continuation, which was both harder to read and impossible to check.
  Rewritten as a plain if/else with both branches visible.
* **NOT verified:** nothing has been run against a database or against Brave.
  The buttons have never been pressed.

### The node

`worker_service/` is UNCHANGED in this release. **No copy needed.**

---

## v143 — LOG SAYS THIS IS ALREADY LIVE

`DEPLOY_LOG.md` records v143 at 2026-09-03 11:39, commit `80b64fa7`, so this
block has already shipped and the deploy tool simply did not clear it. Kept
rather than deleted, because deleting a record I cannot confirm is worse than
a duplicate. The next successful deploy empties this file anyway.

## v143 — two names a rule cannot rebuild, and the plan for what follows

### Schema

* **Two nullable columns on `master_titles`** — `search_query` and
  `marketplace_title` — with entries in `NEW_COLUMNS`. A title is ONE string
  in the table and THREE in practice: what the worker reads, what gets
  searched for, what the marketplace lists. The movie niche could derive the
  last two; travel cannot. "Niagara Falls" searches as "Niagara Falls USA"
  and "Taj Mahal" as "Taj Mahal Agra India" — different rules, no pattern
  reproduces both, so the sheet carries the answer.
* **`listing_name()` and `search_text()` in pipeline.py.** ONE place each for
  the fallback. Inlining `x.marketplace_title or x.title` at every use is how
  two callers disagree — which happened here with the Chrome profile folder,
  and the cleanup never ran for any account for months.
* Both fall back to the plain title, so a sheet without the columns imports
  and behaves exactly as before. That is the third-project test.

### Travel settings the owner stated, 2026-09-03

* `images_per_title` **1**, was 2 (a MUSIK placeholder).
* `title_template` **`{title}`**, was `{title} #{letter}` — which at one image
  per title would have listed every location as "Santorini #A". The suffix
  only ever existed to tell several images of one place apart.
* `keywords_static` **seven DESCRIPTIVE tags**, appended to what FAA
  generates rather than replacing it: travel, destination, tourism,
  landscape, scenery, landmark, world. Commercial words (wall art, home
  decor, gift for traveler) removed — they describe the product, which FAA
  already knows it is selling.
  **Every word here must be true of all 88,970 places**, because the same
  seven go on every listing: "town" is wrong on a mountain, "nature" wrong on
  a cathedral. The per-place word exists (the sheet's `description` column
  carries the kind) but this setting is static by design and cannot reach it.

### Two labels that were lying

* **Two nav tabs both read REVIEW IMAGES.** One renders "Review " plus the
  project's plural noun, and travel's noun IS "images". They are different
  jobs on different things — one judges the photograph the WORKER found, the
  other the artwork GPT MADE. The second is now **Approve Artwork**.
* **`DEPLOY_LOG.md` named no server.** Harmless with one box; a lie with two,
  which is now the plan. Worse, the de-duplication removed any entry with the
  same commit, so deploying one commit to test and then to production would
  have SILENTLY ERASED the test line. The host is now part of the line and
  part of the key, and the "same version, different commit" warning only
  compares against deploys to the same box. Sabotage-tested against the
  shipped rule.

### The invariant that ships with it

`check_sheet_columns_all_or_nothing`: within one project the two new columns
are on every title or on none. Both fall back silently, which is exactly what
makes a lost column invisible — rename a header and every title quietly
searches on the bare name and lists under the wrong one, with the first sign
being the listing checker calling healthy listings missing weeks later.

**Sabotage-tested and PASSING on the test box**, all five cases:

    docker compose exec web python tools/test_sheet_columns_check.py

Its own first version pointed at `DB_PATH`, which is DERIVED from
`DATABASE_URL` rather than read from the environment, so it would have
created and deleted rows in the live catalogue. Its guard caught that.

### Plan changes recorded in ROADMAP.md and CLAUDE.md

* **Two boxes, permanently.** `.34.144` is PRODUCTION, `.232.196` is TEST.
  New niches are proved on test first. Moving to production is stage 6,
  AFTER the Mega Audit and before the walkthrough — the owner's call, and
  the right one. Production has no worker on it and will not have one until
  travel is running, so the move is equally cheap whenever it happens, and
  moving early would mean two boxes to deploy to through weeks of changes.
* **THE MEGA AUDIT** is a new stage 5 — every planned audit as one pass,
  after the UI revamp, last thing before testing. Seven questions, written
  out in ROADMAP.md.
* **Ban recovery dropped** at the owner's instruction. Nobody owns Kyoto.
* **Listing check** folded into stage 3, QoL.

## The node

`worker_service/` is UNCHANGED in this release. **No copy needed.**
