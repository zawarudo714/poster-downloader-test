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
    3. QoL / STRUCTURE    what screens exist, what lives where
    4. UI REVAMP          how it looks, on a settled structure
    5. THE MEGA AUDIT     every audit at once, on a frozen structure
    6. MOVE TO PRODUCTION wipe .34.144, deploy the finished thing
    7. FULL WALKTHROUGH   owner as worker and as admin, ON PRODUCTION
    8. FIX WHAT 7 FINDS

**THE MOVE COMES LATE — DECIDED `2026-09-03`, and the first answer was
wrong.** It was briefly stage 3, on the argument that production has no
worker on it today so the move is free now and will not be free later.

The owner spotted the hole: **that window is not closing.** There is no
worker on production until travel is running, and travel is not running
until after the audit — so the move costs exactly the same whenever it
happens. Meanwhile moving early means TWO boxes through weeks of churn,
every deploy done twice or production left stale anyway, and every one of
those deploys a chance to make a mistake on the box that matters.

**Making those mistakes on the box that does not matter is what a test box
is for.**

It goes BEFORE the walkthrough rather than after, so the walkthrough happens
on production and doubles as the proof the move worked. The other way round,
production ends up being the one thing never tested.

**THE TWO BOXES, DECIDED `2026-09-03`.** `178.105.34.144` is PRODUCTION and
`178.105.232.196` is the TEST BOX, permanently. Everything new is proved on
test first and only then deployed to production. The reason is timing, not
tidiness: a new niche takes a week or two to bed in, and once travel is
earning there will be a worker on production every day whose work must not
stop while an idea is tried out.

Right now production has no worker on it, because the movie and music
niches are finished. That is why the move can wait: there is nobody to
interrupt, so nothing is lost by leaving it until the work is finished.

**Run `python tools/preflight.py` before every deploy in every stage.**

---

## 1. TRAVEL SPECIFICS — three of five settled, two left

| | |
|---|---|
| `images_per_title` | **1.** Stated 2026-09-03. Was 2, a MUSIK placeholder |
| `title_template` | **`{title}`.** No suffix, because one image needs none — and the catalogue was made unique on exactly this string |
| `keywords_static` | **Seven travel tags, appended** to what FAA generates, not replacing it. Stated 2026-09-03 |
| `brave_query_normal` | **STILL OPEN, but now HIS to answer.** Only the STYLING — `{title}` substitutes the sheet's `search_query`, which already carries its country |
| `openai_prompt` | **STILL OPEN.** How a sourced photo becomes artwork. Being tested in the OpenAI playground |

Also open: **a style reference image**, one picture showing the look every
poster should have — needed only if the prompt asks for one.

**BOTH REMAINING VALUES ARE NOW EXPERIMENTS HE CAN RUN HIMSELF, `2026-09-03`,
and that was the point of v144.** Neither can be reasoned out in advance, so
neither should have needed a developer.

* **The search wording.** Brave is a far smaller search engine than Google,
  and the same place answers very differently to different phrasing. There is
  now an IMAGE SEARCH tab holding all five Brave settings, plus
  `brave_search_phrasings` — one phrasing per line, each line becoming one
  more button on the worker's search bar. He types the sentences, presses the
  buttons on a real place, and compares what comes back.
* **The prompt.** `openai_use_style_image` switches the reference image on and
  off, because whether a reference is wanted is a property of the PROMPT
  rather than of the project, and he expects to rewrite the prompt whenever a
  new model appears.

**The master sheet is DONE and imported.** 88,970 places, numbered 1 to
88,970 in traffic order, built by `build_titles.py` and exported by
`make_check_file.py` as `IMPORT_titles.csv`. Read by hand in rank order;
9,131 places cut, 8,186 titles rewritten. Column `0` is the number, and it
prefixes the folder on disk — **assign once, never renumber.**

**The CATEGORY question is answered, and the answer was the careful one.**
The sheet carries the kind (city / lake / mountain — 17 values) in the
`description` column, which is the field the film plot used and which the
worker screen already renders. NOT `has_content_type`, which meant
movie-or-tv: one column meaning two things is the mistake this codebase
keeps paying for.

`search_query` and `marketplace_title` are new columns for the same reason
— see `MasterTitle`. No rule can rebuild them, so the sheet carries them.

## 2. RUN IT LOCALLY

`DEV_SETUP.bat`. Wipes and rebuilds a fresh database with the travel project
and 24 demo locations. **This is the first time any of the strip-down runs**,
so treat a clean boot as the real green light, not preflight.

What to click: log in as admin, land somewhere sensible, check every count
reads zero, open the Pipeline tab and confirm NO Photoshop settings and no
JSX editor appear, claim a title as the worker, confirm the in-page search
grid shows and there is no "Open <somewhere>" button.

## 3. QoL / STRUCTURE

**Diagnostics made actionable.** A count that never goes down stops being
read. Let a finding be SETTLED — acknowledge with a reason, it drops into a
"known and accepted" list, and the headline number comes to mean *new*.
Also: the disk walk belongs behind an explicit "run it" rather than a page
load.

**The master-level Accounts tab.** Already a stated plan, and the test case
for the retrofit audit — an account is the thing that has repeatedly turned
out to be plural, or to fail in more than one way at once.

**The listing check.** Moved here from its own item, 2026-09-03.

**Navigation and wording.** Every number names its scope. "17 of 1543" when
the run only covers 627 is the example that cost an evening.

**EIGHTEEN SETTINGS WITH NO BOX ANYWHERE, found mechanically 2026-09-03.**
They exist in `pipeline.DEFAULTS`, they work, and there is nowhere on any
screen to change them — so changing one means a code edit and a deploy. Among
them are the worker PAY RATE, the ALLOWED IMAGE HOSTS, the image cap per
title and the source search URL, all four of which this project's own notes
name as examples of what belongs on the dashboard.

They are listed by name in `SETTINGS_WITH_NO_BOX_YET` in `preflight.py`,
which warns about them on every run and FAILS on any new one. Giving each a
box is straightforward — a line in `SETTINGS_GROUPS` and a panel to hold it —
so this is a good, contained job for this stage. Delete each name from that
list as its box is built.

**The owner will expand this stage himself when it is reached.** What is
written here is a sketch, not the list — said plainly 2026-09-03 so a future
session does not treat these four bullets as the whole job.

## 4. UI REVAMP

Visual only, on a structure that has stopped moving.

**The risk in this stage is structural template edits**, the most expensive
class of bug in this project's history: removing a panel and leaving one
closing tag behind reparents everything below it, renders fine, parses fine,
and breaks a button on a different tab. `preflight.py` checks tag balance and
`data-` hooks; run it every time.

## 5. THE MEGA AUDIT

**Named and scoped by the owner, `2026-09-03`.** Every audit this project has
ever planned, done ONCE, together, as the last thing before he starts
testing. It comes AFTER the UI revamp on purpose: an audit of a structure
that is still moving has to be redone.

**Why one pass and not seven.** These audits kept being listed separately and
never started, because each one alone looks like a week nobody has. Together
they are one pass over the same code asking seven questions, and the answers
overlap — the field that cannot express a second user is usually the same
field whose two failure modes share one column.

### The seven questions

**1 · INTERACTION.** What else is true while this happens? For every action —
button, scheduled trigger, node job, stage transition — what is RUNNING at
the same time, what does it leave half-done if it stops midway, and what does
the far side do that we have not seen. Pay attention where two mechanisms
claim one resource: the node, an account, a browser profile, the pipeline
hold. Every serious bug so far has been at one of those seams. The specimen:
three correct mechanisms combining to deactivate 199 designs off a sweep that
had covered 199 of 1,543.

**2 · RETROFIT.** What will the SECOND user of this field need? Every column
naming one owner — can the thing it points at ever be plural? Every status
field — can the thing it describes fail in more than one way at once?
`project_id` on an account, one `paused_until` for uploading and for reading
money, one `action_error` for switching off and switching back on: each was
right for its first user, wrong for its second, and each hid a real fault
while looking fine.

**3 · DEAD MECHANISM.** What is on screen that cannot do anything here? Not a
search for unused code — an enumeration of every control on every screen,
asking "does this do anything in THIS project". A renamed button that still
links to TMDB is worse than an unrenamed one, because now it looks right.
`preflight.py --map` does the mechanical half.

**4 · SILENT FAILURE.** Where can something go wrong and say nothing? Every
place that claims work must report all three exits, including the one nobody
wrapped. Every counter that swallows an error. Every reply that is thrown
away. Every guard that depends on a flag somebody else sets.

**5 · THE OUTSIDE NUMBER.** Where do we check ourselves against something we
did not write? An invariant over our own data cannot catch our own data being
wrong — that is how a reactivation recorded 80 designs switched on while one
sat on the inactive tab and nothing internal disagreed. FAA prints a balance;
TeePublic prints an inactive count. Find every place a free outside number
exists and is not being used.

**6 · WORDS AND NUMBERS.** Read every screen as a stranger. Does each count
name its scope? Does each label mean one thing? "17 of 1543" when the run
covers 627, and two nav tabs both reading REVIEW IMAGES, are the two found so
far — both by the owner, which is the problem.

**7 · MONEY.** Follow one sale and one payment end to end. Anything that can
quietly cost money: a listing switched off and not back on, a title greenlit
without payment, a poster counted as owed that can never be paid, an account
paused by one failure and silenced for another.

### How it is done

Not "be thorough" — that is worthless. **Enumerate, then answer.** Write the
tree down as a file; the artefact IS the tree, not the fixes. A finding needs
a reproduction, a log line, or the path traced from PRODUCER to CONSUMER —
otherwise it is a question, and saying "these two look inconsistent, I could
not confirm it" is genuinely useful where a confident wrong diagnosis costs
an evening and teaches him to discount the true ones.

**Every fix ends with the two standing questions:** what would have caught
this without him, and what mechanical check is being added so the answer
changes next time.

### Not in scope

**Ban recovery.** Removed 2026-09-03 at the owner's instruction: this niche
will not be banned, because nobody owns Kyoto. If it ever is, the catalogue
and the upload records are enough to rebuild against a new account then.

## 6. MOVE TO PRODUCTION — wipe and deploy

The owner has chosen a genuine fresh start: **no snapshot kept**, image files
deleted, storage box emptied.

**This happens LATE, after the Mega Audit.** See "THE MOVE COMES LATE"
under The order for why the earlier answer was wrong. In short: production
has no worker today and will not have one until travel is running, so the
move is equally cheap whenever it happens — and doing it early would mean
carrying two boxes through weeks of deploys.

The whole path is already proved on the test box: deployed, wiped with
`reset_workflow.py --yes --wipe-titles`, 88,970 titles imported, the new
invariant clean.

1. Copy poster files to `S:` first if he changes his mind — decided against
2. `git pull` on `178.105.34.144`, `docker compose up -d --build`
3. Wipe. `reset_workflow.py --dry-run --wipe-titles` first, then `--yes
   --wipe-titles`. It backs up before touching anything and will demand
   `--force` on a production-looking database, which is correct
4. `scripts/create_admin.py` if the wipe left no admin
5. **COPY `worker_service/` TO THE WINDOWS NODE** if it changed. `next_deploy`
   says whether it did; the Nodes tab is the only proof the copy happened
6. Import `IMPORT_titles.csv`
7. Add the FineArtAmerica account, with the artist name typed in exactly as
   FAA holds it — the listing checker cannot derive it

**There is no migration step.** Startup runs `create_all`, `migrate_schema`,
`sync_projects` in that order.

**The test box is not decommissioned.** It stays, permanently, as where a
new niche is proved before production sees it. Both boxes now appear in
`DEPLOY_LOG.md` by host — before 2026-09-03 the line named no server, and
deploying one commit to both would have silently erased the first entry.

## 7. FULL WALKTHROUGH — on production

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
