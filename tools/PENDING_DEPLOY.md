# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v141 — review pass part 2: the migration flow was broken for
migration day** · targets 178.105.232.196 (test box)

**Deploy:** yes. **Copy `worker_service/` to the node:** NO — server-side
only, `AGENT_VERSION` stays 1.28.0. **Schema change:** none.

## The finding — traced end to end, and it would have burned THE day

The v124 `ensure_account` fix (rightly) stopped inventing
`unknown@example.com`. But the migration GUI drives the history import with
`--account-name` ONLY, and the migration plan deletes every FineArtAmerica
account before importing. So on migration day, as scripted:

  1. email lookup — skipped, no email given
  2. name lookup — nothing exists, all accounts deleted
  3. `ensure_account` returns None
  4. **the dry run returned None IN SILENCE** — the guidance message sat
     AFTER the `if dry_run: return None`, so the rehearsal that exists to
     catch exactly this would have passed quietly
  5. the real run then hit the per-row `account is None` branch — which is
     the DRY-RUN counting path — and would have printed
     **"+ 4,865 tracking rows" while writing ZERO**

The GUI's own does-it-add-up tally would have caught the zero afterwards
(that invariant earned its keep), but the script lies first, and any run
outside the GUI has no net. Without the tracking rows, the pipeline would
re-upload all 4,865 images to FineArtAmerica — the exact catastrophe the
import exists to prevent.

## Fixed, three layers

  * `ensure_account` prints the no-account guidance on the DRY RUN too, and
    it states the order that works: **create the account in the dashboard
    first** (real address — needed for uploading anyway), then run the
    import; the name lookup finds it. Or pass `--account-email`.
  * `import_upload_tracking` REFUSES a real run with `account=None`, loudly,
    before the row loop — it can no longer count rows it is not writing.
  * The rehearsal still counts correctly (dry run + no account is the
    legitimate counting case).

Verified structurally: guidance at line 198 precedes the dry-run return at
205; the refusal at 451 precedes the row loop at 474. My first AST check
claimed the second guard was missing — the checker was wrong, the plain
read confirmed placement. Same lesson as yesterday, caught in thirty
seconds instead of shipped.

## For the migration checklist

**Order on the day: add the real FineArtAmerica account in the dashboard
BEFORE running the upload-history import step.** The import finds it by
name and links it. This was implicitly required before and is now enforced
and said out loud by the tool.

### Files

`scripts/migrate_pipeline.py`, `app/config.py` (140 → 141).
