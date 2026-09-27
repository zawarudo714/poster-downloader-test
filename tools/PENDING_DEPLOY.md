# Not yet deployed

**v236 — Approve Artwork: LEAVE FOR PHOTOSHOP, and the decision keys on the
dashboard.** (Built on top of v235, which has not been deployed either — one
`git pull` brings both.)
- New mark, LEAVE FOR PHOTOSHOP, with a blue outline. It is not released on
  SAVE & RELEASE. The picture stays waiting, with its mark and chosen
  version, until it is edited and kept. This is for days when Photopea is
  down.
- The keys are now dashboard settings: RERUN 7, LEAVE FOR PHOTOSHOP 8, KEEP
  9. A digit also works from the number pad with Num Lock off. The old R and
  K letters are gone. U, C, V, Z, B, E and the signature keys are unchanged.
- Worker Images uses the same KEEP key (9) for "looked at it", in the grid
  and in the zoom.
- Server only. The Windows node is NOT affected.

**v235 — same-picture check on every worker save, plus the admin Google
add-on.**
- A worker saving the EXACT picture file already used for a different title
  is now warned, or refused. The dashboard switch "Same picture on two
  titles" (Settings, search section) picks warn / block / off. It starts on
  warn. It covers the paste box, the search grid, REPLACE, and the phone
  add-on.
- On startup, a background job fingerprints every older picture that had
  none, so the check can see them too. The log says "Fingerprinted N
  picture(s)".
- Fixed: swapping a picture from the search grid used to delete the old file
  BEFORE the new one had downloaded. A failed or refused swap then left a
  record with no file. The old file now goes only after every check passes.
- CHECK GOOGLE hands its search to the new admin Chrome add-on
  (poster_admin_extension) when it is installed, so ONE Google tab follows
  the zoom. Without the add-on, it opens a new tab per press, as before
  v234.
- Server only for the site. Two add-ons changed, both outside the server:
  the worker add-on is now 1.9 (explains the new refusal), and the admin
  add-on is new (1.0). The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
