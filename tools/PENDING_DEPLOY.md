# Not yet deployed

## v239 — USE MY OWN PICTURE, and unreviewed days first (2026-09-27)

- New: USE MY OWN PICTURE on Worker Images (title box and zoom), Changes
  Requested (every card and zoom), Skipped (every row) and the two retired
  lists in Needs Attention. One dialog (`static/js/admin_pick.js`, loaded
  for admins in `base.html`), one endpoint (`/admin/title/{id}/admin_pick`).
  Replaces the + ADD box and its `/admin/poster/add` endpoint, both removed.
- Retire, admin DELETE and the new door now share one withdrawal
  (`_withdraw_pictures`): paintings of withdrawn pictures leave Approve
  Artwork, queued uploads stand down. Startup sets aside paintings that
  earlier retires left waiting (`utils.set_aside_withdrawn_paintings`).
- Worker doors refuse to reopen, skip or change a title holding the
  admin's own pick (`_refuse_if_admin_chose`).
- Worker Images opens on a day that still has unreviewed pictures, with a
  NEXT DAY TO REVIEW button.
- New column `saved_posters.added_note` (added automatically at startup).
- Three new Diagnostics checks; new GUARDED rows in preflight.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
