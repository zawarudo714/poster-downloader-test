# Not yet deployed

## v156 — the empty poster pane (v155 broke it), the 500's true cause, select-all

* **The painted image not showing was v155's own bug**: the master-image
  route's body read a `full` flag that only its SIBLING route declared — a
  NameError on every request, an empty pane on every poster. Fixed, and
  the CLASS is now impossible to ship again:
* **`check_undefined_names` rewrote from file-wide to PER-FUNCTION scope.**
  The old version pooled every bound name in the file, so any function's
  parameter vouched for the same name everywhere. Sabotage-tested with the
  exact v155 bug: red with it, green without.
* **The rewritten check immediately solved the owner's "Failed to open
  title: 500"**: `search_text` was imported inside ONE function and used
  bare in three others, `lock_title` among them — NameError on every title
  open since v146. Imported properly now. It also caught a second live
  NameError on the paste-a-URL allow-list path (`host` never bound) that
  would have fired the first time `allowed_image_hosts` was filled in.
* **Needs Attention has a select-all tick** in the header of each table,
  scoped to its own table.

No schema change, no node copy.

**Verified**: preflight green including the new scoped checker across the
whole codebase; the sabotage (re-inserting v155's bug) goes red and the
restore goes green. **NOT verified**: nothing rendered, as ever — but the
500 explanation fits the symptom exactly (title opened fine after refresh,
because the lock committed before the crash).

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
