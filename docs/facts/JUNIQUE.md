# Junique (juniqe.com) — how the upload pages work

MEASURED 2026-11-05 from the owner's recording of 5 uploads
(`Downloads/upload_recording_juniqe.com_2026-11-05-14-34.json`, made with
`extensions/upload_recorder`) and his screenshots of the editor and console.
The upload tool (`tools/junique/junique_upload.py`) is built on these; if a
page changes, fix this file first, then the tool.

## Rules of the shop (owner, 2026-11-05)

- New shops are reviewed: 5–20 strong designs first. The 20 cap lifts after review.
- After approval the cap is 100 designs per shop ("8 out of maximum 100 designs",
  `MEASURED 2026-11-06`). Up to 3 shops are allowed (the "Add new shop" tooltip, read
  2026-11-06). The owner says each shop holds its own 100; that is not yet confirmed.
  The counter also counts deleted drafts. The owner is asking Junique by email whether
  more is given on request, since some artists show 500+ designs (2026-11-06).
- No Duplicate or Copy option exists; every design is set up from nothing.
- No automation terms were found. Clicking only, in a visible Chrome (DECIDED).
- The quality check refuses a plain enlargement as "upscaled"; v294 sharpen 80 +
  grain 4 passes (docs/FAILURES.md).

## The pages, in order

| Step | Page | What to click (stable handle) |
|---|---|---|
| 1 | `/creator-suite/designs` | `button[data-cy="add-new-design-button"]` "Add new design" |
| 2 | `/creator-suite/designs/<id>` (a draft exists from here on) | button "Create design" |
| 3 | `/configurator/?designId=<id>&locale=en-GB&mode=edit` "Choose your design approach" | button "Upload asset" (under "Upload a finished design file") |
| 4 | same | button "From this device" opens a script-made `input[type=file]` |
| 5 | same, tab `span` "2. Products" (`&displayMode=product`) | product cards, one switch, artboard, colour, drag |
| 6 | same | button "Save & exit", then "Save anyway" if the warning shows |
| 7 | `/creator-suite/designs/<id>` | `button[aria-label="Rename"]` → `input[placeholder="Name"]` (60 characters) → form button "Save" → toast "Successfully saved!" |
| 8 | same | `#v2-termsAndConditions` (rights box), description left empty |
| 9 | same | `button[data-cy="publish-button"]` "Publish" |

## Network signals (what "it worked" looks like)

- Upload: POST to S3 `jq-prod-euc1-upload-temp-image-ng`, then POST
  `/web-api/image-ng` (400 = quality check refused; an "Understood" modal
  shows), then POST `/web-api/collage-designer-assets/<id>` = file accepted.
- Rename: PUT `/creator-admin-backend/api/v1/designs/<id>` with the title;
  `isTitleTranslatable` false, `personalizable` false, `description` null.
- Publish: PUT the same with `areGuidelinesConfirmed: true`, then
  PATCH `/creator-admin-backend/api/v1/designs/<id>/status` → **204**. Only this
  counts as published.

## The products screen

- The cards are found by their name text; the list is a plain run of cards
  (card → second child holds the name). Names include a curly apostrophe
  ("Women’s Fitted T-Shirt Back").
- One on/off switch shows for the selected card: `input[type=checkbox]` with
  a generated id (`id-21`, `id-24` — not stable; find the visible one).
- On: Poster, both notebooks, Sticker, all apparel (adult, kids, baby), Tote
  Bag, Ceramic and Enamel Mug, Shower Curtain, Tea Towel, Beach towel (5:4),
  Fleece Blanket (1:1), Cushion (1:1), Backlit Film Print, PVC Banners,
  Wallpaper. Off: Baby Bib (added 2026-11-05), Sticker Sheet, Phone case, Towel, Beach towel (16:9),
  Fleece Blanket (4:3), Cushion (3:2), Doormat. (DECIDED by the owner.)
- A product with no artboard shows no on/off switch; under the preview it has "Front" and a "+". The "+" opens "Choose an artboard for this product" with "#1 (2:3)" (`MEASURED 2026-11-05`, owner's screenshots of draft 705221; the tool's drafts open this way, the owner's own did not).
- The handles (`MEASURED 2026-11-05`, draft 705231): the product cards are `div.ca-products > div.ca-products__product`, card = its 2nd child `.ca-customization-product` (`--disabled` while it has no artboard); the switch `input[type=checkbox][id^='id-']` sits in that card. The "+" is `.ca-change-canvas__add-canvas` inside the `.ca-preview-product-pages__product-page` whose `.product-page__page-label` is "Front". In the popup, click the tile `.ca-preview-page-thumb-tooltip__page-thumb`; its label `div.page-thumb-text` alone does nothing. A tile already chosen carries `--active`; clicking it does nothing and Escape does not close the box, but "Change" (`div.ca-change-canvas__change`) does (`MEASURED 2026-11-05`, draft 705270).
- Preview layouts (Konva, stage W×H, then image rects x,y,w,h; `MEASURED 2026-11-05`): Shower Curtain 375×417, poster 0,-146,375,563 and frame -8,-8,391,433; Notebook 276×383, poster 0,-2,276,414 and frame -24,-14,314,412; T-Shirt 142×189, poster 8,0,126,189 and shirt photo -101,-90,344,412. Frames and photos stick out on the left; the poster does not.
- Artboard: the Front row is `div.ca-change-canvas__text` ("#1 (2:3)") with
  `div.ca-change-canvas__change` beside it, which opens "Choose an artboard
  for this product" (options "#1 (2:3)", "Remove artboard"). Switching a
  product on assigns "#1 (2:3)" by itself.
- Colour: one setting for the design, `li[title="Black"]`, shown on the first
  product with a colour row (Notebook Back or T-Shirt Back in the recording).
- Scaling tabs `[data-testid="ui-tab-contain"]` / `[data-testid="ui-tab-cover"]`
  and "Reset all" exist; the owner leaves them alone.

## The drag (Shower Curtain and the like)

- The preview is Konva (`div.konvajs-content`, canvas 375×417 CSS px);
  `window.Konva` is reachable. About 53 stages exist (thumbnails); the preview
  is the one inside `[class*="ca-preview-canvas"]`.
- No Image node is Konva-draggable: the app moves the picture with its own
  mouse handling, so only a real mouse drag works (Selenium ActionChains).
- Shower Curtain opens with the poster at `[x 0, y -146, w 375, h 563]` and a
  frame image at `[-8, -8, 391, 433]`. Dragging the poster down 146 px (1:1)
  puts it at y 0, which is what the owner wants. Rule: a picture whose top is
  above 0 is dragged down until its top is 0, then read again.
