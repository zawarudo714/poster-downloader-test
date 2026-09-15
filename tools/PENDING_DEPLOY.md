# Not yet deployed

## v210 — images in chat

Waiting to deploy. The node does NOT need copying. Version 210 (live is 209).

You and workers can now send images in chat, both directions. Two ways to
attach, chosen with the image button by the message box:

- CHOOSE FILE — upload a picture from your computer or phone (a screenshot,
  an example shot). JPG, PNG, GIF or WebP, up to 12 MB.
- or paste an image link — an address to a picture already online.

Either can go with text or on its own. Images show inline in the thread and
click to open full in a new tab.

How it works underneath, for a future session:

- Two new columns on chat_messages: image_path (an UPLOADED file, stored
  under WORKSPACE_DIR/_chat with a UUID name) and image_url (a pasted link
  the browser loads directly). A message with neither is text-only, exactly
  as before; body may now be "" when an image is the whole message.
- Uploads are served through auth'd routes: /admin/api/chat/image/{id} for
  the admin, /api/chat/image/{id} for a worker — and the worker route only
  serves an image from that worker's OWN thread, so a guessed id cannot pull
  another worker's picture. Pasted links are loaded straight from their URL.
- save_chat_image() in chat.py validates type and size and writes the file;
  send_message() now needs text OR an image, not text alone.

Neighbour fixed in the same change (rule 2): the Diagnostics orphan-files
sweep is taught that ChatMessage.image_path claims a file. Without that, every
uploaded chat image would have been listed as an unknown file the owner is
invited to delete — the exact one-of-N reference trap that check already
carries a long comment about.

Verified: the JavaScript parses, both templates balance, the page hooks
exist, nothing is stuck behind a hidden ancestor, no undefined names, calls
pass the right arguments, and the workspace-underscore rule still holds
(_chat follows it). Run in isolation because the full preflight suite runs
longer than the sandbox allows; the deploy tool runs it in full before
shipping. Not testable here: a real file upload round trip — that is the
owner's first click (TO_TEST 62).
