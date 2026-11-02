<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## WHERE THIS STANDS — `2026-09-01`, and read this before anything else

**There is ONE project: `travel` (Travel Locations).** Brave image search
in-page -> GPT Image 2 on the Linux server -> FineArtAmerica. Review gate on,
no year, no content type.

**Two earlier niches are GONE, and why matters more than that they went.**

FineArtAmerica closed both of the owner's accounts on 2026-08-28. The reason
was the CONTENT, stated by their staff in writing: movie posters and portraits
of real musicians, neither licensed. Their contributor terms require the
copyright in the source image AND a release for any identifiable person, and
their admin removes an ACCOUNT rather than an image once the violations mount
up.

Both accounts were reinstated after the owner deleted the work and said
plainly that he had no licences.

**This is the single most important constraint on everything built here.**
The test that decides whether a niche is viable is not visual and not
technical: *why would someone buy this?* If the answer names something
somebody else owns — a film, a band, a person, a brand — no amount of
processing fixes it, because the recognisability IS the product. Travel
locations pass: nobody owns Kyoto.

**Do NOT read the account closures as an automation problem.** That theory
was investigated at length and is wrong. Measured: the node made about 70
sign-in attempts in 13 days from the Contabo address, FAA does publicly say
it blocks automated login attempts, and none of that was the reason. The site
loads fine from both that address and the owner's home connection, so no IP
was ever blocked. Do not spend a session on proxies, residential IPs or
datacentre addresses; it has already been spent.

Still worth doing on its own merits: uploads cost ONE login per batch, and
the earnings read is a second. Reading the balance at the end of an upload
run — the browser is already signed in — takes a full day from about four
logins to one.

**MUSIK and the movie project are not archived, they are deleted from the
registry**, and the database they lived in is being replaced with an empty
one. Nothing needs unwinding. Comments and history referring to them are
being cleaned up as they are touched.

---

## The legacy archive and the old data — GONE `2026-09-01`

Both earlier niches are deleted and the database is being replaced with an
empty one. Everything that used to live here — the 4,865 hand-processed
movie images, the dot-truncated 44, the renumbered 10, the 101,605-row master
sheet, the migration tool and its rehearsal — described work that no longer
exists or a marketplace catalogue that has been taken down.

**What is worth carrying forward from it, because it was paid for twice:**

* **`external_id` is unique only INSIDE one project.** Every sheet starts
  again at 1. A lookup by `external_id` that is not scoped to a project will
  find another project's row, silently, and report confidently about the
  wrong data. Enforced by the GUARDED table in `preflight.py`.
* **Two records for one fact drift, and the newer copy is the one that
  breaks.** The old system kept JSON files beside the images; every one
  became a second record that could disagree with the database.
* **A file rename does not update the record that points at it.** Renaming
  44 files on disk fixed nothing until the JSON keys were rewritten too.
  The row-level twin struck 2026-09-13: the grid swap stood down a flagged
  image and created a successor ROW, and the flag stayed pinned to the
  dead row — so the admin's card showed a deleted-file placeholder while
  the image the decision was about lived on a row no screen rendered.
  **When one thing replaces another, enumerate what still points at the
  old one and move each pointer** — records, flags, list memberships —
  in the same transaction as the swap. It struck a third time on
  2026-09-15: the paste-REPLACE flow swaps the picture on the SAME row,
  and the place check (built believing a row's file was immutable) kept
  showing Google's verdict for the previous picture. The 09-15 audit
  caught it; the replace flow now clears every fact about the old bytes
  — place-check fields and content_hash — and re-checks. The question to
  ask any new per-image fact: "what happens to me when replace_poster
  swaps the file under my row?"
  A FOURTH cousin, 2026-09-17, is the same shape in the BROWSER: an
  in-place re-render that rebuilds one small node must recompute every
  DERIVED thing that depended on the changed data — including PARENT-level
  state the small render never touches. `rerenderPosterCard` rebuilt one
  image cell on flag/unflag, but the title's red outline (`.g-title.flagged`,
  set once from `needs_revision`) was a parent the cell rebuild did not
  reach, so a resolved flag left the title ringed red until a full reload.
  When you replace part of a rendered tree in place, list what the OLD render
  computed from the same data and redo each — the parent indicator, the
  counter, the summary — not only the node you swapped. And a DENORMALISED
  flag like `needs_revision`, kept in step by hand across many paths, earns a
  Diagnostics watcher that compares it against the live rows it summarises
  (`check_needs_revision_matches_open_flags`), because "kept in step by hand"
  is the definition of a thing that drifts. It drifted on 2026-09-23 exactly as
  predicted: six doors each computed the marker with their own copy of the
  question, two copies forgot to skip DELETED pictures, and titles with 0
  saved wore a red FLAG for ever — the worker avoided them. The fix was
  ONE question (`utils.live_flag_title_ids`) asked by every door — the
  same fix as the two NEEDS ATTENTION badges, which each added their own
  sum and both left out step 2 giving up (v287: `pipeline.attention_count`,
  also behind the home page's "waiting to upload"
  `waiting_to_upload_count`; `tools/test_attention_count.py`), the
  worker's tag DERIVED from it at read time (which repaired the stuck
  rows with no script), and `check_flag_marker_uses_one_definition` in
  preflight, which found the sixth door on its first run. A seventh door
  set the marker with NO flag behind it at all — SEND BACK on a deleted
  picture — so Diagnostics called it stale and the worker could never
  clear it; removed in v231, and the admin note carries that message. A
  marker must mean one thing, even when setting it looks helpful. And
  **fixing a door never repairs the rows it already wrote**: the worker's
  list derived the tag and looked fixed, while Worker Images read the
  stored copy and kept Beirut ringed red with no flag (owner, 2026-09-27).
  v232 derives the outline there too and runs `resync_flag_markers` at
  startup. When you fix what WRITES a stored copy, ask what repairs the
  copies already written, and which screens still read them.
* **"No tool is needed" was a judgement about cost, written in the same voice
  as the measured figures around it.** Nothing on the page distinguished
  them. That is what the provenance tags are for.

## Data state — `2026-09-01`

**Starting from an empty database.** No master titles, no posters, no
payment runs, no users beyond the admin that gets created, no accounts.

The live box `178.105.34.144` still holds the old sourcing-only build (v15,
101,605 movie titles, ~10,700 posters, 15 payment runs). It is to be wiped,
not migrated: the owner has chosen a genuine fresh start, keeping no snapshot.
Every count that follows the wipe should be zero, and any screen that shows
otherwise is a defect worth chasing rather than a leftover to explain away.

