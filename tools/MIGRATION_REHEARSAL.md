# Migration rehearsal

Run 2026-08-25 09:46. The rehearsal stack answered `{"ok":true,"version":"115"}`.

**1 table(s) lost rows or changed count — do NOT run this against production yet.**

| | table | detail |
|---|---|---|
| new | `account_projects` | table created by the upgrade |
| new | `api_spend` | table created by the upgrade |
| new | `ledger_entries` | table created by the upgrade |
| new | `listing_sweeps` | table created by the upgrade |
| new | `marketplace_snapshots` | table created by the upgrade |
| new | `pipeline_jobs` | table created by the upgrade |
| new | `processed_images` | table created by the upgrade |
| new | `projects` | table created by the upgrade |
| new | `search_cache` | table created by the upgrade |
| new | `store_designs` | table created by the upgrade |
| new | `store_listings` | table created by the upgrade |
| new | `store_scan_runs` | table created by the upgrade |
| new | `title_aliases` | table created by the upgrade |
| new | `upload_accounts` | table created by the upgrade |
| new | `upload_tracking` | table created by the upgrade |
| new | `user_projects` | table created by the upgrade |
| new | `wall_paths` | table created by the upgrade |
| new | `worker_nodes` | table created by the upgrade |
| same | `master_titles` | 101605 rows |
| same | `saved_posters` | 10355 rows |
| same | `users` | 3 rows |
| same | `payment_runs` | 15 rows |
| same | `revisions` | 161 rows |
| same | `activity_log` | 24654 rows |
| CHANGED | `app_settings` | 11 rows before, 15 after (+4) |

## What this did NOT prove

* The workspace reshape ran against a handful of FABRICATED folders, not the real tree. One directory rename per worker is the same code path, but the real tree has not moved. Run step 4 to test it properly.
* Nothing here touched production. Its database was copied and read; the machine was not changed.
* A clean rehearsal says the schema survives. It does not say the site behaves — click around the rehearsal on port 8081 before trusting it.