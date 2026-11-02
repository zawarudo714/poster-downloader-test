<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## TeePublic — REMOVED FROM THE SITE `2026-09-06`

**The owner's decision, in his words: running it on the site caused too many
problems, so he will run the same mechanism from a Python GUI tool on his
laptop, from time to time.** Everything TeePublic was deleted from the
server and the node in v147: the tab, the store-health scan and switching,
the interstitial-wall machinery and its recorded mouse paths, and the
earnings reader.

What a future session needs to know:

  * **Every measured TeePublic fact and the complete working code are saved
    in `../teepublic_tool/` beside this repo** — a notes file plus the
    shipped source. Build the laptop tool from there; re-derive nothing.
  * **The database TABLES survive on existing installs** (`store_listings`
    and friends). They hold the only record of which designs were switched
    off and never switched back on. Do not drop them; the laptop tool will
    import them.
  * **TeePublic account rows survive too.** An account whose marketplace has
    no entry in `service.READERS` is skipped by design, not an error.
  * The general lessons TeePublic taught — real clicks vs JavaScript clicks,
    a wall detected by what is missing, the speed tell, blocked-vs-broken —
    are woven through HOW TO WORK HERE and stay there, because they were
    never really about TeePublic.

