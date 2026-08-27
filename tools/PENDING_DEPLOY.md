# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v128 — audit batch 2, the worker screens** · targets
178.105.232.196 (test box)

### A worker could not fix their own flagged image

`user.js` decided whether to show the paste-a-URL box from `PD.searchMode` —
the project the worker is STANDING IN. A flag card can be for a title in
their OTHER project, and then the two disagree.

Standing in MUSIK with a flagged movie poster, the check said "in-page, no
pasting" and REMOVED the box and the REPLACE button. Pasting a URL is the
only way a movie poster can be replaced, so the worker could look at their
flagged image and had no way to fix it. The reverse case shows a paste box
for an image that can only be re-picked from a grid.

The server was already sending the title's own mode with every revision
(`**_project_ui(db, mt.project_id)`), so this was reading the wrong one of
two values it already had. Fixed in three places; `buildPosterCard` now
takes the mode as an argument rather than being right by accident.

### `go_to_title` claimed a title with no permission check

Every worker route that hands out a title scopes it — `pull_next`,
`select_titles` and `release` all go through `_worker_project` and
`_scope_to_project`. `go_to_title` also hands out a title (it claims an
unclaimed one) and checked nothing, so a worker assigned only to MUSIK could
claim a movie title by id.

Not reachable by clicking, which is exactly why it survived. New
`_may_touch()` checks the title is in one of the worker's PERMITTED projects
— not the active one, because the flag card that leads here can legitimately
belong to their other project.

### The guard check could be satisfied by a COMMENT

Adding a `GUARDED` entry for the above exposed a hole in the safety net
itself. `check_guards_are_called` substring-matched the raw source, so the
comment `# See _may_touch().` counted as calling it: deleting the call left
the check green.

**All four entries had been exposed to this**, including the TeePublic wall
guard and the two external_id ones. It now takes the guards from the calls
the function actually makes, read out of the syntax tree.

Found by sabotage. Reading the check did not find it — and the first three
attempts at the sabotage silently failed to apply, which is why the final
one asserts that it landed before trusting the result.

### Files

`app/static/js/user.js`, `app/routes/worker.py`, `tools/preflight.py`,
`app/config.py` (127 → 128), `CLAUDE.md`.

**Verified:** preflight green, `user.js` parses, and the new guard
sabotage-tested — call removed → red, restored → green, with the other three
guards still passing throughout.

**Not verified:** none of it exercised against a real worker session. The
thing to click is a flag card while standing in the OTHER project — that is
the case that was broken.
