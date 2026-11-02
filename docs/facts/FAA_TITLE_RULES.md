<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## FineArtAmerica title rules — `MEASURED 2026-08-13`

FAA silently rewrites artwork titles. Verified by submitting all 165 distinct
non-ASCII characters from the celebrity database and reading back what saved:

- **Folded to ASCII**: Latin-1 letters plus `š Š ž Ž` — `é→e  ö→o  ñ→n  ç→c
  ø→o  å→a  æ→a  þ→b  ð→o  ß→Ss`. Case is usually preserved but NOT always:
  `Ë→e  È→e  Ì→i  Õ→o` come back lowercase. Encode the observed table; do not
  infer it.
- **Deleted outright**: everything else — Eastern European diacritics
  (`ł ć ş č ğ ń ż ę ř ă ą ě ś ň ő ź ď`), Turkish `ı İ`, macrons, ALL
  punctuation (apostrophes, quotes, hyphens, `… • · × ™ © £ « »`), symbols,
  arrows, superscripts.
- **Max title length is 100 characters**, truncated silently.
- **The first character is upper-cased.**
- A title that normalises to EMPTY is rejected with a page reading
  "Please use only A-Z in your artwork title" — an HTML error page, not an
  HTTP error, so it must be detected by content.
- **A TITLE THE ACCOUNT ALREADY HOLDS IS SILENTLY RENUMBERED.** Stated by
  the owner, `MEASURED 2026-09-03`: send "Los Angeles" twice and the second
  one lists as **"Los Angeles #2"**. Not refused — renamed, with no error.

- **DELETING A LISTING DOES NOT GIVE THE TITLE BACK.** Stated by the owner,
  `MEASURED 2026-09-09`. Deleting turns the listing into `Example #A #1` —
  FAA appends a number rather than releasing the name — and uploading the
  same title again then lands as `#2`. **A title is spent for the life of
  the account the first time it is used.**

  **WE MARK A RE-SENT TITLE OURSELVES SO FAA NEVER RENUMBERS IT.** `BUILT
  v205, 2026-09-14`, at the owner's request. When a recalled title is
  uploaded again, `render_remote_title` appends the next letter — `Kyoto`,
  then `Kyoto B`, then `Kyoto C` — so FAA sees a new name and does not add a
  `#2` we cannot address. A plain letter, not `#N`, on purpose: this way we
  choose the name and never depend on FAA's own numbering, which we cannot
  read. The count lives in `SavedPoster.times_listed`, added by
  `report_uploaded` once per full go-live and read by the renderer;
  `letter_for_index(times_listed)` gives the letter, shown only when
  `times_listed >= 1`. **Recall deliberately does NOT reset it** — a recall
  is the exact moment the memory must survive. It is per-poster and assumes
  ONE upload account per project (true for travel); a second account spends
  the name once per account and would need this per `(poster, account)`.
  Watched by `check_live_titles_are_unique_per_account` in `diagnostics.py`:
  if the letter ever fails to advance, two live listings would share a name
  and it goes red.

  **RE-UPLOAD (v289) is the ONE door that resets it to 0, on purpose.**
  `MEASURED 2026-11-01` by the owner: FAA's renumbering follows the
  ADDRESS, and the address carries the artist slug. "Taj Mahal, Agra" was
  deleted under "Illusination Travel Posters", the shop was renamed, and
  the same title went back up plain, with no "#2". So after a shop RENAME a
  deleted listing's title is free again at its new address, and
  `pipeline.relist_listing` sets `times_listed = 0` so it goes up without a
  "B". That is only safe AFTER the rename, which is why RE-UPLOAD takes
  only rows the Listing check read as 410 under the OLD name (and why the
  screen says to rename before pressing it). Without a rename, a reset
  would send "Kyoto" again and FAA would list "Kyoto #2". The row is the
  SAME row (one per picture per account), so nothing can list a title
  twice; `check_one_listing_per_title_slot` and
  `check_relisted_rows_are_sound` watch it.

  **A NEW PICTURE ON THE SAME TITLE INHERITS THE COUNT.** `times_listed`
  lives on the PICTURE, but the name it counts belongs to the TITLE on the
  account. So USE MY OWN PICTURE (v290) copies the highest `times_listed`
  of the pictures it replaces onto the new one, and moves their upload
  rows with it — a live listing's row too, unchanged. Without that, a
  replaced Kyoto would go up again as plain "Kyoto" and FAA would list
  "Kyoto #2". `tools/test_relist.py` breaks the copy on purpose and goes red.

  This matters because the owner is REUSING an account that carried the
  movie catalogue, rebranded for travel (see `OPEN_ISSUES.md`). Every film
  title ever listed on it is permanently taken, deletions included.

  **A COLLISION BETWEEN THE OLD FILMS AND THE NEW PLACES IS NOT POSSIBLE,
  and I claimed it was.** `TRACED 2026-09-09`, after the owner pushed back.
  The film titles were built from `"{title} {letter}"` with a year, so they
  list as `Dunkirk - 2017 A`. Travel's template is `"{title}"` and
  `images_per_title` is 1, so a travel listing is the bare place name —
  `Dunkirk`. Those are different strings, so FAA cannot renumber one because
  of the other. Deletion pushes them further apart still, because the old one
  becomes `Dunkirk - 2017 A #1`.

  **The error is worth keeping, because it is the exact shape rule 3b warns
  about.** I noticed that films are often named after cities, invented a
  collision from it, and reported it as a risk without ever reading the title
  template — which was one grep away in the file I had open. A pattern that
  LOOKS like a defect is a reason to look, never a reason to report.

  **The owner then closed the question completely: the old films AND series
  both followed that naming rule, hand-made ones included.** So there is no
  residual case at all, and no mechanism is needed. `matching.py` still warns
  that the oldest listings "follow no rule we control" — that is about the
  MATCHING of sales to designs, which is a different question from whether
  two titles can collide.

  **This makes a title half of a primary key, not a label.** Everything
  downstream derives the listing address from `{title-slug}-{artist-slug}`,
  so a renumbered listing lives at an address we never computed, and
  `listing_check.py` reads the miss as a 404. A 404 is defined two sections
  up as "OUR ADDRESS IS WRONG" rather than a takedown, which is the correct
  reading — but only after the sweep has already spent the request, every
  sweep, for as long as the pair exists.

  So a duplicate title is not a cosmetic problem and it cannot be left for
  the marketplace to sort out. **Titles must be unique ACROSS THE WHOLE
  CATALOGUE, after FAA's folding, before anything is dispatched** — "Los
  Angeles" and "Los Ángeles" are the same title to FAA and must not both
  exist. Uniqueness is checked against the folded form, never the raw
  string. **Enforced since the third audit (v175):**
  `check_titles_collide_after_folding` in Diagnostics watches the whole
  catalogue unattended, and the retitle endpoint refuses a name the account
  already lists — those two doors (the imported sheet, a hand-typed
  replacement) are the only ways a duplicate can arrive. Before that, this
  paragraph was an intention wearing the voice of a mechanism.

Consequences that are not optional:

- Normalisation belongs in `render_remote_title`, NOT in the uploader, so the
  stored `remote_title` equals what the listing actually shows. Otherwise the
  planned reconciliation scanner reports thousands of false mismatches.
- Titles must be validated BEFORE dispatch. Empty, under ~2 characters, or
  more than half the length lost => flag for the admin to edit and resend,
  never send to the node.
- This applies to the movie project too. `Amélie`, `Léon`, `WALL·E` have been
  silently mangled all along.

