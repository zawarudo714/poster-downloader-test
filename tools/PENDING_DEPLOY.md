# Not yet deployed

## v241 — USE MY OWN PICTURE reads pictures properly (2026-09-28)

- USE MY OWN PICTURE now reads the picture the way the search grid does
  (`imagefetch.normalise_picture`): the real format from the bytes, the size
  from Pillow, JPEG/PNG kept as they are, WebP/GIF converted to JPEG, and a
  plain sentence when the file is AVIF/HEIC or a web page. A website that
  refuses the server (403) now says "save it and use Choose File".
- `imghdr_lite.read_file_dimensions` falls back to Pillow when the quick
  64 KB read fails, so the worker paste doors can no longer skip the size
  floor on a JPEG with a big metadata block.
- New Diagnostics check: pictures saved without a measured size.
- Files: `app/imagefetch.py`, `app/imghdr_lite.py`, `routes/admin.py`,
  `diagnostics.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
