# Junique — pictures uploaded by hand

`MANUAL_UPLOADS.csv` lists every picture the owner put on Junique by hand
(shop "Illusination Travel Prints"). **Any future Junique upload
automation must skip every `saved_poster_id` in that file**, or it will
list the same design twice. (DECIDED 2026-11-05: the owner asked for this
record before learning the site.)

The first 10 were picked on 2026-11-05 so the shop can be sent for approval
(Junique asks for 5–20 strong designs first). Each row is the current,
approved step 2 picture (4000×6000), looked up read-only in the server
database by its FineArtAmerica title.

## Get the files

```
powershell -ExecutionPolicy Bypass -File tools\junique\fetch_manual_uploads.ps1
```

It copies each file from the Storage Box (`S:`) to
`Downloads\Junique first designs`, named by title, and opens that folder.

## The tools (v296, on the laptop)

| File | What it does |
|---|---|
| `junique_upload.py` | The window: Fetch, Open Chrome, Upload. Clicks through Junique in its own Chrome (`local/chrome_profile`, signed in once by hand). Stops before Publish unless "Publish by itself" is ticked |
| `fetch.py` | The same Fetch from the command line: `python tools/junique/fetch.py 20` |
| `ledger.py` | The record (`local/ledger.json`) and the queue rule: unfinished or failed first, then new in approval order; skips this CSV; gives up on a design after 3 failures |
| `test_ledger.py` | Behaviour test of the queue, with the owner's 50/48/20 example |

The list of ready designs comes from the server over SSH
(`app/junique_list.py`): approved, current, and made with today's sharpen and
grain. A design counts as uploaded only when Junique answers Publish.
Everything in `local/` stays on the laptop (git-ignored). The page facts the
tool relies on are in `docs/facts/JUNIQUE.md`.

## Adding more

Add a row per picture with the same columns. `storage_path` is
`processed_images.storage_path` (relative to `S:`).
