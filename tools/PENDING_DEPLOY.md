# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v185 — the swap endpoint now refuses a too-small picture · 2026-09-11

- **`/api/search_save` now refuses a picture under `min_image_px` on both
  sides**, the same hard floor the paste flow already had. This endpoint is
  what the phone add-on saves through (because it also does the
  one-per-title SWAP), and without this a tiny Google pick would have saved.
  The in-page Brave grid also uses this endpoint, but its pictures are
  pre-filtered above the Brave minimum, so this only ever catches a
  genuinely tiny image — a plain refuse, no confirm.

This pairs with the phone ADD-ON (v1.2, delivered as a zip beside the repo),
which now (1) sends the picture the worker actually TAPPED rather than
guessing which is open, and (2) saves through this swap endpoint so a second
SEND replaces the first image instead of erroring at the limit. The add-on
is loaded on the phone, not deployed here.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
