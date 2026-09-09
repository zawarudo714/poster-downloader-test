# Not yet deployed

## v170 — the signature and the blue: the preview was measuring the wrong box

**Server only. The Windows node is unchanged, so nothing needs copying.**

Three things the owner reported on the Approve Artwork screen, all one cause:
the signature sat outside the picture, the blue ran past the edges of the
artwork, and the mark was a different size zoomed than not.

The plate the poster sits on is deliberately BIGGER than the poster — wider
on a card, taller in the zoom. The server places the signature as a
percentage of the PICTURE. The preview was placing it as a percentage of the
PLATE. Two different boxes, so the mark landed on the coloured bar and came
out a different size in each view.

* **The colour moved off the plate and onto the picture.** A background
  paints its own element's box, and an image's box is the image, so the
  colour can no longer extend past the artwork.
* **The signature got its own layer**, `.sig-layer`, pinned to the poster's
  rendered box. Percentages in the preview now mean what they mean on the
  server. Dragging measures against it too.
* **A fourth fault nobody had reported, found on the way:** the bottom margin
  was `bottom: 0.5%`, which CSS resolves against the container's HEIGHT. The
  server uses `W * margin_pct` — the WIDTH. On a 4000x6000 poster the preview
  showed the mark 30 pixels up where the file puts it at 20. Now computed in
  pixels from the layer width, in one place.
* This REVERSES the v158 decision to let the plate show around the art. The
  reason for v158 was that colour changes were invisible on an opaque
  artwork; `probeTransparency()` now says so in words instead, which is the
  honest version of the same thing.

Mechanical checks:

* `check_overlay_sits_in_its_measuring_layer` — NEW. The mark must be built
  inside the layer it is measured against, at every place markup is built.
  It also fails loudly if the mark is ever renamed, rather than going green
  on an empty set.
* `check_no_background_on_composited_img` — REVISED, because moving the
  colour made its old question meaningless and it was passing for the wrong
  reason. It now fails on a CSS background on the picture OR on the plate.
* Two holes in that check were found by sabotage, not by reading it: the
  selector pattern was blind to `img[data-poster-img]`, and `transition:
  background-color` could read as a background being set.

**What this cannot prove:** that the layer really covers the picture on
screen. That is a fact about a rendered page, and nothing here renders. It
needs a person to look at it.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
