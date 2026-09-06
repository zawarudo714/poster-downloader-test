# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v145 — the worker's photo beside the poster, and a background colour

### Schema — TWO NEW COLUMNS, both nullable

* `processed_images.master_path` — the transparent original, exactly as
  OpenAI returned it, kept un-enlarged.
* `processed_images.background_color` — the colour ALREADY flattened into
  `storage_path`. Not a request for one; a record of what was done.

Both are in `NEW_COLUMNS`, so startup adds them. Existing rows have NULL,
which reads as "this generation was opaque" — correct for every image made
before today.

### Why any of this exists

`background: transparent` is not a background setting. Asking for it changes
HOW gpt-image-2 renders, and the owner's whole poster look depends on it —
MEASURED 2026-09-05. The transparency is a side effect to be flattened away.

Most posters flatten correctly onto black. A few come back with a
semi-transparent sky, which goes muddy on black and reads correctly on its
own colour. Bangkok was the specimen.

### The review screen

* **The worker's photograph now shows beside the poster.** The API was
  already sending it and the screen simply never drew it — a display fix,
  not a new feature. It answers the one question the screen exists for: did
  the model paint the place the worker found, or invent a grander building
  of the same type.
* **A colour control per image**, with an eyedropper. The preview is the
  transparent original sitting on a coloured box, composited BY THE BROWSER
  — the same arithmetic the server does, so what you see is the finished
  poster rather than an approximation, and it updates with no round trip.
* The eyedropper samples the COMPOSITED picture. Click the Bangkok sky and
  you get the blue you can see, not the raw half-transparent value under it.
* Colours are held locally until SAVE, like the decisions, so dragging the
  picker costs nothing and you can change your mind three titles back.
* The control is hidden entirely when there is no transparent master — an
  opaque generation has nothing to recolour, and a dead knob is worse than
  no knob.

### The pipeline

* Generation now saves the raw transparent PNG as the master, flattens onto
  the dashboard's colour, THEN upscales.
* Approval re-renders **only if the colour actually changed** — nineteen in
  twenty keep the default, and re-rendering those would turn a batch of a
  hundred into minutes of pointless work.
* Re-rendering never calls OpenAI. That is the entire reason the master is
  kept: changing your mind costs a second of Pillow, not another picture.
* If a re-render fails the request errors rather than reporting a success it
  did not achieve. The old file is still in place, so nothing is lost.

### New dashboard setting

`gpt_background_color`, default `#000000`, on the IMAGE GENERATION panel.

### New Diagnostics check

`approved_images_have_a_background` — an approved image made from a
transparent generation must say what is behind it. The ordering already makes
this near-impossible, but the failure would be silent: the poster ships on
whatever colour it last had and merely looks slightly wrong. A money defect
wearing the clothes of a taste defect.

### Verified

* **By running:** `preflight.py` green on all 21 checks. Every touched Python
  file compiles. `admin_review_images.js` passes `node --check`. The
  compositing maths exercised on real pixels — a half-transparent pixel lands
  halfway to black, lands somewhere DIFFERENT on blue (which is the whole
  point of the eyedropper), a fully clear pixel becomes the background, and
  an opaque picture is untouched. Six junk colour values all fall back to
  black instead of raising.
* **A claim I had to withdraw.** I wrote, in four places, that upscaling
  before flattening leaves a dark fringe. Measured, the two orders came out
  within two levels of each other. Flatten-first is still what runs — it
  cannot fringe by construction and costs nothing — but the comments now say
  it is a safety argument rather than an observed fault, and the test reports
  the numbers instead of asserting a difference it could not show.
* **New file `tools/test_background_flatten.py`**, 20 checks with a sabotage.
  **He needs to run this one:**
  `docker compose exec web python tools/test_background_flatten.py`
* **NOT verified:** no poster has been generated, reviewed or recoloured
  through the real screen. The eyedropper has never been clicked.

### The node

`worker_service/` is UNCHANGED. **No copy needed.**
