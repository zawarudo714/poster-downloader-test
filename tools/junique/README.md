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

## Learning the upload steps

The owner records a few uploads with `extensions/upload_recorder` and hands
over the saved files. The steps found in them go into
`docs/facts/JUNIQUE.md`, which the owner corrects before anything is built.

## Adding more

Add a row per picture with the same columns. `storage_path` is
`processed_images.storage_path` (relative to `S:`).
