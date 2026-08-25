# Migration rehearsal

Run 2026-08-25 09:51. The rehearsal stack answered `{"ok":true,"version":"116"}`.

**No existing row counts moved. 0 table(s) were created by the upgrade, which is what it is for.**

| | table | detail |
|---|---|---|
| same | `master_titles` | 101605 rows |
| same | `saved_posters` | 10355 rows |
| same | `users` | 3 rows |
| same | `payment_runs` | 15 rows |
| same | `revisions` | 161 rows |
| same | `activity_log` | 24654 rows |
| same | `app_settings` | 15 rows |

## What this did NOT prove

* The workspace reshape ran against a handful of FABRICATED folders, not the real tree. One directory rename per worker is the same code path, but the real tree has not moved. Run step 4 to test it properly.
* Nothing here touched production. Its database was copied and read; the machine was not changed.
* A clean rehearsal says the schema survives. It does not say the site behaves — click around the rehearsal on port 8081 before trusting it.