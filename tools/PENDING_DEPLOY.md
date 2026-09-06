# Not yet deployed

## v158 — the owner's evening findings, round two

* **RETURN TO WORKER is gone** — button, prompt and endpoint. His rule is
  absolute: the worker is paid at save time, nothing ever goes back. The
  rejected-image guidance now says what actually happens instead: replace
  the photo yourself on Worker Images, or retire the image.
* **The "broken" eyedropper was a visibility problem, not a logic one.**
  The coloured plate hugged the poster exactly, so on a mostly-opaque
  artwork a colour change changed nothing visible. The plate now fills
  the card width with the poster centred on it — same as the zoom view —
  so the chosen colour always shows around the art.
* **The zoom pane lag**: the display master was a 1000px PNG, still
  megabytes for full-art. It now goes out as WebP with alpha intact —
  roughly a tenth of the bytes — cached as before. (A failed conversion
  serves the PNG and skips the cache rather than mislabelling bytes.)
* **THE UPLOAD FAILURE IS A NEW FAA FORM, NOT TRANSPARENCY.** The node
  uploads the flattened print JPEG, never the transparent file. The log
  shows FAA serving a THIRD form variant — `updateartwork2026.html` —
  whose fields all matched but whose submit control does not. Built the
  fix the notes have demanded since the two-forms discovery: **a selector
  may now hold several candidates separated by `||`**, all polled
  together; whichever the served page has wins. The 2026 submit selector
  itself is NOT guessed (never invent a value aimed at a real
  marketplace) — the node captured the page at the moment of failure, so
  the owner sends the FAILURE EVIDENCE page dump from Diagnostics and the
  right candidate becomes a dashboard edit, no deploy.

* **The 2026 submit selector is now MEASURED, not guessed.** The owner
  saved the new form's HTML; its submit is `<a class="buttonSubmit">`
  calling `submitartworkform(...)`, and every other field name matches the
  old form exactly. The default `submit_button` selector now carries both
  candidates: the old form's div-anchor `||` the new form's
  `css:a.buttonSubmit`. Whichever page FAA serves, the node finds its
  button. (If a hand-edited selector override exists in the dashboard, it
  wins over this default — paste the same two-candidate value there.)

### THE NODE CHANGED — copy `worker_service/` to the Windows box

`AGENT_VERSION` is **1.31.0**; the Nodes tab confirms the copy landed. No
new installs.

### Verified

preflight green; both JS files parse; every touched Python file compiles.
NOT verified: never rendered; the `||` mechanism has not met a real page
(it degrades to exactly the old behaviour for any selector without `||`).

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
