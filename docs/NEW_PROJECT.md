<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## Adding a new project — what is expected of you

The owner will describe a workflow, roughly like:

> worker >> gettyimages.com >> save >> admin greenlight >> photoshop >> upload

That is the whole specification of what the project DOES. Your job is to
work out what it therefore does NOT do.

**Inherit nothing by default.** Start from the stated flow and add only what
it needs. Then go through the other projects' mechanisms one at a time and
say out loud which are DEAD for this one — before building. Examples of the
reasoning expected:

- Its images come from an external site, so it needs the paste-a-URL box and
  an "Open <source>" link — and NOT the in-page search grid, the Brave keys,
  or the deep-search button.
- It uses Photoshop, so it needs the JSX editor, the sharpen settings and a
  Windows node — and NOT the OpenAI key, the prompt box, the style reference
  or the spend cap.
- It has an admin greenlight but no mention of judging output, so no review
  gate — Photoshop is deterministic and there is nothing to approve.

The converse trap is just as real: MUSIK searches in-page and saves by
tapping, so a "paste replacement URL" field on a saved image is obsolete
there — the worker would delete and re-pick, never paste. It survived
because it was inherited rather than justified. Apply that judgement.

**Ask about the gaps.** The owner normally states the specifics, but where
his description implies a step without pinning it down, ask — openly, not
with a leading guess. If he describes a worker "getting images" without
saying from where, ask how the worker finds them: another tab at a named
site, a search grid inside the page, an upload from their own machine? The
answer changes the source field, the search mode, half the worker UI and
which settings are dead. Better one question now than a project that
inherits the wrong half of another one.

---

