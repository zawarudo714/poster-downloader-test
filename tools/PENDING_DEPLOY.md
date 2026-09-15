# Not yet deployed

## v208 — the Google Vision key now saves and shows "saved"

Waiting to deploy. The node does NOT need copying. Version 208 (live is 207).

The bug, in plain words: the Google Vision key box always read "not set" even
after saving, because the key was never added to the list of "secret" keys
in routes/pipeline_admin.py. Four things hang off that one list, and all four
were wrong for this key:

- the "saved / not set" badge could only ever say "not set";
- the value was sent back to the browser instead of being kept on the server;
- saving that panel again with the box blank would have WIPED the key;
- it was stored unencrypted, unlike the other keys.

The fix is to add google_vision_api_key to SECRET_KEYS, and to read it back
through get_secret (which decrypts, and tolerates a plaintext value — so the
key already saved still works and one saved from now on is encrypted).

What the owner will see after deploy: the box flips to "saved" on its own,
because a value was already stored. No re-paste is strictly needed for it to
work, but re-pasting once and saving is worth doing so the key is stored
encrypted rather than as the plaintext the old path left behind.

The mechanical net so this class cannot recur: check_password_fields_are_
secret in preflight.py compares the password boxes in admin_pipeline.js
against SECRET_KEYS in pipeline_admin.py and fails the deploy on any key that
is one but not the other. Same two-lists-must-agree shape as the colour-name
check. Sabotage-tested: removing the key from SECRET_KEYS turns it red on
exactly that key, and building the check surfaced a bug IN the check itself
(SECRET_KEYS uses double quotes, the JS rows single) that was fixed before it
shipped.

Verified: py_compile passes on every changed file, and the new check was run
in isolation — green on the real code, red on the sabotage. The full
preflight suite runs longer than this sandbox allows, so it was not run end
to end here; the deploy tool runs it in full before it will ship, which is
the real gate.
