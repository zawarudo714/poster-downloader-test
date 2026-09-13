# Not yet deployed

**v195 — every saved image records which search found it, and the screens
say so.** (First deploy aimed at PRODUCTION, 178.105.34.144 — the log
above still shows the retired test box.)

Each save now stores one word: 'brave' (the in-page grid), 'google' (the
phone add-on) or 'pasted' (a hand-pasted link, worker's box or admin-add
alike). Site-only — the phone add-on is untouched: the site's own grid
announces itself, and silence at that door can only be the add-on. Shown
in the Activity Log line ("found on Google"), as a pill on the Review
Posters gallery cards and its lightbox, and on Approve Artwork beside
"what the worker found", card and zoom both. Old saves have no record and
show nothing. New database column (image_source, added automatically at
startup), and a new preflight check — sabotage-tested — that refuses any
future save door that forgets the stamp.

Files: models.py, schema_migrations.py, routes/worker.py, routes/admin.py,
routes/pipeline_admin.py, user.js, admin.js, admin_audit.js,
admin_review_images.js, admin_review_images.html, style.css, preflight.py,
config.py.

The node was NOT changed — no worker_service copy needed. The phone
add-on zip was NOT changed — workers keep what they have.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
