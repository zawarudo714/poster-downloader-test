# Not yet deployed

## v171 — Approve Artwork: the sliders, the vertical position, and keeping your work

**Server only. The Windows node is unchanged, so nothing needs copying.**
**This adds a database column, so back up `poster.db` before deploying.**

* **The overlay no longer opens when you touch a slider.** `data-zoom-open`
  was on the whole card, with an exception list naming the colour bar and the
  version buttons. The signature bar was added later and nobody extended that
  list. The attribute now sits on the pictures themselves, so there is no list
  to keep up to date.
* **A vertical slider**, labelled "height". `signature_y_pct` is the gap from
  the bottom of the mark to the bottom of the poster, measured in percent of
  the poster's WIDTH — the same unit as the margin, so 0.5 in one box and 0.5
  in the other are the same visible distance. It defaults to the margin, which
  is exactly where the mark has always sat, so nothing already placed moves.
* **Dragging is still horizontal only.** He asked for a vertical slider, not
  vertical dragging, and adding it would mean a slightly wobbly sideways drag
  quietly lifts the mark off its line.
* **FAR LEFT and FAR RIGHT buttons**, plus `signature_key_left` (`,`) and
  `signature_key_right` (`.`), both editable on the Settings page. The keys are
  checked BEFORE the fixed letter keys, so setting one to `k` throws the mark
  instead of silently approving.
* **Tweaks now survive leaving the screen.** A new endpoint,
  `/api/review/remember`, writes the colour and the signature onto the row as
  you change them. It approves nothing and builds nothing.
* **`ProcessedImage.background_chosen` is a NEW COLUMN** beside
  `background_color`. They are not two records of one fact: one is what you
  picked, the other is what was flattened into the file, and
  `_build_print_file` compares them to decide whether it has work to do.
  Writing a preference into the painted column would have told the builder
  the job was done and shipped the old colour in silence.
* **KEEP / RERUN / UNUSABLE are kept in the browser**, not on the server. A
  decision is unsent intent, and a "decided but not released" row is a state
  the greenlight query, the funnel counts and the worker machine know nothing
  about.
* Three copies of the signature key list became one: both endpoints now read
  `signature.NUMERIC_KEYS` instead of carrying their own tuple. Adding a new
  adjustment is now one edit rather than three.

Mechanical checks:

* `check_click_targets_do_not_swallow_controls` — NEW. A whole-region click
  target may not contain a slider or a button. Sabotage-tested by putting the
  original bug back, and it also reports blindness if the hook is renamed.
* `check_chosen_colour_was_painted` — NEW invariant in Diagnostics. Once
  released, the colour chosen must equal the colour painted.
* The two new settings were confirmed to fail before deploy if their boxes are
  removed.

**What could not be checked here:** whether the sliders feel right, and
whether the vertical position lands where you expect in the finished file.
Nothing in this environment renders a page or builds a poster.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
