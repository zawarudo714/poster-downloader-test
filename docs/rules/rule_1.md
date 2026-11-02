<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 1. Audit before you build

Anything that touches a screen, or a mechanism more than one thing uses,
starts with an inventory — not with code:

> screen → control → what it does → **does it do anything in THIS project?**

For plumbing: what reads this, what writes it, what breaks if it is wrong.

Why this and not a search: searching finds WORDS. Only enumerating forces
the question "should this exist here at all". A real failure caused by
skipping it: MUSIK's flag card had an "Open source" button that had been
correctly RENAMED and still linked to TMDB — a project that never touches
TMDB. Renaming made it worse, because now it looked right.

**Walk it as the user, including states you only reach by acting.** Claim a
title, save an image, tap the saved image, flag it, then look at the worker's
view. Most dead mechanisms are not on the page at load; they appear after a
click, which is exactly where a text search cannot go.

