# Not yet deployed

## v174 — "N/A" is made impossible, not cleaned up

The owner reported (2026-09-09) that "(N/A)" was still showing beside titles
on the Activity Log, and asked whether resetting the site would remove it
everywhere or whether the letters were written into the code.

**They were written into the code, in four places.** A reset on its own
would have brought all four straight back. This release removes them and
adds two mechanical checks so they cannot come back quietly.

What changed:

- `app/models.py` — the `year` column was `nullable=False, default="N/A"`.
  That single default is where the letters came from. The column is now
  nullable with no default, so a title with no year holds nothing.
- `app/routes/worker.py` — the folder builder was handed `t.year or "N/A"`,
  which wrote `1. Santorini (N/A)` as a folder name on disk. Folder paths
  never change once written, so this one was permanent. It now passes
  nothing.
- `app/parsing.py` — `folder_name_for` only adds the brackets when there is
  a year. Two dead functions that returned "N/A" were deleted; nothing
  called either of them.
- `scripts/dev_setup.py` and `tools/test_sheet_columns_check.py` — the local
  seed data used "N/A", so local testing reproduced the bug.
- `app/static/js/admin.js` and `app/static/js/admin_pipeline.js` — two
  screens drew the year with no guard at all, so even with perfect data they
  would have printed an empty `()`. Both are guarded now.

New mechanical checks:

- `preflight.py` · **absence is NULL, never a magic word** — fails on a NOT
  NULL column defaulting to one of these words, and on any code that falls
  back to one. Sabotage-tested three ways: the column default, the folder
  fallback, and a seed row. All three go red.
- `diagnostics.py` · **year_is_a_year_or_nothing** — reports any title whose
  year is not four digits. Preflight is about the code; this one is about
  the data, so it holds whatever an import does later.

**Deploy this.** The Windows node did NOT change, so there is nothing to
copy and `AGENT_VERSION` stays where it is.

**On the live server the DATA still has to be cleaned.** The code change
stops new "N/A" values; it does not rewrite rows that already hold the
letters. That happens for free in the reset to zero. If you want to see
whether your current rows carry it, run:

    cd /opt/poster && docker compose exec web python -c "from app.db import SessionLocal; from app.models import MasterTitle; from sqlalchemy import func; db=SessionLocal(); print(db.query(MasterTitle.year, func.count()).group_by(MasterTitle.year).all())"

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
