# Not yet deployed

**v232 — no more red outline on titles that have no flag.**
- Worker Images now works out the red outline from the live flags each
  time, the same way the worker's own list already did. Before this, it
  read a saved mark that older versions had sometimes left switched on
  (Beirut was the example).
- On startup the server corrects every saved mark once, so the Title List,
  the dashboard count and Diagnostics agree too. The log line reads
  "Corrected the flag marker on N title(s)".
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
