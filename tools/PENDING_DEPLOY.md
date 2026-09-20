# Not yet deployed

**v222 — the chat badge's REAL fix, and the strip chip (waiting).**
- Root cause found at last, third try: a chat tab left open in the
  BACKGROUND kept polling and marked every arriving message READ, so the
  unread count was honestly zero and no badge anywhere had anything to
  show. chat.js now marks read only while the tab is actually VISIBLE; a
  hidden tab notes that reading is owed and pays it when fronted. This
  also makes the "Seen" line truthful: seen now means on a screen a human
  was looking at.
- The pulse strip gains a red beating CHAT chip ("1 new message" →
  /admin/chat), fed by the same api_pulse reply every admin screen already
  polls (its one-endpoint-one-timer rule). Drawn only when something is
  unread. Owner's ask, 2026-09-20.
- Server only. The Windows node is NOT affected.

**Also in v222 — RETIRE TITLE, pay-and-remove (owner's design, 2026-09-20).**
- New button on the Worker Images zoom: RETIRE TITLE. For places that turn
  out to have no good photograph anywhere. Two typed locks (a reason + the
  word Confirm), both re-checked by the server.
- What it does: withdraws the worker's picture (file removed, row
  soft-deleted, flags closed), parks the title as status 'unusable' with
  the reason stored on it, releases any claim — and the worker is STILL
  PAID: the withdrawn row carries `pay_despite_delete`, the one mark
  `payable_criteria()` honours for a deleted row. Refuses if a picture is
  mid-pipeline or already listed.
- Title List gains an "Unusable (retired)" filter; the reason shows when
  the mouse rests on the status pill. Deliberately NOT in the bulk-status
  menu — a bulk flip would skip both the pay and the deletion.
- Two additive columns ride the startup migration:
  `saved_posters.pay_despite_delete`, `master_titles.unusable_reason`.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
