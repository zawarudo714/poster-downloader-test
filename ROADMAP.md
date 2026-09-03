# The parked list — everything outstanding, in order

---

## WHERE THIS STANDS — `2026-09-01`

**The system was stripped to a single project, `travel` (Travel Locations),
on 2026-09-01.** Brave in-page search -> GPT Image 2 on the Linux server ->
FineArtAmerica. Review gate on, no year, no content type.

Both earlier niches are gone. FineArtAmerica closed the owner's two accounts
on 2026-08-28 over the CONTENT — unlicensed film posters and portraits of real
musicians. Their staff said so in writing. Both accounts were reinstated once
the work was deleted and he told them plainly he had no licences.

The full reasoning, and the test for whether a future niche is viable, is in
`CLAUDE.md` under WHERE THIS STANDS. Read it before proposing subject matter.

### What the strip-down actually did

* `PROJECT_DEFS` holds ONE project. `DEFAULT_PROJECT_SLUG` is `travel`.
* Global DEFAULTS made niche-neutral, because a global default is what the
  NEXT project inherits before anyone configures it: `title_template` lost
  `{year}`, `keywords_static` is blank, `source_search_url` is blank, the
  Brave queries are generic, and the GPT prompt no longer says "zoom in to
  the upper body areas" — correct for portraits, wrong for a landscape.
* `build_queries()` now accepts `{title}` as well as `{artist}`. The old
  placeholder was hardcoded and is a music word; a travel operator would have
  typed `{title}` for a mountain and silently searched for that literal text.
* DELETED as dead with the movie catalogue: `archive_index` and everything
  behind it (two admin endpoints, two node endpoints, the READ THE STORAGE
  BOX panel, its setting and its tests) · `scripts/migrate_pipeline.py`, all
  920 lines · `backfill_upload_status.py` · `fix_crossproject_processing.py`
  · `tools/migrate_gui.py` · the rehearsal database and note ·
  `fineartamerica-bulk-delete.md`.
* Photoshop is DORMANT, not deleted — the owner may want it for a future
  project. It is hidden by `processor`, and the test panels no longer fall
  back to assuming Photoshop when there is no active project.
* Dev seed data is travel locations, chosen to keep the awkward-character
  coverage (accents, apostrophes, a colon, a þ) that the old movie list had.

**Verified by running:** preflight green on all 20 checks · 42 behaviour
tests pass · all 10 sabotages still caught · everything compiles.
**NOT verified:** the site has never been run against a database. That is
step 2 below and only the owner can do it.

---

## The order

    1. TRAVEL SPECIFICS   the owner states them; nothing else can start
    2. RUN IT LOCALLY     DEV_SETUP.bat — first proof it boots at all
    3. WIPE AND DEPLOY    main becomes an empty database on current code
    4. QoL / STRUCTURE    what screens exist, what lives where
    5. UI REVAMP          how it looks, on a settled structure
    6. FULL WALKTHROUGH   owner as worker and as admin, every motion
    7. FIX WHAT 6 FINDS

**Run `python tools/preflight.py` before every deploy in every stage.**

---

## 1. TRAVEL SPECIFICS — blocked on the owner

Five values, none of which can be guessed. Placeholders are in
`PROJECT_DEFS` and marked STILL TO BE SPECIFIED:

| | |
|---|---|
| `images_per_title` | How many images a worker saves per location. 2 is a placeholder. Sets the size of the whole catalogue |
| `brave_query_normal` | What the worker's SEARCH button asks for. `{title}` is the placeholder |
| `keywords_static` | Tags appended to every FAA listing. Published publicly under his name |
| `openai_prompt` | How a sourced photo becomes artwork |
| `title_template` | Currently `{title} #{letter}` |

Also needed: **the master sheet itself.** The importer takes a `title`
column, or a single-column sheet, or one named `location` / `place` /
`destination`. Everything else in the row is optional.

**Open question he raised and deferred:** whether the sheet carries a
CATEGORY (park / city / lake / mountain). If it does, that is a NEW field —
not a reuse of `has_content_type`, which meant movie-or-tv. One column
meaning two things is the mistake this codebase keeps paying for.

## 2. RUN IT LOCALLY

`DEV_SETUP.bat`. Wipes and rebuilds a fresh database with the travel project
and 24 demo locations. **This is the first time any of the strip-down runs**,
so treat a clean boot as the real green light, not preflight.

What to click: log in as admin, land somewhere sensible, check every count
reads zero, open the Pipeline tab and confirm NO Photoshop settings and no
JSX editor appear, claim a title as the worker, confirm the in-page search
grid shows and there is no "Open <somewhere>" button.

## 3. WIPE AND DEPLOY

The owner has chosen a genuine fresh start: **no snapshot kept**, image files
deleted, storage box emptied.

1. Copy poster files to `S:` first if he changes his mind — decided against
2. `git pull` on `178.105.34.144`, `docker compose up -d --build`
3. Replace `poster.db` with an empty file; startup creates everything
4. `scripts/create_admin.py`
5. **COPY `worker_service/` TO THE WINDOWS NODE.** `AGENT_VERSION` is
   1.29.0; the Nodes tab is the only proof the copy happened
6. Import the travel master sheet
7. Add the FineArtAmerica account, with the artist name typed in exactly as
   FAA holds it — the listing checker cannot derive it

**There is no migration step.** Startup runs `create_all`, `migrate_schema`,
`sync_projects` in that order.

## 4. QoL / STRUCTURE

**Diagnostics made actionable.** A count that never goes down stops being
read. Let a finding be SETTLED — acknowledge with a reason, it drops into a
"known and accepted" list, and the headline number comes to mean *new*.
Also: the disk walk belongs behind an explicit "run it" rather than a page
load.

**The master-level Accounts tab.** Already a stated plan, and the test case
for the retrofit audit — an account is the thing that has repeatedly turned
out to be plural, or to fail in more than one way at once.

**The retrofit audit** (`CLAUDE.md` item 8). Same question this stage already
asks. For every field and mechanism: what will the SECOND user need, and can
it express that today?

**Navigation and wording.** Every number names its scope. "17 of 1543" when
the run only covers 627 is the example that cost an evening.

**Audit `routes/pipeline_admin.py`** — ~3,500 lines, never read by any audit
pass, and travel runs through it. The last genuinely unexamined surface.

## 5. UI REVAMP

Visual only, on a structure that has stopped moving.

**The risk in this stage is structural template edits**, the most expensive
class of bug in this project's history: removing a panel and leaving one
closing tag behind reparents everything below it, renders fine, parses fine,
and breaks a button on a different tab. `preflight.py` checks tag balance and
`data-` hooks; run it every time.

## 6. FULL WALKTHROUGH

Owner as worker and as admin, every motion, compiling an error list as he
goes. **This is the interaction audit (`CLAUDE.md` item 7) done by walking
rather than by writing** — the better version, because a written trace
inherits whatever the writer failed to imagine.

Seed it with the seams already known to leak: the quiet window against a
scheduled earnings read; an account paused for uploading while being read for
money; the node going offline in each stage; a marketplace serving
maintenance to either reader.

**Whatever this finds, ask the two standing questions:** what would have
caught it without him, and what mechanical check is being added so the answer
changes.

---

## PARKED — the owner will say when

**Remove TeePublic from the server.** Roughly 20 settings, `store_health.py`
(~55k), the TeePublic tab, all `wall_*` / `scan_*` / `store_*` machinery. His
reasoning: they no longer get through the wall reliably, and repeated failed
attempts risk drawing the same kind of attention FAA gave him. He plans to
run the earnings check, scan, deactivate and reactivate tools **locally on
his laptop**, where there is no wall.

**Photoshop.** Dormant and hidden. Comes back as a project's `processor` if a
future niche wants it — no rebuild needed.

**The workspace migration** (`app/workspace_migration.py`) and the legacy
path fallback in `saved_poster_folder()`. Both exist to move a
`{worker}/{date}` tree to `{project}/{worker}/{date}`. On a fresh install
that layout never existed, so both are dead — but the fallback sits on the
path every gallery thumbnail resolves through, so it deserves its own small
change with its own verification rather than riding along with a wipe.

---

## CARRIED FORWARD — measured things still worth knowing

**Earnings logins.** Uploads cost ONE login per batch, not per image; with
`upload_batch_size` 40 and a daily limit of 100 that is three, plus one for
the earnings read. The cheap improvement is to read the balance at the END of
an upload run, where the browser is already signed in — about four logins a
day down to one. The BURST is what looks bad, not the total.

**FAA account closures were about content, not automation.** The site loads
fine from both the Contabo node and the owner's home connection, so no IP was
ever blocked. Do not spend a session on proxies or residential IPs.

**Ban recovery assumes a surviving sibling account.** Both accounts were
closed at the same moment by one person for one reason, and there was nowhere
to hand over to. Not yet addressed.

**Confirmed NOT bugs — do not spend time here.** The "six stranded jobs"
(five, all killed by agent restarts, fixed by `release_claims_for_node()`) ·
the "silent 400 loop" (same event, self-resolved) · site speed (it was the
network link to Kenya, since recovered) · Cloudflare on TeePublic (genuinely
gone) · the 157 "No publish button" errors (one event, already fixed).
