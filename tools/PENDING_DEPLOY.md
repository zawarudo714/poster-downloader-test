# Not yet deployed

## v205 — re-sent titles get a letter so the marketplace can't renumber them

Waiting to deploy. The node does NOT need copying — nothing in
`worker_service/` changed. Version 205 (live is 204).

What changed, in plain words:

- When a title is sent back (recalled) and then uploaded again, the site now
  gives it the next letter by itself. The first time a place goes up it is
  just "Kyoto". After a recall it goes up as "Kyoto B". After a second recall
  it is "Kyoto C", and so on.
- This is so FineArtAmerica cannot silently rename a repeat title to
  "Kyoto #2". A "#2" lives at an address we never wrote down, so the listing
  check would read the first one as missing. By choosing the letter
  ourselves, the name we store always matches the name FAA shows.
- The letter only appears once a place has actually gone live at least once.
  A title that was painted but never uploaded, then recalled, still goes up
  under its clean bare name — because no name was ever spent for it.

How it works underneath (for a future session):

- New column `saved_posters.times_listed` counts how many times this poster
  has gone fully live. `report_uploaded` adds one per full go-live;
  `render_remote_title` reads it and appends the letter (`times_listed` 1 →
  " B", 2 → " C"). The recall handler deliberately does NOT reset it.
- The counter is per-poster and assumes ONE upload account per project (true
  for travel). A second upload account would spend the name once per account
  and needs this to be per (poster, account). Noted in the column comment.

Neighbours touched:

- The recall panel's warning was REWRITTEN. It used to say "do not recall a
  live title, FAA will renumber it." That is exactly the case this fixes, so
  it now says a live title is safe to send back and explains the automatic
  letter — plus the one real caveat, that the old listing stays live on FAA
  until you delete it there.

Safety net added:

- New Diagnostics invariant `live_titles_unique_per_account`: no two live
  listings on one account may share a name. If the letter ever failed to
  advance, two sends would land on the same name and this goes red — the
  drift caught by a check instead of by the owner.

Verified: `python tools/preflight.py` and the JavaScript parse (see the
delivery note for exactly what was and was not run here).

Owner-only test, because only he can drive a real upload: recall a title that
is already live, let it repaint and upload, and confirm it goes up as
"<name> B" at the matching address. Item added to TO_TEST.md.
