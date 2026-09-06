# Not yet deployed

## v150 — the sidebar fix, and the skin sharpened

**v149 shipped with the sidebar broken, and the cause is worth writing
down.** The top bar had `backdrop-filter: blur(...)` on it. A filter on an
element makes it the CONTAINING BLOCK for any fixed-position child — and
the sidebar lives inside the top bar — so the whole rail was squeezed into
the bar's 48 pixels and rendered as a tiny scrollbox. The rule is now a
comment on `.topbar` itself: no filter on that bar, ever. The pulse strip's
blur went too, for the same family of reason.

**And the owner's verdict on the first look — "still feels generic, less
rounded" — is applied:**

* Pills are gone. Buttons, tabs, inputs and nav rows are sharp 4px
  rectangles with hairline borders; roundness survives only on the small
  count badges, where a circle is the correct shape.
* Every panel title now carries a short iris tick — the one recurring
  signature mark.
* The active sidebar row is a flat tint with a 2px iris edge, not a
  gradient pill. Journey arrows faded to near-nothing.

No schema change, no node copy.

**Verified**: preflight green; the preview artifact was rebuilt from the
fixed stylesheet. **NOT verified**: the rail has still never rendered on a
real page — that is the first thing to look at after deploying.

---

Nothing. Everything written is on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
