# Not yet deployed

## v155 — Approve Artwork made fast, and the phone jump button

* **Approve Artwork loads fast now.** The recolourable posters were
  shipping their full transparent PNG master, re-fetched from the Storage
  Box on every view. The master now goes out at display size (1000px,
  alpha kept — live recolour and the eyedropper still work), and review
  images are cached on the server's own disk (`review_cache/`, safe to
  delete any time; a re-flatten clears its own entries so a changed colour
  can never show stale). The full print file still comes on request.
  The owner's "upscale only after approve" idea was considered and not
  taken: the upscale runs once at generation and every kept image needs it
  anyway — the waste was in what the BROWSER was sent.
* **The phone jump.** Tapping a title now scrolls straight to its work
  panel on a phone, and a floating "↓ TO THE TITLE" button appears
  whenever a title is open but its panel is off-screen — the DONE PICKING
  button's twin, pointing the other way.

No schema change, no node copy.

**Verified**: preflight green; JS parses; the re-flatten stale-cache hole
was found by reasoning and closed before shipping. **NOT verified**: never
rendered — the speed difference and the jump button are judged by eye.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
