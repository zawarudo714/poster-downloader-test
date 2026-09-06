# Not yet deployed

## v154 — the owner's thirteen finds, all fixed

He clicked for ten minutes and found what code-reading could not. Every
item below names its cause.

* **"Failed to open title: 500"** — the exact throw is not visible from
  here, but the lock now COMMITS before any decoration, logs the real
  traceback, and serves a working payload even if decoration fails. Run
  `cd /opt/poster && docker compose logs web --tail 100` after it happens
  once more and send the traceback — the cause gets its own fix.
* **"(N/A)" beside every place** — the IMPORTER wrote the literal text
  "N/A" into the year column; every screen's `year ?` guard passed because
  text is truthy. No-year now imports as truly empty. Existing rows clear
  on the re-import (or the one-line command in the chat).
* **"OPEN BRAVE IMAGE SEARCH" on a Google button** — the label was the
  in-page grid's source, not the link's destination. It now derives from
  where the link actually goes: "Open Google image search".
* **Raw `{title}{kind}` on the ALSO TRY chips** — chips now show only the
  phrasing's own words ("landscape"); the full template stays in the
  tooltip; the search itself is unchanged (still sent by index).
* **NOT RECEIVED asks first** — one tap of confirmation, because it sits
  thumb-distance from ACKNOWLEDGE on a phone.
* **Chat badges are red** now, admin and worker, including on the People
  group button.
* **Worker Images lightbox** — the place is named large at the top, with
  a position counter, on-screen ‹ › arrows and ← → keys stepping through
  every image of the day without closing.
* **REVIEW RERUNS stuck at 4** — it counted the superseded originals,
  which keep status "rerun" for ever as evidence. It now counts (and
  opens) fresh attempts awaiting review.
* **Approve Artwork reworked** — one big REVIEW EVERYTHING WAITING (n)
  button; the date range demoted to a side door; the worker photo and the
  poster now EQUAL side-by-side sizes; clicking either opens a full-screen
  compare with the title named, ← → moving between titles, Esc closing.
* **The dead colour control** — `has_transparency()` checked the file
  MODE, and gpt-image-2 returns RGBA even for fully opaque posters. It now
  checks actual pixels; and the review screen probes each image, swapping
  the colour bar for a plain sentence when colour cannot change anything.
  Images generated after the Background=transparent setting will recolour
  properly.
* **"being processed" hover overlap** — the card no longer lifts into the
  panel head; hover is a border change.
* **Photoshop/STALE wording on a GPT project** — the in-flight explainer
  now speaks plainly and names no software.
* **Stats page still lists GR/MUSIK** — not a code bug: those are project
  rows in the OLD test database, and the current registry no longer
  creates them. They vanish with the fresh database. Left alone on
  purpose.

### Schema / node

None. No node copy.

### Verified / not verified

preflight green; every touched file compiles and parses. NONE of this has
rendered — same limit as ever, which is why the fixes follow his sightings
so closely. The 500's true cause is still unconfirmed until the log line
arrives.

---

## v153 — THE MEGA AUDIT, plus the speed pass the owner asked for with it

The artefact is `MEGA_AUDIT.md` — what was asked, found, fixed, and (said
plainly) what was NOT covered. The changes that ship:

### Speed — the site should feel snappier, and slowness is now measurable

* **Static files cache for a year** (they were revalidated on every page —
  every URL carries `?v=`, so deploys still bust them instantly).
* **The database runs in WAL mode**: readers no longer wait for writers,
  which is exactly this site's shape (polling tabs + node + GPT worker).
* **The Settings screens stopped making ~200 tiny queries** per load; one
  query now fetches every override.
* **The dashboard no longer walks the workspace on disk at every load** —
  the tree panel loads on its LOAD TREE button.
* **Every response carries a Server-Timing header**, and Diagnostics has a
  new SLOWEST PAGES panel: the server's own milliseconds for the worst
  recent requests. A page that feels slow while its number is small is the
  network — the distinction that once cost an evening.

### The audit's one real catch, fixed two ways

Clearing the title list — or importing with REPLACE — while saved images
still pointed at it would ORPHAN those images: they vanish from every
count while their files and pay records stand. That is precisely the
owner's own upcoming re-import. Both paths now REFUSE, in plain words,
while living posters would be orphaned; and a new Diagnostics invariant
(`posters_without_title`) is the net under the guard.

### The second pass (same v153, owner's push-back)

The doors' wrong-section flash on load is fixed (the server now renders
each door's own section visible instead of letting the script correct it
after boot), and four of the week's new seams were traced clean — the
details are in MEGA_AUDIT.md's second-pass section.

### Schema / node

No schema change (WAL is a file property, set automatically). No node copy.

### Verified

* preflight green on all checks; every touched file compiles/parses.
* WAL persistence proven with a scratch database in the sandbox.
* `all_settings` resolution order kept byte-identical to `get_setting`
  (project → global → default; empty string means unset).
* **NOT verified**: no page has rendered with these changes; the orphan
  refusal has never fired for real. The re-import (item on TO_TEST) will
  exercise it: it should REFUSE only if saved images exist.

---

Nothing. Everything written is on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
