# Not yet deployed

## v211 — a hard 350px floor on every side, no override

Waiting to deploy. The node does NOT need copying, and NOTHING in the Chrome
extension changes — the size is measured on the server. Version 211 (live is
210).

The rule for a saved worker image changed, at the owner's instruction:

- A picture is now REFUSED when it is under 350px on ANY side. Every saved
  image is therefore at least 350 on both width and height.
- It is a HARD reject on every door — the in-page grid, the phone add-on, the
  paste-a-link box and the replace flow. There is no "save anyway" anywhere
  any more.

This is stricter than before. The old rule only refused a picture that was
small on BOTH sides (a thumbnail) and let a tall banner or a wide panorama
through. The owner chose the strict floor knowing it will turn away the
occasional genuine wide vista whose short side is under 350.

What changed, for a future session:

- `_too_small` in worker.py flipped from "both sides" (AND) to "either side"
  (OR). One shared test, read by all four size gates.
- The number is the existing dashboard setting min_image_px, default raised
  300 → 350. If a value is already stored on the dashboard it wins, so set it
  to 350 there too (the box is now labelled "Reject below this size").
- The "save anyway" override is gone: the paste and replace gates no longer
  consult confirm_low_quality, and the worker page (user.js) no longer offers
  the confirm — it just shows why the image was refused.
- "We could not measure it" still never rejects (unknown dimensions), same as
  before — only a measured, too-small picture is refused.

Why the extension is untouched: the add-on only sends a picture's address;
the server downloads it, measures it and refuses it, and the add-on already
handles that refusal. Nothing about size lives in the extension.

Verified: JS parses, Python compiles, no undefined names (this caught a
leftover `small` reference in the replace flow, now fixed), calls pass the
right arguments, every setting is still reachable and read. The full
preflight suite runs longer than the sandbox allows; the deploy tool runs it
in full before shipping.
