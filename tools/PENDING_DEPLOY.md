# Not yet deployed

- **v293 · the daily upload limit counts the last 24 hours.** It counted a
  calendar day, and from 00:00 to 03:00 Nairobi it counted nothing, so 101
  extra went up on 5 Nov and FineArtAmerica logged the account out. The
  node now stops a run when it is logged out, or after 3 failures in a
  row. **The node needs copying:** `worker_service/` to win-vps-1, agent
  1.35.0. Tested by `tools/test_upload_limit_24h.py`.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
