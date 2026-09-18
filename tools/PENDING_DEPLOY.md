# Not yet deployed

**v220 — the chat badge that never showed, and the "Seen" line (waiting).**
- The sidebar chat badge was dead for the admin: nav_badges.js guessed the
  viewer's role by sniffing the ADMIN pill's CSS classes, and a wrong guess
  asked the worker endpoint as an admin, was refused, and showed nothing —
  in silence. The role now arrives from the server on `<body
  data-user-role>`, and every chat badge is found by one shared
  `data-chat-badge` attribute instead of a list of ids.
- The new-message pop-up now STAYS until clicked (open chat) or dismissed
  (✕), instead of evaporating after 8 seconds; it also clears itself once
  the messages are read.
- Instagram-style "Seen": the admin's chat thread shows one quiet "Seen
  HH:MM" line under the last of their messages the worker has read, fed by
  the worker's existing read-marker on every poll. Server change is in
  `chat_thread` + a `viewer_read_at` helper in chat.py; no schema change.
- Server only. The Windows node is NOT affected.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
