# Changelog

All notable changes to the Poster Downloader web app are documented here.

---

## v15 — 2026-05-25

### New features

- **Skip deletes posters**: When a worker saves posters under a title and then
  clicks Skip instead of Done, all live posters on that title are now
  automatically soft-deleted and removed from disk. Skipped titles should not
  have posters counting towards pay.

- **Complete requires ≥1 poster**: A title can only be marked as "complete" if
  it has at least one live poster. Both the worker's Done button and the admin's
  Approve Completion button enforce this guard. Titles with zero posters should
  be skipped, not completed.

- **Cross-title duplicate image URL warning**: The system now tracks image URLs
  saved today across all titles. If a worker tries to save a URL that was
  already used on a different title the same day, a warning is shown (not a hard
  block) identifying which title already has that image. This catches the common
  mobile issue where the clipboard doesn't update between copies.

- **Payment "NOT RECEIVED" button**: When a payment receipt is pushed to the
  worker panel, a "NOT RECEIVED" button now appears alongside "ACKNOWLEDGE". If
  the worker hasn't seen the payment on their end, they click this instead. The
  admin sees a red "NOT RECEIVED" status pill on the Payments page and can
  follow up. Re-pushing a receipt clears the dispute flag.

### Schema migration

This round adds one new column to `payment_runs`. Run **before** rebuilding:

```bash
docker compose exec web python -c "
import sqlite3
c = sqlite3.connect('/app/poster.db')
c.execute('ALTER TABLE payment_runs ADD COLUMN not_received_at DATETIME')
c.commit()
print('OK')
"
```

If it errors with `duplicate column name`, you've already run it — safe to
ignore. Then rebuild:

```
git pull && docker compose up -d --build
```

### Internal

- `APP_VERSION` bumped to `15`.
- Changelog file (`CHANGELOG.md`) added to project root.
- Version archives now saved to `versions/` folder after each release.

---

## v14.3 and earlier

See `README.md` for historical round notes.
