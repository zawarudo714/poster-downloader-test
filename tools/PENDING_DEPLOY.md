# Not yet deployed

## v157 — invisible failures made visible, and the review pane truly light

* **"Failed processing with nothing anywhere saying why."** The Needs
  Attention panel had buckets for `[auth]`/`[billing]`, `[rejected]`, and
  "everything untagged" — but `[bad_request]`, a real kind the classifier
  emits, matched NO bucket. Six failures showed as a badge of 6 and a
  panel explaining none of them. The catch-all now excludes only the kinds
  a bucket above already displays, so a kind nobody anticipated lands
  there WITH its full error text instead of vanishing. (And the NULL-error
  case is kept via `or_(IS NULL, …)` — three bare NOT-LIKEs would have
  silently dropped every failure with no text, recreating the bug.)
* **The review pane was STILL loading 4000×6000 for some rows** — any
  processed image whose preview_path is empty fell back to the print
  file. The route now refuses to send print pixels at all: anything big
  is downscaled to 1200px on the way out, once, and cached. It no longer
  matters why a preview is missing.

No schema change, no node copy.

**Verified**: preflight green (including the per-function undefined-name
checker). **NOT verified**: why those six generations failed — the error
text will be readable on Needs Attention after this deploys, and the
command in the chat reads it straight from the database right now.

---

Nothing. Everything written is on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
