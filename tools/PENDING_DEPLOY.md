# Not yet deployed

**v228 — the stuck red FLAG on titles with nothing in them.**
- The worker's list now works out the red FLAG fresh each time: it shows
  only when an open flag sits on a picture that still EXISTS. Atlanta and
  Yellowstone (0 saved) lose the tag immediately — no clean-up script. An
  admin note still shows as its own ADMIN NOTE pill and banner.
- Cause: two doors (worker delete, admin DELETE THIS RECORD) counted flags
  on DELETED pictures when setting the marker, so one old flag on a gone
  picture kept it lit for ever. All six doors that compute the marker now
  ask one shared question, utils.live_flag_title_ids — the same one
  Diagnostics uses. The new preflight check found the sixth (REJECT on a
  completion) by itself.
- New preflight check "the flag marker has one definition", sabotage-
  tested red then green.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
