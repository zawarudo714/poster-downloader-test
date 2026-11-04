<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## Code map

```
app/
  models.py            All tables. Pipeline models are at the bottom with a
                       design contract comment — read it before adding one.
  projects.py          Active-project resolution + query scoping. Read its
                       docstring before touching navigation.
  diagnostics.py       Read-only consistency scanner (DB vs disk vs pipeline).
                       Never writes — see the docstring for why.
  pipeline.py          ALL post-production policy. Settings resolution,
                       greenlight, dispatch/claiming, templates, encryption.
                       DEFAULTS is the only place a pipeline literal belongs.
  payments.py          Eligibility + payment runs. Painting does NOT start
                       here since v265 — see pipeline.pictures_ready_to_paint.
  earnings/            Money coming IN. Sibling of diagnostics.py, NOT of the
                       pipeline — nothing here dispatches or changes a design.
    faa.py             Parsers for FineArtAmerica's Sales and Balance pages.
                       Every function takes a STRING, never a URL, which is
                       what let the fetching move to the node for free.
                       Deliberately contains no page URLs at all — TRUE AGAIN
                       as of 2026-08-27. It had quietly stopped being true:
                       `read_sales`, `read_ledger`, `fetch_sale_detail` and
                       `login` were still there, importing `requests` and
                       holding `BASE = "https://fineartamerica.com"`, two
                       lines above a comment insisting there were no URLs.
                       Nothing had called them since reading moved to the
                       node. Deleted — and the deletion matters more than the
                       160 lines, because that is the server-side fetch path
                       FAA answers with "Verify Visitor", and leaving it
                       sitting there looking usable is how the next session
                       spends three deploy cycles rediscovering that.
    matching.py        Attributing a sale to one of our designs. Refuses to
                       guess — see its docstring for why a wrong match is
                       worse than no match.
    service.py         Storing rows, queueing the node's reads, and the
                       figures each screen needs.
  schema_migrations.py Additive, idempotent column and index migrations, run
                       at startup. Data migrations do NOT belong here.
  brave_search.py      Query building and the image search. `{title}` is the
                       placeholder; `{artist}` is a legacy alias.
  gpt_worker.py        Image generation, on THIS server. Started only if a
                       project declares processor='gpt'. Step 1 (painting)
                       and step 2 (the flatten, v266), one at a time.
  ai_batches.py        The same two steps sent as OpenAI BATCHES — half
                       price, within 24 h. Send, poll, collect; every exit
                       puts its pictures back (v266) — except a send cut
                       short, which is looked up at OpenAI first (v286).
  ai_usage.py          What the OpenAI key has cost: one `ai_usage` row per
                       paid answer, from OpenAI's own token counts; the
                       sweep that fills in old batches; the Usage tab's
                       numbers (v292).
  listing_check.py     Is what we think is live on FineArtAmerica actually
                       there? 410 means removed, 404 means our ADDRESS is
                       wrong. Never collapse the two.
  routes/
    worker.py          Worker-facing (file name is historical; URLs don't say
                       "worker")
    admin.py           Admin dashboard, review, payments, users, backups
    pipeline_api.py    Machine API,  /api/pipeline,    node bearer token
    pipeline_admin.py  Dashboard API, /admin/pipeline, admin session
    listing_admin.py   The Listing check tab.
    usage_admin.py     The Usage tab, /admin/usage (v292). Read only.
  templates/           Jinja2. base.html holds the nav.
  static/js/           One file per page, vanilla JS, no build step.

worker_service/        Runs on the Windows VPS. agent.py is the loop.
                       (listing_swap.py, the live-listing picture swap of
                       v266, was REMOVED in v289 — the owner deletes and
                       RE-UPLOADs instead. Preflight keeps it removed.)
                       Updated by COPYING THE FOLDER, which has no receipt —
                       so AGENT_VERSION is bumped whenever anything in here
                       changes, and read back on the Nodes tab.
scripts/
  create_admin.py
  reset_workflow.py    Wipe all WORK (posters, pipeline, payments, chat, log)
                       and reset titles to pending. Keeps users, accounts,
                       settings, projects. Backs up first; refuses on a
                       production-looking DB without --force.
  dev_setup.py         Local dev: wipes and rebuilds everything. GUI + --cli.
DEV_SETUP.bat          Double-click launcher for the above (creates .venv).
```

---

## Running it locally

Double-click `DEV_SETUP.bat` (or `python scripts/dev_setup.py`). It wipes
`poster.db`, `workspace/` and `backups/`, then rebuilds:

- `admin` / `123456` and `worker1` / `123456`
- N master titles to claim
- Completed titles with **real PNG files** on disk (so the admin gallery,
  payments and the pipeline all have something to work with)
- A payment run, a registered pipeline node (token printed), a disabled demo
  marketplace account, and the seeded work already greenlit

The GUI has START SERVER / OPEN BROWSER buttons. `--cli` does identical work
headlessly through the same code path.

**It refuses to run against a production-looking database** (>5,000 master
titles, or >3 payment runs). That check is verified against the real
101,605-row database — do not weaken it. `--force` overrides, CLI only.

**Dependencies are checked before anything is deleted** (`preflight()`), and
`DEV_SETUP.bat` re-syncs `requirements.txt` on every run. Both exist because
adding `cryptography` to requirements after a venv already existed left setup
dying mid-run with a wiped database and no users. If you add a dependency the
tool needs, add it to `REQUIRED_MODULES` in `dev_setup.py` too.

---

