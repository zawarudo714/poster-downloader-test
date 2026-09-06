# Not yet deployed

## v148 — UI Revamp Part 1: home page, pulse strip, nav groups, Pipeline split

### The Pipeline page is now THREE DOORS

The old page mixed watching, deciding and configuring. The project nav now
has:

* **Greenlight** — its own tab, because it is a work queue like Worker
  Images. Its badge shows how many posters are waiting for the word.
* **Pipeline** — the live room only: Overview, Needs Attention, Nodes.
* **Settings** — Image Search, Processing, Upload, Test & Debug.

Under the hood it is still ONE template and ONE script — each door shows its
own tab buttons, every section's markup renders everywhere, and a click that
targets another door's section simply navigates there. A real three-file
split was rejected as the most expensive class of edit this project knows,
for zero extra behaviour. Old `/admin/pipeline#upload`-style links redirect
to the right door, and the Diagnostics links were updated.

### What is new on screen

* **Opening a project now lands on a HOME page** (`/admin/home`): a journey
  strip showing every stage of the pipeline with live counts (each number is
  a link), and "waiting on you" cards that only appear when their count is
  above zero. The old behaviour dropped you straight into Worker Images.
* **A live status strip under the top bar, on every admin screen**: worker
  machine on/off, what it is doing, quiet window, workers online — plus red
  alarm lines from ANYWHERE (machine offline, paused accounts, failed
  uploads, pipeline halted), each a link to the right screen.
* **The master nav is five items instead of ten**: Dashboard · Money
  (Payments, Earnings) · Marketplace (Listing check) · People (Chat, Users,
  Activity Log) · System (Backups, All-Project Stats, Diagnostics). Dropdowns
  on desktop; headed, always-open sections in the phone drawer. The chat
  badge also shows on the People button so it is never hidden.
* **Renames**: "Review Images" is now "Worker Images" (it judges what the
  worker found; Approve Artwork judges what the machine painted); the master
  "Stats" is "All-Project Stats".
* **Nav badges**: Worker Images, Changes Requested, Approve Artwork and
  Pipeline now carry live counts of what is waiting.
* **One poll feeds all of it**: `/admin/api/pulse`, every 15 seconds per
  tab. Anything new that wants live data should ride in it, not add a timer.
* Mobile: wide tables scroll sideways instead of squeezing; tiny buttons are
  thumb-sized on touch; the review screen's commit bar sticks to the bottom.
* The Worker Images empty state now says what to do next; RETURN ALL asks a
  question that names what it includes.

### Schema

None. No node change either — **no folder copy this time.**

### Verified

* `preflight.py` green on every check; all new and touched JS passes
  `node --check`; template tags balance; every touched Python file compiles.
* **NOT verified — and this matters more than usual**: the new
  `/admin/api/pulse` endpoint, the home page, and the three Pipeline doors
  have NEVER RUN against a database. The queries follow the codebase's own scoping patterns
  (`scope_titles`, `scalar_subquery`), but the first click after deploying
  should be opening the Travel project and watching the home page fill in.
  If the strip stays on "Loading…", the endpoint is failing — the browser's
  console (F12) will show the error to send me.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
