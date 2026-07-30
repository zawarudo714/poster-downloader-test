# Post-Production Pipeline

Automates everything after a worker finishes a title: Photoshop processing,
permanent archiving, and marketplace uploading. Replaces the two local tools
(`FAA_Real_Paint_FX.jsx` run by hand, `FAA_MovieSeries_Uploader_v2.py` Tkinter
app) and their four sidecar JSON files.

**Read this file first if you are picking up this project.** It covers what
exists, why it's shaped this way, what state the data is in, and what's planned
next.

---

## 1. The whole flow, one image at a time

```
┌─ Linux VPS (178.105.34.144) ────────────────────────────────────────────┐
│  poster_downloader_web — the brain                                      │
│                                                                         │
│  Worker saves posters      → workspace/{user}/{date}/{N. Title (Year)}/  │
│  Admin reviews / flags     → revisions                                  │
│  Admin pays for the week   → PaymentRun  ──auto──►  GREENLIT            │
│                                                                         │
│  poster.db holds ALL pipeline state, settings, scripts and credentials   │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │  HTTPS, bearer token
                        │  /api/pipeline/*
┌───────────────────────▼─────────────────────────────────────────────────┐
│  Windows VPS — the muscle (stateless, disposable)                        │
│                                                                         │
│  worker_service/agent.py loops:                                         │
│    1. jobs      (tests first, then batch runs)                          │
│    2. process   claim → download source → Photoshop → write to storage  │
│    3. upload    claim → read from storage → Selenium → report per image  │
└───────────────────────┬─────────────────────────────────────────────────┘
                        │  SMB mount (S:\)
┌───────────────────────▼─────────────────────────────────────────────────┐
│  Hetzner Storage Box — the archive                                      │
│  Every processed _Painted.jpg, permanently.                             │
│  This is what makes account-ban recovery free: no reprocessing.         │
└─────────────────────────────────────────────────────────────────────────┘
```

An image ends up in three places: the **source** stays on the Linux VPS, the
**derivative** lives on the Storage Box forever, and the **listing** is on the
marketplace. Lose the marketplace account and nothing else is affected.

### Stage detail

| Stage | Where | What happens |
|---|---|---|
| Save | Linux VPS | Worker pastes a TMDB URL; app downloads to the frozen folder path |
| Greenlight | Linux VPS | Payment (or manual click) sets `greenlit_at`, posters → `greenlit` |
| Process | Windows VPS | Downloads source, runs the JSX on **one image**, writes `_Painted.jpg` to the Storage Box, reports dimensions/size/duration |
| Queue | Linux VPS | On a successful process, a `pending` upload row is created for **every enabled account** in the project |
| Upload | Windows VPS | Reads from the mount, one browser tab, one image at a time, reports **per image** |

---

## 2. Design rules — do not break these

These are the properties that keep the system extensible. Each one exists
because of a specific problem in the original setup.

### 2.1 Nothing is hardcoded
Every runtime string lives in `app_settings` and is resolved through
`pipeline.get_setting()`. That includes the **Photoshop JSX source**, every
**CSS selector**, the **title/keyword/description templates**, all **Selenium
waits**, batch sizes and the schedule.

`pipeline.DEFAULTS` is the single place a literal belongs. Adding a knob means
adding a `DEFAULTS` entry — and then it appears in the dashboard automatically
(see `SETTINGS_GROUPS` in `admin_pipeline.js`).

Why: when FineArtAmerica changes its upload form, the fix is editing a text
field in the browser and re-running a one-image test. Not a code change, not a
redeploy, not an SSH session.

### 2.2 Settings resolve project-first, then global
```
pipeline.<project_slug>.<key>   ← per-niche override
pipeline.<key>                  ← shared default
DEFAULTS[<key>]                 ← code default
```
A new niche inherits everything and overrides only what differs. This is the
mechanism the celebrity workflow will use.

### 2.3 Multi-project and multi-target from day one
Every pipeline table carries `project_id`. Accounts and tracking rows carry
`target_site` (`'faa'`, `'teepublic'`, …). There is exactly one project today
(`tell-a-vision`), but adding another is **inserting rows, never migrating
schema**.

Nothing in `pipeline.py` assumes movies, TMDB, or FineArtAmerica.

### 2.4 The database is the only source of truth
The legacy JSON files are imported once and then retired. Do not reintroduce
sidecar state files — that's what made the old setup impossible to reason about
across two machines.

| Legacy file | Now |
|---|---|
| `faa_upload_tracking.json` | `upload_tracking` table |
| `faa_config.json` | `upload_accounts` table (passwords Fernet-encrypted) |
| `faa_settings.json` | `app_settings` under `pipeline.*` |
| `faa_content_data.json` | Already in `master_titles` — same source CSV, same `external_id` |

### 2.5 Work is claimed, not just read
Claim endpoints flip status and set `claimed_at`/`claimed_by` in the same
transaction that returns the payload, so two nodes can't grab the same image.
`pipeline.reap_stale_claims()` runs before every dispatch and returns work
held by a node that stopped reporting (default 45 min). A hard crash costs one
cycle, not a queue.

### 2.6 Reporting is per item, never per batch
A node that dies on image 30 of 40 keeps credit for the first 29. This is why
`/upload/report` is called after **each** image.

### 2.7 Uploads are sequential, one tab
**Do not "optimise" this back to parallel tabs.** The legacy tool opened 35-50
tabs up front and lost 20-30% of every batch — worse as batch size grew.
Causes were structural: memory pressure crashing tabs, sessions expiring
before their tab was reached, stale element references, and many simultaneous
opens tripping rate heuristics.

Sequential is slower per image and that is irrelevant — the box is unattended
and only needs to land 100/day reliably.

### 2.8 Every stage is independently testable
`Pipeline → Test & Debug` runs one stage on one item and streams a per-phase
log. You never start a full run to check a small fix.

| Test | Proves |
|---|---|
| Test Download | Server URL, token, network path, file transfer |
| Test Process | The current JSX, on one image, output to `_tests/` |
| Test Upload | Login → form → each field → submit, phase by phase, nothing marked uploaded |

Failures capture a **screenshot and the page HTML**, stored server-side and
shown in the Failures list. That's how you see the marketplace changed its
markup instead of guessing.

---

## 3. Files

### Server (`app/`)
| File | Role |
|---|---|
| `pipeline.py` | **All policy.** Settings resolution, greenlight, dispatch, claiming, templates, status rollup, encryption, aggregates. Start here. |
| `models.py` | `Project`, `WorkerNode`, `UploadAccount`, `ProcessedImage`, `UploadTracking`, `PipelineJob` + new columns on `MasterTitle` / `SavedPoster` |
| `routes/pipeline_api.py` | Machine API at `/api/pipeline` — node bearer token only |
| `routes/pipeline_admin.py` | Dashboard API at `/admin/pipeline` — admin session only |
| `templates/admin_pipeline.html` | Seven-section control centre |
| `static/js/admin_pipeline.js` | `SETTINGS_GROUPS` drives every settings form — don't hand-write inputs |

The two routers are deliberately separate so a leaked node token can never
reach admin functionality.

### Worker node (`worker_service/`)
| File | Role |
|---|---|
| `agent.py` | The loop. `python -m worker_service.agent` |
| `client.py` | HTTP client. Retries connection errors only, never HTTP errors |
| `processor.py` | Photoshop, one image per invocation, with timeout + kill |
| `uploader.py` | Selenium, sequential, all selectors from config |
| `config.example.json` | The only local config: server URL, token, temp paths |

### Scripts
| File | Role |
|---|---|
| `scripts/migrate_pipeline.py` | Schema + legacy import + status backfill. Idempotent. |

---

## 4. Current data state

Measured against the live `poster.db` (2026-07-30) and the local legacy files.

| | Count |
|---|---|
| Master titles | 101,605 |
| Completed titles | 3,467 → 7,972 live posters |
| Photoshopped locally (22 date folders, Apr 29 – May 24) | 4,811 matched images |
| Uploaded to FineArtAmerica | 4,811 (+4 marked removed) |
| Paid (12 runs, 5 KES/poster) | 7,975 posters ≈ 39,875 KES |
| **Backlog: complete, not processed** | **~3,161 posters** |
| In progress (worker idle since ~24 Jun) | 39 titles |
| Pending, untouched | 98,004 titles |

**Verified before shipping:**
- Legacy → DB matching runs at **98.9%** (4,811 of 4,865) on both the tracking
  JSON and the processed-file tree.
- The schema migration was applied to a copy of the real 101,605-row database
  twice: idempotent, non-destructive, and the dispatcher's queries work.
- Letter suffixes (A/B/C) were checked against what was actually uploaded:
  4,808 of 4,811 agree. Three differ (`The Shawshank Redemption 4`,
  `Blade II 2` and `3`) because those titles were re-worked after upload.

### The 54 unmatched entries — two distinct causes

Worth understanding, because one of them is a **data-loss bug in the old
Photoshop script** and it destroyed real work.

**A. The JSX dot bug — 44 files across 44 titles**

`FAA_Real_Paint_FX.jsx` named its output like this:

```javascript
var newName = currentFile.name.split('.')[0] + "_Painted.jpg";
```

`split('.')[0]` truncates at the **first** dot. Any title containing a dot
collapsed every one of its posters onto a single output filename:

| Source posters | Old output name | Result |
|---|---|---|
| `E.T. 1.jpg`, `E.T. 2.jpg`, `E.T. 3.jpg` | all → `E_Painted.jpg` | 2 of 3 destroyed |
| `Kill Bill Vol. 1 1/2/3.jpg` | all → `Kill Bill Vol_Painted.jpg` | 2 of 3 destroyed |
| `House M.D. 1/2/3.jpg` | all → `House M_Painted.jpg` | 2 of 3 destroyed |

Each ran through Photoshop, then overwrote the previous one. **62 processed
posters were silently lost** and only 44 images (one per affected title)
reached FineArtAmerica.

Those posters are genuinely unprocessed work, so not importing them is the
correct outcome — the pipeline will redo them properly. Two consequences:

- The affected titles will produce 2-3 new listings each, as they should have
  originally.
- FineArtAmerica already holds one listing per affected title. **Expect one
  near-duplicate per title** and consider deleting the old one. The affected
  `external_id`s are listed in the orphan report.

`app/pipeline.py` computes output names with `os.path.splitext()` (last dot)
and hands Photoshop an explicit `OUTPUT_FILE`, so the bug cannot recur. This
was verified against every punctuated title in the real data: 11/11 distinct
names, all round-tripping back to the exact source stem.

**B. Poster renumbering — 10 files across 6 titles**

The number in a poster filename is not stable: `save_image` derives it from the
live poster count, so deleting and re-saving shifts it. On the first six titles
worked (`Shawshank`, `Dark Knight`, `Inception`, `Fight Club`,
`Game of Thrones`, `Forrest Gump`) posters were swapped after being uploaded,
so the uploaded filenames point at numbers that no longer exist. No action
needed.

Both categories are written to `pipeline_migration_orphans.json` with their
cause, the live filenames that *do* exist, and the affected `external_id`s.

**Neither category is in the master sheet's way** — all 2,077 tracked titles
resolve to a `master_titles` row. The mismatch is at the filename level within
a title, never the title itself.

At 100 uploads/day the backlog is **~32 days** of uploading — the marketplace
cap is the binding constraint, not processing.

---

## 5. Deployment

### 5.1 Server (do this first — safe, no downtime consequences)

```bash
ssh root@178.105.34.144
cd /path/to/poster_downloader_web
git pull

# Snapshot before schema changes. Non-negotiable.
docker compose exec web python -c "
import shutil, datetime
shutil.copy('/app/poster.db', f'/app/backups/manual-{datetime.date.today()}__pre-pipeline.db')
print('backed up')"

# Add the pipeline columns and tables.
docker compose exec web python scripts/migrate_pipeline.py --schema-only

docker compose up -d --build
```

`cryptography` is needed for account-password encryption — confirm it's in
`requirements.txt` before rebuilding.

### 5.2 Import the legacy history

Copy `faa_upload_tracking.json` and the `Outputs/Straight From Photoshop` tree
somewhere the container can read, then **dry run first**:

```bash
docker compose exec web python scripts/migrate_pipeline.py --dry-run \
  --tracking /data/faa_upload_tracking.json \
  --processed-root "/data/Straight From Photoshop"
```

Expect roughly: 4,811 processed registered, 4,811 tracking rows, 54 unmatched.
If those numbers look right, apply:

```bash
docker compose exec web python scripts/migrate_pipeline.py \
  --tracking /data/faa_upload_tracking.json \
  --processed-root "/data/Straight From Photoshop" \
  --account-name GR \
  --account-email darktitan72@gmail.com \
  --account-profile-url "https://fineartamerica.com/profiles/2-elton-odhiambo"
```

The account is created **disabled** until you set its password in the
dashboard — deliberate, so nothing can upload before you've reviewed the state.

### 5.3 Storage Box

Order a Hetzner Storage Box (BX10, 1 TB, ~€3.50/mo). Mount it on Windows and
copy your existing processed tree up — the migration already recorded paths in
this exact layout, so no renaming:

```cmd
net use S: \\uXXXXXX.your-storagebox.de\backup /user:uXXXXXX <password> /persistent:yes
robocopy "C:\Users\Administrator\Desktop\FineArtAmerica Tell-A-Vision\Outputs\Straight From Photoshop" S:\processed /E /Z /R:3 /LOG:C:\faa\copy.log
```

This copy is **not blocking**. The pipeline works without it; the archive only
matters for re-uploading to a replacement account later.

### 5.4 Windows VPS

Contabo VPS M or better (~€13/mo, 6 vCPU / 16 GB — Photoshop wants the RAM).

1. Install Photoshop + Real Paint FX, Chrome, matching chromedriver, Python 3.11+.
2. Mount the Storage Box as `S:` (persistent).
3. Copy `worker_service/` over; `pip install -r requirements.txt`.
4. Dashboard → **Pipeline → Nodes → Register Node**. Copy the token (shown once).
5. Create `worker_service/config.json` from the example.
6. Verify: `python -m worker_service.agent --once`
7. Install as a Task Scheduler task, run at startup, restart on failure.

### 5.5 First run, in order

1. **Pipeline → Upload** — set the real account password, enable the account.
2. **Pipeline → Processing** — fix `fx_script_path`, `photoshop_exe`,
   `storage_root` for the actual node paths.
3. **Test & Debug → Test Download** on any completed title.
4. **Test & Debug → Test Process** on one poster id.
5. **Test & Debug → Test Upload** on one processed image.
6. Only when all three pass: **Greenlight** one date, watch it run end to end.
7. Then greenlight the backlog.
8. Re-enable the worker (`humphrey` is currently `is_active=0`).

---

## 6. Operating it

### Greenlight
`greenlight_mode` is `both` by default: paying a run auto-greenlights exactly
the posters paid for (driven off `poster_ids_json`, so back-pay days are
included and unpaid days can't leak in), and manual greenlight stays available.
Set to `manual` to disable the hook entirely — no code change.

### When uploads start failing
1. **Failures** tab → open the screenshot. It shows what the browser saw.
2. If the markup changed: **Upload → Page Selectors**, fix it.
3. **Test & Debug → Test Upload** on one image to confirm.
4. **Failures → Retry Selected**. Attempt counters reset.

Retries are automatic up to `upload_max_attempts`. `EXHAUSTED` means it needs
a human. A systemic problem (bot wall, bad credentials, missing selector)
pauses the whole account instead of burning the queue.

### When an account is banned
1. Add a replacement account.
2. **Requeue Back Catalogue** — optionally mirroring the dead account.
3. It uploads from the Storage Box. Nothing is reprocessed.

Delete the dead account only if you want to; its history is kept either way,
and that history is what makes mirroring possible.

### Changing the Photoshop effect
Edit the JSX in **Processing**, save, **Test Process** on one image. The script
hash changes, the node reloads it on the next batch. Previously-processed
images keep their old `script_version`, so you can find and reprocess just the
ones that predate a change.

---

## 7. Extending it

### A second niche (celebrity — planned next)
1. **Pipeline → Nodes** section has project creation via
   `POST /admin/pipeline/api/projects`; slug e.g. `celebrity`.
2. Import that master list with its own columns → `master_titles` with
   `project_id = 2`.
3. Override only what differs, scoped to the project:
   `images_per_title = 2`, its own `keywords_static`, `title_template`, and its
   own `process_script` if the effect differs.
4. Add its FineArtAmerica account with `project_id = 2`.
5. Same nodes, same agent, no new code.

Source-site differences (Pinterest instead of TMDB) affect the **worker-facing**
save flow, not the pipeline. `Project.source_site` is already there to drive
that when you get to it.

### A second marketplace (TeePublic)
1. New account row with `target_site = 'teepublic'`.
2. Settings block for its selectors — the map is per-project already; add a
   target-scoped key if two marketplaces need different maps in one project.
3. A worker-side upload module for its form. `uploader.py` is written against
   the selector map, so much of it is reusable.

No schema change either way.

### A master dashboard across niches
`funnel_counts()`, `upload_history()` and `api_stats` all already take
`project_id`. A cross-project view is an aggregate over those, not new
plumbing.

---

## 8. Traps

- **`Base.metadata.create_all()` does not ALTER existing tables.** New columns
  on `master_titles` / `saved_posters` must go in
  `migrate_pipeline.NEW_COLUMNS`. New *tables* are created automatically.
- **`get_setting()` raises `KeyError` for unknown keys.** Intentional — it
  stops a dashboard typo from silently resolving to `None` at runtime. Add to
  `DEFAULTS` first.
- **Dict settings merge over the default.** So adding a selector to
  `DEFAULT_FAA_SELECTORS` reaches installs that already saved the old map.
  Don't replace the merge with an overwrite.
- **`PIPELINE_SECRET`** (falls back to `SESSION_SECRET`) decrypts account
  passwords. Rotating it silently breaks logins — accounts pause with a clear
  reason rather than crashing, but you'll have to re-enter passwords.
- **Poster letter index** (A/B/C) comes from creation order over *live*
  posters. `ensure_upload_rows()` and the migration must agree on this;
  changing one without the other rewrites titles for existing listings.
- **`project_id` is nullable** on `master_titles` so the app boots before the
  backfill. Pipeline code treats `NULL` as project 1 via `resolve_project()`.
  Don't add a `NOT NULL` constraint without a full backfill.
- **Test output goes to `_tests/`** in storage. Keep it that way so
  experimenting can't overwrite a live derivative.
- **Real Paint FX depends on Photoshop application state.** Its pattern
  (`.pat`) and action (`.atn`) sets must be loaded into Photoshop itself; they
  live in Photoshop's preferences, not in any file the pipeline controls. If
  the processing stage starts failing with plugin errors on a node that
  previously worked, check these first — a preferences reset or a rebuilt VPS
  loses them silently, with the script and all paths unchanged. Copies are kept
  at `S:\installers\realpaintfx-presets\`, and the node's snapshot is what
  normally preserves them. See `SETUP_WINDOWS_NODE.md` §5c.
- **The FX Box panel installer is not used and does not work on Photoshop
  2026.** `Fx Tool\Real-Paint-FX_installer.jsx` only registers the effect in
  the interactive panel and throws `TypeError: undefined is not an object` on
  current Photoshop. The pipeline calls
  `Real Paint FX\Scripts (actions)\Real-Paint-FX.jsx` directly. Don't chase it.

---

## 9. Quick reference

### Machine API — `/api/pipeline` (node token)
```
POST /hello                     handshake + poll/schedule hints
GET  /process/settings          rendered JSX + Photoshop settings
POST /process/claim             claim a Photoshop batch
GET  /source/{poster_id}        download a source image
POST /process/report            report one image's outcome
POST /upload/claim              claim an upload batch (creds + selectors + quota)
GET  /upload/image/{id}         fallback fetch when the mount is unavailable
POST /upload/report             report one upload's outcome
POST /upload/quota              remaining daily allowance
POST /jobs/claim                take a queued job (tests prioritised)
POST /jobs/{id}/log             append to the Live Console
POST /jobs/{id}/finish          close out with result or error
POST /artifact                  upload a failure screenshot / page dump
```

### Admin API — `/admin/pipeline/api` (admin session)
```
GET  /overview                  funnel, quotas, nodes, jobs, failures
GET  /greenlight/queue          awaiting greenlight, grouped by save date
POST /greenlight                by title_ids | dates | start+end | all_paid
POST /ungreenlight              pull unprocessed work back out
GET  /titles                    paged drill-down by stage
GET  /settings                  effective values + defaults + override scope
POST /settings                  save (scope: global | project)
POST /settings/reset            drop an override
GET  /settings/script_preview   the JSX as the node will receive it
GET  /accounts                  accounts + quota + per-status counts
POST /accounts                  create
POST /accounts/{id}             update (blank password keeps existing)
POST /accounts/{id}/resume      clear a node-set pause
POST /accounts/{id}/requeue     ban recovery
GET  /failures                  kind=upload|processing
POST /failures/retry            reset attempts and requeue
POST /failures/skip             exclude permanently
POST /failures/mark_removed     record a marketplace takedown
GET  /artifact?path=            serve a screenshot
POST /test/{download|process|upload}   queue a single-stage diagnostic
GET  /jobs, /jobs/{id}          job list / detail incl. log
POST /run                       trigger a batch now
POST /nodes, /nodes/{id}/rotate register / rotate token
POST /projects                  create a niche
```

### Pipeline stages
```
SavedPoster.pipeline_status   (authoritative, per image)
  NULL → greenlit → processing → processed → uploading → uploaded
                  ↘ failed_processing      ↘ failed_upload
                                            skipped

MasterTitle.pipeline_status  (rollup, for cheap listing)
  NULL | greenlit | processing | processed | uploading | uploaded | partial | failed

UploadTracking.status        (per image per account)
  pending → uploading → uploaded
          ↘ failed (retryable)
            removed (takedown) | skipped
```

### `external_id` is the universal key
The `0` column from the original CSV. It is the folder prefix
(`50. Pulp Fiction (1994)`), `MasterTitle.external_id`, and the key in both
legacy JSON files. Everything joins on it — preserve it.
