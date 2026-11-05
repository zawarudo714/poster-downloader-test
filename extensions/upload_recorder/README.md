# Upload Recorder

Records what you click, type and upload on ONE website, so the steps can be
studied and turned into an upload automation later (first use: Junique,
2026-11-05). Nothing leaves your computer; you save the file and hand it over.

## Put it on Chrome (once)

1. Open `chrome://extensions` and switch on **Developer mode** (top right).
2. Click **Load unpacked** and pick this folder (`extensions/upload_recorder`).
3. Click the puzzle piece in the toolbar and pin **Upload Recorder**.

## Use it

1. Sign in to the website first. Recording never needs your password.
2. Open the upload page, click the Upload Recorder icon and press
   **Start recording**. The icon shows a red **REC** while it records.
   `Alt+Shift+R` also switches it on and off.
3. Upload a design the way you normally do. Rough clicking is fine.
4. Before the next design, press **Next design**. Use **Add note** for
   anything worth knowing, for example "that click was a mistake".
5. Press **Stop recording** when you finish, then **Save recording file**.
   The file lands in Downloads as `upload_recording_<site>_<date>.json`.

Refreshing the page, changing pages, closing the tab or restarting Chrome
loses nothing: every step is saved the moment it happens. Pressing Start
again on the same site carries on the same recording. Only **Delete the
whole recording** clears it.

## What it records, and what it never does

- Records only on the site where you pressed Start (and its subdomains).
- Clicks, typed text (once you stop typing), choices in lists and boxes,
  the names and sizes of files you pick or drop, Enter/Tab/Escape, messages
  the site shows, and a list of every field and button on each page.
- The requests the page sends to the site: address, status, time taken,
  form field names and short values. A picture being sent is recorded by
  size only.
- **Never** records passwords, tokens, card numbers or cookies: password
  fields and anything named like a secret are saved as `[hidden]`.

Test: `node tools/test_upload_recorder.js`.
