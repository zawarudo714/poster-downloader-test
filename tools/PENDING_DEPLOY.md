# Not yet deployed

**v165 — a colour per part of the sidebar, and a status strip that says
what is actually happening.**

**No schema change. No node copy** — `worker_service/` is untouched and
`AGENT_VERSION` stays at 1.31.0. This release is styling plus two extra
counts on one endpoint, so it is a plain deploy.

What is in it:

- Every section of the sidebar carries its own muted colour: Money gold,
  Marketplace sky, People rose, System mint, Dashboard and Home violet.
  Inside a project the same idea groups the links by job — finding pictures,
  your decisions, the machinery, just looking. The colour shows as a faint
  line down the section, a tinted hover, and the marker on the open page.
- The tint is one custom property per group (`--nav-tint`), so adding a
  section is one line rather than a block of CSS. A tint with no rule behind
  it falls back to grey AND fails preflight.
- The status strip is rebuilt as chips that match the rest of the site.
  It now says: whether the worker machine is up, what it is doing this
  second, what is QUEUED behind it, whether the pipeline is stopped and why,
  how many things are waiting on you, workers online, and when the figures
  were last refreshed.
- `/admin/api/pulse` gained `node.to_process` and `node.to_upload` (counted
  across every project, because the machine is shared) and `needs_you`
  (summed from the same badges the menu shows, so the two cannot disagree).
  The pre-baked `node.busy` sentence is gone — the screen builds the wording
  from the figures now.
- New preflight check `check_colour_names_have_rules`, sabotage-tested in
  both directions: a `data-nav-tint` the stylesheet does not define, and a
  `chip('…')` tone with no `.pulse-…` rule.
- The old `.pulse-item` styling is deleted rather than left disabled.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
