# Not yet deployed

## v248 — a top-into-bottom background on Approve Artwork (2026-09-29)

- A background is now one colour or two: "#top/#bottom", the top colour
  blending evenly into the bottom one down the picture. One spelling,
  `imagefetch.normalise_background`; the painter builds the blend in
  `background_plate`, the screen shows it as a CSS linear-gradient on the
  picture's own box.
- New setting `gpt_background_color_bottom`, default #000000, on the
  Settings page under the top colour. `pipeline.default_background` is the
  one reader of the pair (painter, Approve Artwork, approval, Photopea).
- Approve Artwork: TOP and BOTTOM swatches, each with an eyedropper
  (E = top, Shift+E = bottom). Pictures still waiting that were painted
  with the old one-colour default now show the new blend; approving
  rebuilds their print file to match.
- The save door refuses a value that is not a background. New Diagnostics
  check `check_backgrounds_are_readable`.
- Files: `app/imagefetch.py`, `app/pipeline.py`, `app/gpt_worker.py`,
  `app/models.py` (comment only), `app/diagnostics.py`,
  `routes/pipeline_admin.py`, `static/js/admin_review_images.js`,
  `static/js/admin_pipeline.js`, `templates/admin_review_images.html`,
  `static/css/style.css`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
