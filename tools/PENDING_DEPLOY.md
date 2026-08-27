# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v129 — the notes revamp** · targets 178.105.232.196 (test box)

Documentation and one preflight check. **No application behaviour changes**,
so nothing to click after deploying — but it needs to go up so the repo and
the server do not diverge.

### What prompted it

`AUDIT.md` — a complete control-by-control walk of every screen, per role,
per project, written 2026-08-17 — was referenced by NOTHING. A session on
2026-08-27 redid most of that work from scratch and missed findings the file
already had. It was only discovered by listing the directory for an
unrelated reason.

### 1. Provenance tags

Every claim now says where it came from, because four contradictions found
the same day were all one shape: a guess written in the voice of a
measurement.

    MEASURED <date>   somebody ran it; the numbers are real
    DECIDED  <date>   a choice, not a fact; can be revisited
    LEAD              untested hypothesis — never act on it as a diagnosis
    RULE              timeless; applies until the code changes

Defined at the top of `CLAUDE.md`, applied to the planned items and the
marketplace-behaviour sections, and used throughout the rewritten
`AUDIT.md`. Untagged text predates the convention; tag as you touch.

### 2. A document index in CLAUDE.md

All 15 documents, what each answers, and when to read it — split into "every
session", "before writing code", and "when the work touches this".

### 3. `check_no_orphan_documents` in preflight

Every `.md` must be named in that index. Sabotage-tested twice: red when a
new unlinked document appears, red when an existing one is unlinked, green
otherwise.

### 4. AUDIT.md verified and made living

Every DEAD/WRONG row re-checked against today's code. **All three root
causes from 17 August are fixed.** Two entries stay open: the Upload title
template help text still offers `{year}` and `{content_type}` to MUSIK where
both render empty (still true, minor), and the admin "Paste URL to add one"
control could not be found by that name — marked `LEAD`, not assumed either
way.

It also records the correction that the flag-card bug reported earlier that
day was overstated: the list is project-scoped, so the two values involved
can never disagree.

### Files

`CLAUDE.md`, `AUDIT.md`, `tools/preflight.py`, `app/config.py` (128 → 129).
