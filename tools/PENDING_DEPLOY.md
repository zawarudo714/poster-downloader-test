# Not yet deployed

## v152 — the Background option the panel was already talking about

The IMAGE GENERATION panel's colour box said "when Background is set to
transparent above" — and there was no Background control above. Worse, the
API request never sent a `background` parameter at all, so generations were
running on the API's own default while the owner's whole look depends on
transparent (MEASURED 2026-09-05: it changes HOW the model paints).

* New setting `openai_background`, default **transparent**: transparent /
  auto / opaque. "auto" omits the parameter — which is exactly the
  omitted-parameter trap the moderation note describes, so the default is
  the value his tested prompt assumes.
* `gpt_images.generate()` now sends it whenever it is not "auto".
* The dashboard box sits between Quality and the flatten colour, where its
  neighbour's help text already pointed.

No schema change, no node copy.

**Verified**: preflight green; both Python files compile; the JS parses.
**NOT verified**: no real generation has run with the parameter — the next
TEST IMAGE GENERATION run proves it (the result should come back with
see-through areas and flatten onto the chosen colour).

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
