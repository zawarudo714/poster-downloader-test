# Playbook of known problems

Check this before investigating anything. Add a row the SAME DAY a cause is
found: sign · cause · fix · guard (the test or check that now catches it).
The long stories behind the older lessons are in `docs/rules/` — the table
in `docs/rules/00_INDEX.md` names the shape each rule catches.

| Sign | Cause | Fix | Guard |
|---|---|---|---|
| Junique refuses the upload: "look upscaled or out of focus" (4 of the first 10: Jakarta, Istanbul, Kilimanjaro, Cortina) | Step 2 paints about 1024×1536 and the server enlarges it about 4× with plain Lanczos. Junique's check sees the enlargement; a sharpness score does not predict which fail (`MEASURED 2026-11-05`) | v294: after the enlargement, UnsharpMask(2, 80, 3) then a grey grain of strength 4 (settings `upscale_sharpen` 80, `upscale_grain` 4). Passed for all 4 refused pictures (`MEASURED 2026-11-05` by the owner). Real-ESRGAN (Upscayl Lite) also passed but needs Vulkan or ~30 s of processor per poster, so it was dropped | `tools/test_upscale_grain.py` (sabotaged 3 ways); v295 records each file's settings (`print_treatment`), remakes old ones (`app/print_remake.py`, `tools/test_print_remake.py`, sabotaged 3 ways) and Diagnostics counts what is left |
| More than the daily limit uploaded; then a run of "Submit did not complete — still on the form" (FAA logged the account out) | `uploads_today` takes the Nairobi date but compares it with UTC upload times. From 00:00 to 03:00 Nairobi (21:00–24:00 UTC) it counts 0, and what goes up then is never counted. `MEASURED 2026-11-05`: 100 up 4 Nov 07:57–08:44 UTC, 101 more 21:01–21:48 UTC, then 117 rows failed 3 times each 22:11–00:16 UTC. "Still on the form" does not pause the account, so the run went on until the queue was empty | v293: the cap counts the last 24 hours (`uploads_last_24h`); the node stops on FineArtAmerica's logged-out page (parks the account 4 h) and after 3 failures in a row | `tools/test_upload_limit_24h.py` (both sabotaged); the day-boundary preflight no longer allowlists the line |
| A repaint appears inside the review batch being worked through; NEXT NOT MARKED jumps to it | Every reload re-picked the first N; a repaint is an older design, so it sorted in front | v291: the screen sends the open batch back as `batch` | `tools/test_review_batch.py` (sabotaged) |
| A setting saved on the dashboard has no effect | Saved as a project override, read with no project (or the reverse) | Read with `project=`; site-wide keys go in `pipeline.SITE_WIDE` | preflight "settings read without a project are site-wide" |
| A button does nothing, no error | An error inside an `async` handler, or a helper called from another wrapper's scope | Move the helper / fix the name | preflight `check_js_helpers_are_in_scope` |
| A page 500s after deploy on a new setting | Key missing from `pipeline.DEFAULTS` | Declare it | preflight settings checks |
| A route never receives requests | A parameterised route above it captures the address | Literal routes first | preflight `check_literal_routes_before_param_routes` |
| Live site crash-loops after deploy | A helper inserted between `@router` and its `def` stole the decorator | Insert helpers above the decorator | preflight `check_route_decorators_sit_on_routes` |
| A picture is paid for twice | A send that may have arrived was sent again | Ask OpenAI first (`_settle_unsent`); keep paid files | `tools/test_step2_batch.py`, `tools/test_paid_kept.py` |
| Everything reads GONE on the Listing check | Wrong artist name, so every address 404s | Fix the account's artist name | the sweep refuses above a small fraction |
| Something is "slow" | Usually the network link, not the database (measured 2026-08-27) | Time it before blaming code | — |
