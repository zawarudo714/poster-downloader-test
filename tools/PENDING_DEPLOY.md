# Not yet deployed

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

## v181 — the wrong Newcastle, and the Google button's {kind} · 2026-09-10

- **A result naming a DIFFERENT region is ranked off the grid.** "Newcastle
  Beach Australia" on a search for "Newcastle, South Africa" now folds away
  behind the existing SHOW THEM button, and the line under the grid says
  how many were hidden for naming somewhere else. The region list comes
  from the sheet's own after-comma parts (271 regions at a 20-title cut,
  measured on the real rows), so it maintains itself on every re-import.
  Nothing is deleted, and a caption naming OUR region can never be hidden.
  Behaviour-tested in `tools/test_brave_ranking.py` with the owner's real
  captions; the test goes red if the conflict check is ever deleted.
- **The Google button now carries {kind}.** The phrasing setting accepted
  {kind} but the OPEN title's button was the one call site not passing it,
  so it searched the title alone. The argument is required now — a
  forgotten call site fails preflight (`check_call_arity`) instead of
  quietly searching the wrong words. The same button also now uses the
  search-words column like every other path, instead of the raw title.

Deploy: the server only. The NODE does not need copying — nothing in
`worker_service/` changed.
