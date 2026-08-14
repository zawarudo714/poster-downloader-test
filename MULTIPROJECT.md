# The multi-project contract

**Read this before writing any code in this repo.** It is short on purpose.

This app runs several print-on-demand niches side by side. Today there are
two — movie/series posters and MUSIK (music artists) — and there will be
more. Almost every bug this project has had in its multi-project life came
from code that quietly assumed there was only one.

---

## The one rule

> **Nothing is allowed to assume a niche.**
> Not a word on screen, not a query, not a stage, not a folder.

If you are about to type `poster`, `TMDB`, `Photoshop`, or `FineArtAmerica`
anywhere a person will read it, you are about to write a bug. Ask the
project instead.

---

## What a project declares

`pipeline.PROJECT_DEFS` is the registry. Projects are declared **in code**
and reconciled into the database by `sync_projects()` on every startup —
there is deliberately no UI to create one, because standing up a niche needs
a source, a script, accounts and worker assignments, all of which are code.

| Field | Answers |
|---|---|
| `slug` | Stable id. **Immutable** — per-project settings are keyed `pipeline.<slug>.<key>`, so renaming orphans them silently. |
| `name` | What humans see. Safe to change. |
| `source_site` | Where images come from: `tmdb`, `brave`, … |
| `target_site` | Where they are listed: `fineartamerica`, `teepublic`, … |
| `processor` | How they are made: `photoshop` (Windows node) or `gpt` (this server). |
| `search_mode` | `external` (open the source in a tab) or `inpage` (search grid). |
| `images_per_title` | How many per title. |
| `item_noun` / `item_noun_plural` | The project's own word. "poster" / "image". |
| `has_year` | Films have one, artists do not. |
| `has_content_type` | Movie vs TV. Meaningless for artists. |
| `has_review_gate` | CAN this project have an approval step. |
| `settings` | Per-project setting overrides, written **only when absent**. |

Add a capability as a **column**, not as a special case in a template.

---

## Saying the right words

### In templates

`app/templating.py` injects the vocabulary into **every** render. Just use it:

```jinja
{{ noun }} {{ nouns }} {{ Noun }} {{ Nouns }} {{ NOUN }} {{ NOUNS }}
{{ source_label }}      {# TMDB · Brave image search #}
{{ target_label }}      {# FineArtAmerica #}
{{ processor_label }}   {# Photoshop · AI image generation #}
{{ has_year }} {{ has_content_type }} {{ search_mode }}
```

**Trap:** these do not interpolate inside another `{{ }}`. This is wrong and
renders literally:

```jinja
{{ info("Live {{ noun }} counts") }}      ✗
{{ info("Live " ~ noun ~ " counts") }}    ✓
```

**Trap:** a top-level `{% set %}` in a *parent* template is **not** visible
inside a child's blocks. That is why `admin_pipeline.html` re-resolves `pctx`
itself rather than relying on `base.html`.

### In JavaScript

`base.html` publishes the same words as `window.PD`:

```js
PD.noun  PD.nouns  PD.Noun  PD.Nouns  PD.NOUN  PD.NOUNS
PD.sourceLabel  PD.targetLabel  PD.hasYear  PD.hasContentType
```

Never write `x.item_noun || 'poster'`. That fallback is silently wrong on
every project except the movie one, and it had already spread to four files
before it was caught.

### In worker payloads

Anything the worker screen renders comes through `worker._project_ui()`,
which is spread into every title payload. Add a field there rather than
teaching the front end about a niche.

---

## Scoping data — where the real damage happens

**`project_id IS NULL` means the DEFAULT project, not "any project".** The
101,605 imported movie rows are all NULL. Always pass the default:

```python
project_scope(project_id, default_project_id=_default_project_id(db))
```

Omitting that second argument makes every project inherit the movie backlog.
That is not theoretical: MUSIK's title list once showed all 101,605 films,
its worker queue handed them out, and its pipeline would have uploaded them
to the celebrity marketplace account.

Use the helpers; do not hand-roll:

| Context | Helper |
|---|---|
| Admin queries | `projects.scope_titles()` |
| Worker queries | `projects.scope_titles_multi()`, `worker._worker_project()` |
| Pipeline admin | `pipeline_admin._title_scope()` |
| Which project an endpoint acts on | `pipeline_admin._project()` — falls back to the ACTIVE project |
| Stats | `stats._poster_scope()` / `stats._title_scope()` |

Rules that have each already been broken once:

- **A worker's queue scopes to the ONE project they are standing in**, never
  to the union of their projects. `user_projects` says what they MAY touch;
  the active project says what they ARE touching.
- **Every pipeline API endpoint resolves its project through `_project()`.**
  They once took a query parameter the dashboard never sent, so every one of
  them silently operated on the movie project.
- **Storage paths come from the project of THAT TITLE**, not from the
  batch's project. A mixed batch filed MUSIK images under the movie folder.
- **Upload rows are filtered by `account.project_id`**, so a celebrity image
  can never be listed on the movie account under the movie title template.

---

## Which stage claims what

A project's `processor` decides who does the work:

- `photoshop` → the Windows node, via `claim_process_batch()`
- `gpt` → `gpt_worker`, a thread inside the web process

`pipeline.NODE_PROCESSORS` is the list the node is allowed to claim. **A
processor missing from that tuple is never handed to a node**, which is the
safe direction to fail.

Two traps here, both hit in production:

1. The node claimed greenlit work from *every* active project, so MUSIK
   images were opened in Photoshop and run through the movie effect.
2. When the filter matched **no** projects, the fallback query dropped the
   project filter entirely — "no project matched" and "no project specified"
   were spelled the same way at the call site. An empty match must return an
   empty list.

---

## Settings

Resolution order: `pipeline.<slug>.<key>` → `pipeline.<key>` → `DEFAULTS[key]`.

- **A save made inside a project writes project scope.** Saving globally
  while a project override exists produces a save that appears to do nothing
  — the field reverts on refresh with no error.
- **Exceptions are marketplace-wide**, listed in `MARKETPLACE_WIDE` in
  `admin_pipeline.js`: selectors and timings describe the marketplace's DOM,
  so fixing one should fix every project.
- `sync_projects()` writes a registry override **only when absent**, so a
  value you tune in the dashboard is never stamped back on the next deploy.
  The corollary: changing a default in `PROJECT_DEFS` does **not** change an
  existing install. Change it in the dashboard.
- Anything the owner might want to tweak belongs in the dashboard, backed by
  `app_settings`. A literal in a template or a constant in `config.py` for
  such a value is a defect.

---

## Adding a project

**A new project is a conversation, not a copy.** Do not clone the movie or
MUSIK definition and hope.

Ask what the niche actually needs, and say explicitly which existing pieces
would be **dead** for it. A project that does not use Photoshop must not
inherit the JSX editor and the sharpen radius. One whose sheet has no year
must not inherit the YEAR column. The capability columns exist so the answer
is data, not a template edit.

Checklist:

1. Add the entry to `PROJECT_DEFS`, including its `settings` overrides.
2. Say which stages it uses, and confirm the ones it does not are hidden —
   not merely unused.
3. Check `NODE_PROCESSORS` if it introduces a new processor.
4. Add a `SITE_LABELS` / `PROCESSOR_LABELS` entry so its screens read right.
5. Give it its own marketplace account. Accounts are project-scoped.
6. Assign workers via `user_projects` (no rows = no restriction, which is the
   state every pre-existing worker is in).

---

## Before you say it works

Compiling is not verification. Two checks that would each have caught a bug
that reached production:

```bash
# 1. Undefined names — py_compile does NOT catch these.
#    A missing `Project` import 500'd the save-history page.
python3 - <<'EOF'
import ast, builtins, glob
for path in glob.glob('app/**/*.py', recursive=True):
    tree = ast.parse(open(path, encoding='utf-8').read())
    # ...walk functions, compare loaded names against module scope
EOF

# 2. Every JS fetch target must exist as a route.
grep -oE "API \+ '[^']+'" app/static/js/*.js
```

And ask, every time: **would this still be right for a third project?**
If the answer needs a "well, for MUSIK…", it is not finished.
