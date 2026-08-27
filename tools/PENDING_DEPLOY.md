# Not yet deployed

Written but NOT running on the server.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.

---

**Waiting: v132 — the $6.00 gap, found and fixed** · targets
178.105.232.196 (test box)

**Includes a schema change** (`ledger_entries.raw_type`), so back up
`poster.db` before deploying. It is one nullable column and
`schema_migrations.py` adds it with `IF NOT EXISTS`, so it is safe and
idempotent — but the rule is the rule.

## What it was

ONE row:

    2026-08-25  debit $6.00  entry_type 'other'
    "Highlander - 1986 A - T-Shirt - Navy - Medium"

FineArtAmerica put a word in its Type column that `classify()` does not
know, so the row was filed as `other`. The row was stored correctly, with
the right amount in the right column. But the reconciliation added up three
buckets — sales, payouts, refunds — each selected by `entry_type`, and
`other` was in none of them, so the money left the total.

    sales    +360.60
    payments  -87.30
    other      -6.00
              ───────
               267.30   = exactly what FineArtAmerica states

`MEASURED 2026-08-27` — this also explains REFUNDED reading `-$0.00`
everywhere: the one refund-shaped row there has ever been was not called a
refund.

**And the screen's diagnosis was wrong.** It said "rows are missing, so the
totals above are wrong. Try READ NOW." The row was already there; reading
again could never have helped. That sentence was a guess about the cause
presented as an observation.

## Fixed

**Sum the money, not our labels for it.** Totals are now credits minus
debits across EVERY row. Credit and debit are the marketplace's own
columns, so a row we cannot name still lands in the right place — and a
kind of entry nobody has ever seen counts correctly the first time, with no
code change. Verified against the real numbers: the old way was out by
exactly $6.00, the new way lands on their figure.

**`ledger_entries.raw_type`** now stores the marketplace's own word,
verbatim. Without it an unclassified row is a dead end — we could see the
money was unaccounted for and had no way to learn the word that would let
us name it. The Highlander row's word is unrecoverable; the next one will
not be.

**The message says what is known, not what caused it.** It no longer claims
rows are missing, and it points at their figure as the one to trust.

**A new line on the page** when unnameable rows exist: the money is counted,
but they are absent from REFUNDED and WHAT SOLD, and here is an example.

**`check_unclassified_ledger_rows`** in Diagnostics, so this surfaces
unattended instead of as a $6 discrepancy nobody can explain.

## After deploying

Open Earnings. GoldenR T should reconcile exactly — no red box. Underneath
you should see a line saying one row is of a kind the app does not
recognise, with the Highlander description.

**If you can, look at that row on FineArtAmerica's Balance page and tell me
the word in its Type column.** That is one line in `classify()` and it stops
being an unknown. From the next read onward the app records the word itself.

### Files

`app/earnings/service.py`, `app/earnings/faa.py`, `app/models.py`,
`app/schema_migrations.py`, `app/diagnostics.py`,
`app/static/js/admin_earnings.js`, `app/config.py` (131 → 132).
