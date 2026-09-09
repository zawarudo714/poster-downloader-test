# Not yet deployed

## v172 — six changes in one release

**Server only. The Windows machine is unchanged, so nothing needs copying.**
**No new database column, so no backup is strictly required — but take one
anyway, because this removes a table from the code.**

### 1. "Canceled Item" is a refund

FineArtAmerica writes **"Canceled Item"** in the Type column when an order is
cancelled, as a debit against the artwork. `classify()` knew six words and
that was not one, so those rows sat as `other` — counted correctly in the
balance, missing from REFUNDED and WHAT SOLD.

`MEASURED 2026-09-09` from the owner's Balance page, and checked against
their running balance: each cancelled row moves the balance down by exactly
its own amount, so it reverses a sale. The cancel test runs BEFORE the sale
test, because "Canceled Sale" matching on "sale" would file a debit as
income — wrong on screen while the balance still added up.

### 2. The orphan-files check was a FALSE ALARM pointing at a delete button

`check_orphan_files` built its known-file list from `SavedPoster` rows only,
so it reported the owner's SIGNATURE and REFERENCE PICTURE as unknown files
"the app has no idea" about. Acting on it would have stopped the poster
builder — a signature switched on with no file is a hard refusal.

A file can be claimed three ways: a ROW points at it, a SETTING names it, or
a COLUMN holds its path. It knew one. It now knows all three, and the
settings side is derived from `DEFAULTS` rather than a list, so a future
setting that names a file is covered on the day it is added.

### 3. Failure evidence is capped at 30

Nothing ever deleted it. `failure_evidence_keep` (default 30, 0 = keep all)
prunes each folder when a new failure arrives, so 30 screenshots and 30 page
dumps — the owner's "30 as in 60 when paired up". Counted per folder rather
than per failure on purpose: the two files of one failure are separate
requests and can land a second apart.

Pruning runs AFTER the new file is safe and swallows its own errors, because
housekeeping must never fail an upload report and strand a poster.

### 4. The status strip shows every job, not two

It built "doing now" from two counts of IMAGES, so six of the machine's eight
jobs were invisible and a listing check ran for an hour under the word
"nothing". It now reads the running jobs table, which already stores each
job's kind — so a ninth kind appears on its own instead of waiting for
somebody to add a ninth counter. An unrecognised kind is spelled out rather
than dropped.

### 5. ALL the OpenAI and Brave spend machinery is gone

Deleted: `openai_costs.py`, the `ApiSpend` table, `PRICE_PER_MTOK`,
`record_spend`, `month_to_date_usd`, `cap_state`, `Generation.cost_usd`, the
`/api/spend` endpoint, the SPENDING panel, both spend findings on Needs
Attention, the nightly reconcile, the admin key, and the settings behind all
of it.

**Two things to know:**

  * **THE MONTHLY CAP WENT WITH IT.** The painting loop used to consult it
    before claiming each image. Nothing on this server now limits what a
    night of generation can cost — the ceiling is whatever spend limit the
    OpenAI account itself carries. His cap was set to 0 (off) already.
  * **`brave_daily_query_cap` was never read by anything.** It had a box
    describing itself as a safety net against a looping bug and no code
    consulted it. Removed rather than wired up, because it belonged to the
    feature being deleted.

On an existing install the `api_spend` table stays behind unused, because
`create_all()` never drops anything. A fresh database simply never makes it.

### 6. Earnings start date — old rows are PARKED, never dropped

`earnings_start_date` on the Earnings page. Sales before it are previous
business: still imported, still counted in every total, never matched and
never shown on the unmatched list.

**They are not dropped, and that is the design.** FineArtAmerica prints a
Current Balance that our gross minus our payouts has to land on — the only
outside number that can tell us we have missed a row. Importing a subset
would break that checksum for ever, and break `due_next` with it.

The cutoff rides on `MatchIndex`, which is already built once and threaded
through every path that matches anything, so no caller can forget it and it
costs no query per row.

---

## Verified

  * `preflight.py` clean. Every touched Python file and every touched script
    parses. The template's tags balance.
  * **Behaviour tests, all against code LIFTED OUT OF THE SHIPPED FILE** so a
    test could not be handed a copy of the rule it checks: 9 cases on
    `classify()`, 7 on the evidence pruning, 7 on the earnings cutoff.
  * **Sabotage:** removing the box for `failure_evidence_keep` goes red;
    removing the save call for `earnings_start_date` goes red. The first
    attempt at both tested the wrong thing and had to be redone — the
    `data-` attribute alone does not make a setting reachable.
  * Full sweep for surviving spend code: nothing left that executes.

## NOT verified

  * Anything that needs a browser or a real database. Whether the strip reads
    well mid-listing-check, whether the earnings box saves, and whether the
    orphan list is now empty are all things only the owner can see.
  * The pruning has never run against a real evidence folder on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
