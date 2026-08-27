# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v131 — fixes the 500 that v130 introduced** · targets
178.105.232.196 (test box)

## DEPLOY THIS. v130 is broken on the server right now.

Saving a poster by pasting a URL returns **500** in the movie project. MUSIK
is unaffected because it saves through a different endpoint.

### What broke

```
UnboundLocalError: cannot access local variable 'resolve_project'
  File "/app/app/routes/worker.py", line 1347, in save_image
```

`save_image()` resolved the project THREE times, each behind its own
`from ..pipeline import resolve_project` further down the function. A local
import binds that name for the WHOLE function, so the module-level import on
line 49 becomes invisible — and v130 added a fourth use ABOVE them.

Fixed by resolving once at the top and reusing it, which also removes the
triplication that made the trap possible.

### Why nothing caught it

`py_compile` is happy. `check_no_undefined_names` is happy, because the name
IS bound — just not yet at that point. Preflight was fully green. It fails
only when that line actually runs, and the owner found it by saving a
poster.

**New check: `check_local_imports_not_used_earlier`** — a name imported
inside a function but used earlier in that same function. Sabotage-tested
against a copy carrying the exact v130 bug: it reports
`save_image() uses 'resolve_project' on line 1353, imports it on 1429`, and
is silent on the fixed file.

Reported as a warning rather than a failure — it is legal in some shapes —
but it is worth reading every time.

### Files

`app/routes/worker.py`, `tools/preflight.py`, `app/config.py` (130 → 131).

**Verified:** preflight green; the new check sabotage-tested both ways.
**Not verified:** still nothing has actually saved an image through this
path. **Same test as before after deploying** — paste a TMDB URL and press
SAVE in the movie project.
