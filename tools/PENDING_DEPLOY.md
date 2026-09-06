# Not yet deployed

## v151 — comfort and polish

The owner's brief after seeing v150 live: graphite instead of black,
nothing hard to read, more air, livelier stats, small visual delights.

* **Palette lifted** in `config.py`: background `#16171d`, cards `#1d1f28`,
  borders and secondary text both brightened. Raised surfaces (inputs,
  toasts) sit clearly above their cards now.
* **Contrast guarantee**: inputs, selects, textareas, options and labels
  all explicitly take the text colour; placeholders take the subtext. The
  dim "300" box on the Listing check settings was the specimen.
* **Breathing room**: page padding 28px, panels 24px apart, taller table
  rows, roomier panel bodies.
* **Stats revamped**: tiles and pills are raised gradient cards with large
  gradient-ink numbers (violet for counts, mint for good, coral for bad),
  hover lift, and hairline-separated record rows.
* **New-message toast**: when the chat unread count RISES while you are on
  any other screen, a small violet toast appears under the top bar — "New
  chat message — open" — and the chat badges do a little hop. Never fires
  on the chat page itself. Works for admin and workers.
* **Small delights**: thin violet scrollbars, buttons press down 1px on
  click, journey cards lift on hover, the shared toast restyled to match.

No schema change, no node copy.

**Verified**: preflight green (including the new top-bar-filter check);
`nav_badges.js` passes `node --check`; the preview artifact was rebuilt on
the graphite palette. **NOT verified**: the chat toast has never fired for
real — send a message from the worker account while sitting on the
dashboard to see it.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
