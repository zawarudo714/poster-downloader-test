# Not yet deployed

**v166 — the sidebar as coloured bands, which is what v165 should have
been.**

**No schema change. No node copy** — `worker_service/` is untouched and
`AGENT_VERSION` stays at 1.31.0. Styling and one template restructure.

What is in it:

- Each job in the sidebar is now a BAND: a soft gradient wash of its own
  colour, a coloured spine down the left edge, a faint border, and a small
  heading naming the job. v165 only tinted the hover and the marker, so the
  colour appeared once you were already pointing at what you wanted — no
  help at all. The owner drew what he meant and it was the whole block
  shaded.
- The project menu is restructured into four bands: FINDING PICTURES,
  YOUR DECISIONS, THE MACHINERY, JUST LOOKING. Home and Dashboard are bands
  of one, so both menus are the same shape.
- The master menu's existing groups become bands too, using the group
  button as the heading. No markup change there beyond the tint attribute
  that was already added in v165.
- Each row carries a small dot in its band's colour, which lights up on the
  page you are on.
- The phone drawer gets the same bands, with its own spacing.
- `check_colour_names_have_rules` gained a second half: every `/admin/`
  link in `base.html` must sit inside a band. Sabotage-tested — pulling one
  link out of its band fails the deploy.

The nav structure was verified by parsing `base.html` and printing the tree,
which confirmed every admin link lands in the right band. The "All Projects"
exit link and the whole worker menu are deliberately outside the bands, and
the check is scoped by href so it does not need a list of exceptions.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
