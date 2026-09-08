# Not yet deployed

## v162 — twelve worker/admin items from the owner's testing

1. **"(N/A)" on the phone** — the importer stopped writing it (v154) but
   rows imported before that still carry the text, and "N/A" is truthy so
   every `year ?` guard passed. A year is now only rendered if it looks
   like a year (four digits), at every display point.
2. **DONE and SKIP swapped** — SKIP sat closest to the search area where a
   thumb could throw a title away by accident. DONE takes that spot now.
3. **SAVE SELECTED keeps a busy state** ("LOADING IMAGE…") until the saved
   thumbnail is actually on screen, not just until the upload returns —
   the gap is why workers pressed it twice.
4. **Re-picking REPLACES** — saving another grid image while at the limit
   now offers "replace it with this one?" instead of dead-ending on "you
   already have 1 of 1". Only images the pipeline has NOT touched may be
   swapped; anything greenlit/processing/uploaded refuses with a reason,
   because that would be a retraction rather than a re-pick.
5. **Long-press a saved image on the phone** opens the big view instead of
   Chrome's own image menu, and the viewer fills the screen width.
6. **The results label shows only the owner's own words** — `{title}` and
   `{kind}` are stripped from the phrasing before it is displayed.
7. **Flagged images can be fixed from the search grid** — the correction
   card gains "🔍 FIND A REPLACEMENT" for in-page projects, which opens the
   title at the results; saving there replaces the flagged image through
   the same path as (4). Previously only a pasted URL could fix a flag,
   which is useless when the worker never has a URL.
8. **The chat badge is visible ON the sidebar** — the rail's group heading
   no longer fades its badge, and an unread count pulses until read.
9. **Approve Artwork caching** — images stay cached across visits and the
   cache for an image is dropped at exactly one moment: when it is
   approved and heads for upload.
10. **FAA titles keep the comma** — added to the surviving-character set,
    which matters now the listing title is the rich "Kyoto, Japan".
    Owner's own observation; re-read one live listing to confirm.
11. **Upscale quality, two real gains**: the print file is now encoded
    ONCE (flatten to a lossless intermediate, then enlarge straight into
    the final JPEG) instead of JPEG→re-read→JPEG, so small-size artefacts
    are no longer magnified 4x; and it saves with full colour resolution
    (subsampling 4:4:4), which is exactly what flat blocks with hard edges
    need. Default quality 92 → 95.

No schema change, no node copy.

**Verified**: preflight green; every touched Python file compiles; the JS
parses. **NOT verified**: none of it rendered — in particular the replace
prompt, the long-press viewer, and the upscale change want one real
generation to judge.

Nothing. Everything written is on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
