# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v137 — a maintenance page no longer reads as a redesign** ·
targets 178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — nothing under
it changed, `AGENT_VERSION` stays 1.27.0. **Schema change:** none.

## The gap the owner named himself

`CLAUDE.md` planned item 8 records this as his own example of the class of
bug the interaction audit exists to catch, and says it is NOT yet handled:

> what happens to the earnings read when FineArtAmerica serves a MAINTENANCE
> page? Today the parser would fail to find the figures and report "FAA has
> changed its page" — sending the next session hunting a redesign that never
> happened.

`MEASURED 2026-08-27` — still true, on TeePublic. `parse_account_page` raised
"TeePublic has changed it" whenever fewer than three of the four labels were
found. That is ONE cause asserted as fact, out of several that look
identical: a redesign, a maintenance page, an error page, a half-loaded
page, the interstitial.

## Fixed with a mechanism that already existed

`site_markers()` returns the header logo, which is on every ordinary
TeePublic page and on nothing that stands in front of one. It was already
being used by the store scan; the account parser was not asking.

Three answers now, three messages:

  * the sign-in field is present  -> "the session was not accepted"
  * their header is ABSENT        -> "something is standing in front of it:
    the interstitial, a maintenance page, or an error page. Nothing has been
    redesigned. Try again shortly."
  * their header is PRESENT and the labels are not -> "they HAVE changed the
    page and the four labels this reads need updating."

Same shape as FineArtAmerica's 410 versus 404, and as the DNS-versus-internal
split added in v130.

**Tested** against the shipped parser: a real page parses; each of the three
failures produces its own message; and a marketplace with no markers
configured still gets an answer rather than being refused.

## What batch 5 did NOT find

Two lines of enquiry produced nothing and are recorded so nobody repeats
them:

  * **Exception handlers that swallow silently.** 172 matched, which is a
    flood rather than a finding, and the detector was itself wrong — it did
    not know `emit()` is how the node reports, so handlers that DO speak
    were counted as silent. Not a usable check in that form.
  * **`intake_open` coverage.** All three stages its docstring claims are
    verified to call it. `claim_job` deliberately does not, and that is
    correct: earnings reads and store jobs are the very work the gate exists
    to make room for, so gating them would deadlock.

### Files

`app/earnings/teepublic.py`, `app/earnings/service.py`,
`app/config.py` (136 -> 137).
