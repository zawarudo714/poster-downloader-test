# Not yet deployed

## v149 — UI Revamp Part 2: the visual skin

The look, from the owner's reference screenshot (2026-09-06): deep ink
background, iris-violet accent, rounded surfaces, pill buttons — and the
reference's signature move, a **left sidebar** on desktop.

* **Palette**: one place, `config.py` PALETTE — ink `#0c0d13`, panel
  `#141620`, iris `#a89bfa`/`#7263e8`, mint/amber/coral for good/warn/bad.
  Light mode re-derived to cool paper with the same iris.
* **Typography**: Georgia and Courier retired. One clean sans carries the
  whole interface; monospace survives only on figures (`.mono`), where
  digits lining up in columns is the point.
* **The left rail**: at desktop widths the nav becomes a fixed 228px
  sidebar with headed groups — the exact treatment the phone drawer already
  used, promoted. The phone drawer itself is untouched. Signed-out pages
  (login) get no rail.
* **The glow is spent in one place**: the active nav item, primary buttons,
  and the Home page's journey numbers. Everything else stays flat.
* Every hardcoded gold tint in the stylesheet was converted to the iris at
  the same opacity, so nothing still wears the old accent.
* `prefers-reduced-motion` is honoured; focus outlines are visible.

### Schema / node

None. No folder copy.

### Verified

* preflight green on every check; `config.py` compiles; the CSS layer sits
  LAST in the file so it wins by cascade order without rewriting old rules.
* A static preview built from the REAL stylesheet was rendered as an
  artifact for the owner to judge before deploying.
* **NOT verified**: no real page has rendered with the new skin. The first
  look after deploying IS the test — Part 2 is exactly the stage where a
  wrong-looking screen is cheap to report and fix. Check the phone view and
  the light-mode toggle as well as the desktop rail.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
