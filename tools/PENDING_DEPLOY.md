# Not yet deployed

## v265 — painting follows your K mark (2026-10-26)

Server only. **The worker machine does NOT need copying** — nothing in
`worker_service/` changed.

- A picture goes to painting once it is PAID, you have pressed K on it at
  least 5 minutes ago, it has no open flag and its title is finished. A
  payment no longer starts painting by itself. Your own pastes go at once.
- Un-pressing K or flagging takes a picture back out of the painting queue.
  A painted but not yet approved picture can still be swapped by the
  worker; its painting leaves Approve Artwork.
- Money: a picture whose 7 KES was taken back no longer blocks paying a
  later redo of the same title.
- PAY AHEAD is ticked by default. New Greenlight setting: minutes after K.
- Fixed on the way: the Greenlight tab's MANUAL never applied to payments.
- Deleted `scripts/convert_run_to_pay_ahead.py` (one-off, already applied).
- To test: TO_TEST item 112. Behaviour test: tools/test_painting_follows_k.py.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
