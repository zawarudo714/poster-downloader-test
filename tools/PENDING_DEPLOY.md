# Not yet deployed

**v167 — the signature, the print file moved to approval, a recall tool for
testing, and the approve screen's counts and speed.**

**THERE IS A SCHEMA CHANGE** — two new columns on `processed_images`
(`signature_json`, `signature_applied`). They are added by `migrate_schema()`
at startup, so there is no separate step, but **back up `poster.db` before
deploying**.

**No node copy.** `worker_service/` is untouched and `AGENT_VERSION` stays at
1.31.0.

## The big one: where the print file is built

The machine no longer makes the 4000-pixel print file when it paints. For a
project with a review gate it saves only the see-through original and the
small preview, and `storage_path` is left EMPTY until the file exists.

The print file is built once, on approval, by `_build_print_file` (which was
`_reflatten`): flatten onto the colour, enlarge, paint the signature at print
resolution, encode ONE JPEG. That is less total work than before — the file
used to be built at painting time and then rebuilt on approval whenever the
colour changed — and it keeps the single-encode quality rule.

**Deferring only happens when both halves are true**: there is a master to
rebuild from, and there is a review gate to rebuild at. An opaque generation
or an ungated project still builds at painting time, because otherwise
nothing would ever build the file at all.

If the build fails, the approval now FAILS and rolls back. It used to be a
cosmetic recolour that could be swallowed; it is now the thing that makes the
file, and swallowing it would hand the marketplace a path with nothing behind
it.

## The signature

- Upload box on Pipeline → Settings. Refuses a picture with no transparency,
  because one would paint a solid rectangle over every poster's corner.
- Defaults from his own Photoshop placement, stored as percentages: 16.8% of
  the width, 0.5% margin, 35% opacity, right-hand side.
- On Approve Artwork: drag along X, size, opacity, a white/black switch (key
  `B`), and RESET. The preview is the real arithmetic — the same percentages,
  opacity and colour the server uses — so what is dragged is what is painted.
- Per-poster adjustments live in `processed_images.signature_json`.
  `signature_applied` records what was actually painted, so approving an
  unchanged poster twice does not rebuild megabytes for nothing.

**Verified by measurement, not by eye**: building the same print file with and
without the overlay and diffing them gives a mark 672px wide, 20px from the
right, 20px from the bottom, on a 4000 × 6000 poster. Those are his numbers
exactly. Clamping, the black version and the already-big-enough path were
tested too.

## Everything else

- **Recall tool** on the Greenlight door: sends titles back to the start for
  testing. Typed confirmation, a count before you commit, and the duplicate
  listing warning on the panel.
- **The released count on RELEASE WITHOUT FINISHING THE SKIM was wrong**
  whenever KEEP had been used — it subtracted every decision including the
  approvals. Counted from what the decisions say now.
- **Saving is sent in chunks of five** with a real "saving 12 of 40", and it
  says where it stopped if a chunk fails.
- **Deleting the unchosen generations is one call for the whole batch**
  instead of one Storage Box connection per poster, and the saved message now
  says how many files went.
- New Diagnostics invariant `approved_without_print_file`, and the
  shared-file check now ignores rows whose file has not been built yet.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
