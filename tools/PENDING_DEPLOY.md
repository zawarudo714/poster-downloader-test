# Not yet deployed

**v224 — the 2026-09-20 audit's findings (waiting to deploy).**
Found by walking the retire flow's seams, none reported by a symptom yet:
- Retiring a title now also stands down any QUEUED upload rows for its
  pictures (status 'skipped', the SKIP UPLOAD word) — the claim already
  refused deleted pictures, but the row would have sat counted in the
  strip's "waiting: N to upload" for ever.
- The admin's own ADD-image box now refuses a retired title, the one door
  left that could hang a live picture under 'unusable' (worker doors were
  sealed in v223).
- New Diagnostics invariant `retired titles hold nothing`: a live picture
  under a retired title, or a pay-despite-delete mark on a picture still
  alive, goes red unattended.
- New preflight check `status menu matches its filter` (sabotage-tested
  red and green): the Title List's status dropdown and _master_query's
  whitelist can never disagree again — the class behind the "Unusable
  filter showed everything" miss.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
