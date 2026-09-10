# Every way a title can be wrong

**A title is not a label. It is half of a primary key.** The listing address
on FineArtAmerica is built from `{title-slug}-{artist-slug}`, the folder on
disk is built from the number and the title, and a sale is attributed back to
a design by matching its name. So a title that is wrong is not untidy — it is
a listing nothing can find again, or two workers paid to photograph one place.

This file is the enumeration the owner asked for on `2026-09-10`, after the
folding check found twenty duplicate place names on its first real run and he
said: *"You cant miss things like this so draft up a clean parameter master
list to check for things like this."*

**The rule for reading it: every row says which check covers it, or says
plainly that nothing does.** A row with no check is a known gap, not an
oversight — and if a row here has no check and no reason, that is a defect in
this file.

---

## WHERE A BAD TITLE CAN ENTER

There are only three doors, and knowing them is what makes the list finite.

1. **The imported sheet.** `IMPORT_titles.csv`, 88,970 rows. Almost every
   problem below arrives here.
2. **A hand-typed replacement.** The retitle box on Needs Attention, used
   when the marketplace has refused one.
3. **A marketplace rename we did not ask for.** FineArtAmerica silently
   rewrites on save, so the title we sent and the title that exists are not
   always the same string.

The checks below run against the whole catalogue at rest, so they cover all
three doors at once rather than one guard per door.

---

## THE LIST

### A · TWO TITLES BECOMING ONE ON THE MARKETPLACE

The most expensive kind, because the damage is silent and permanent. FAA does
not refuse a duplicate — it appends `#2`, and that listing then lives at an
address we never computed, so the listing checker reads it as missing for
ever. Deleting does not release the name either.

| # | The fault | Example | Covered by |
|---|---|---|---|
| A1 | **Accents fold together** | `Bolimów, Poland` · `Bolimow, Poland` | `titles_collide_after_folding` |
| A2 | **Punctuation is deleted** | `Aoraki / Mount Cook` · `Aoraki Mount Cook` | same |
| A3 | **Case-only difference** | `Paris, France` · `paris, france` | same — the comparison is lower-cased |
| A4 | **Spacing-only difference** | `Paris,  France` (two spaces) | same — spacing is collapsed before comparing |
| A5 | **Truncation at 100 characters collides** | two long titles sharing their first 100 characters | same — the fold is truncated before comparing |
| A6 | **A title already ending in FAA's own numbering** | `Springfield #2` | `titles_the_marketplace_would_reject` |

### B · TITLES THE MARKETPLACE WILL NOT ACCEPT AS SENT

These fail at upload, one at a time, after a worker has already been paid to
photograph the place and the machine has already been paid to paint it. The
existing guard catches them at dispatch, which is far too late to be the only
guard — hence the same question asked of the catalogue at rest.

| # | The fault | Example | Covered by |
|---|---|---|---|
| B1 | **Nothing survives folding** | a name in a non-Latin script | `titles_the_marketplace_would_reject` |
| B2 | **Under two characters after folding** | `Å` | same |
| B3 | **More than half the characters lost** | heavy diacritics | same |
| B4 | **Over 100 characters** — truncated with no error | a long official place name | same |
| B5 | **Empty or whitespace only** | | same |

### C · TITLES THAT COLLIDE ON DISK RATHER THAN ON THE MARKETPLACE

A separate failure with a separate cause. Windows forbids nine characters in
a folder name, so the folder builder replaces them — and two different titles
can be replaced into ONE folder name. The second worker's photographs then
land in the first one's folder.

| # | The fault | Example | Covered by |
|---|---|---|---|
| C1 | **Two titles, one folder after sanitising** | `Rome: Forum` · `Rome- Forum` | `titles_that_share_a_folder` |
| C2 | **Trailing dot or space** — Windows strips it silently | `St. Peter's ` | same |
| C3 | **A reserved device name** | `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9` | same |

### D · CHARACTERS NOBODY CAN SEE

The worst to diagnose by eye, because the screen shows two identical strings
and every comparison says they differ. Usually arrives from a spreadsheet or
a copy-and-paste out of a web page.

| # | The fault | Covered by |
|---|---|---|
| D1 | **Leading or trailing whitespace** | `titles_with_invisible_characters` |
| D2 | **Doubled spaces inside** | same |
| D3 | **Non-breaking space** (U+00A0) posing as a space | same |
| D4 | **Zero-width characters** (U+200B–U+200D, U+FEFF) | same |
| D5 | **Tab or newline inside a title** | same |

### E · THE KEY ITSELF

`external_id` is column 0 of the sheet, and inside a project it is the key for
everything — the folder prefix, the row, the upload record. It is unique only
INSIDE one project, and it has to be there at all.

| # | The fault | Covered by |
|---|---|---|
| E1 | **Two rows share one external_id** | `external_ids_are_sound` |
| E2 | **No external_id at all** | same |
| E3 | **A year that is not a year** | `year_is_a_year_or_nothing` |

---

## DELIBERATELY NOT CHECKED, AND WHY

Writing these down matters as much as the checks. A gap somebody chose is a
decision; a gap nobody noticed is a defect.

- **Whether a place actually exists.** No machine here can know that
  `Bolimow, Poland` is a real town. The sheet is the owner's to trust.
- **Whether two DIFFERENT places share a name.** There are many places called
  Springfield, and both may legitimately be listed — but they cannot both be
  listed under the bare word `Springfield`, and that collision IS caught by
  A1–A5. Disambiguating them is his editorial choice, not a fault.
- **Whether the title reads well.** Taste is not checkable.
- **Whether a title matches its photograph.** That is what the Approve
  Artwork screen is for, and it needs eyes.
- **Bare country names.** He cut 50 of these by hand on 2026-09-06. It is a
  judgement about what sells, not a fault in the data, so it stays a decision
  rather than an alarm.

---

## HOW TO USE IT

Run **Diagnostics** after any import, and before greenlighting anything. The
five checks above are the ones whose names begin with `titles_` or
`external_ids_`. Each says how many it found and what to do.

**Fix the catalogue, not the symptom.** Every one of these is a row in
`IMPORT_titles.csv` that should be edited or deleted. Renaming a listing after
it is live does not give the old name back — see the title rules in
`CLAUDE.md` — so the cheap moment is now, while nothing has been uploaded.

**A count of zero is worth as much as a finding.** These checks are the only
thing standing between an 88,970-row spreadsheet and a catalogue of silently
renumbered listings, so seeing them all read zero after an import is the
evidence the import was clean.
