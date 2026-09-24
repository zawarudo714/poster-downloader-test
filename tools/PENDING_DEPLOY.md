# Not yet deployed

**v230 — your newest send-back note shows first on every card.**
- Changes Requested cards now list a flag's full history, newest first:
  "Your latest: …" highlighted, then "Earlier: …", then "First flag: …"
  in grey. Before, a REJECT on a completion was never shown on the card at
  all (only the first flag), and a REJECT on a single picture sat buried
  at the end of one run-on line.
- One reader (the flag_history filter in templating.py) serves all four
  card kinds. How the two REJECT buttons SAVE is deliberately unchanged,
  because the worker's screen reads both fields and would otherwise show
  the note twice.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
