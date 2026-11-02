# Playbook of known problems

Check this before investigating anything. Add a row the SAME DAY a cause is
found: sign · cause · fix · guard (the test or check that now catches it).
The long stories behind the older lessons are in `docs/rules/` — the table
in `docs/rules/00_INDEX.md` names the shape each rule catches.

| Sign | Cause | Fix | Guard |
|---|---|---|---|
| A repaint appears inside the review batch being worked through; NEXT NOT MARKED jumps to it | Every reload re-picked the first N; a repaint is an older design, so it sorted in front | v291: the screen sends the open batch back as `batch` | `tools/test_review_batch.py` (sabotaged) |
| A setting saved on the dashboard has no effect | Saved as a project override, read with no project (or the reverse) | Read with `project=`; site-wide keys go in `pipeline.SITE_WIDE` | preflight "settings read without a project are site-wide" |
| A button does nothing, no error | An error inside an `async` handler, or a helper called from another wrapper's scope | Move the helper / fix the name | preflight `check_js_helpers_are_in_scope` |
| A page 500s after deploy on a new setting | Key missing from `pipeline.DEFAULTS` | Declare it | preflight settings checks |
| A route never receives requests | A parameterised route above it captures the address | Literal routes first | preflight `check_literal_routes_before_param_routes` |
| Live site crash-loops after deploy | A helper inserted between `@router` and its `def` stole the decorator | Insert helpers above the decorator | preflight `check_route_decorators_sit_on_routes` |
| A picture is paid for twice | A send that may have arrived was sent again | Ask OpenAI first (`_settle_unsent`); keep paid files | `tools/test_step2_batch.py`, `tools/test_paid_kept.py` |
| Everything reads GONE on the Listing check | Wrong artist name, so every address 404s | Fix the account's artist name | the sweep refuses above a small fraction |
| Something is "slow" | Usually the network link, not the database (measured 2026-08-27) | Time it before blaming code | — |
