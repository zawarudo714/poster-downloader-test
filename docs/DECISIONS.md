# Decisions — newest first

One line per decision: date · what was decided · why · where the detail lives.
A replaced decision stays here, marked SUPERSEDED, never deleted.
Older decisions are tagged `DECIDED <date>` where they apply (in the docs and
in `AUDIT.md`); search for `DECIDED` to find them.

- 2026-11-17 · Worker pictures the owner turns down (any flagged one, and any
  he deletes himself) are moved to `data/rejected_originals/` with a line in
  `index.jsonl`, not deleted; the admin's own picks are still deleted · owner:
  "Yes, sharpen rules" (keep flagged originals for a fair re-test of the AI
  picture check) · costs about 175 KB a picture on disk · `app/rejected_originals.py` (v302)
- 2026-11-17 · After every deploy that changes a page, Claude looks at it in the
  desktop app's built-in browser pane, which is signed in as the owner, without
  asking. Claude only looks and never ticks, edits, pays or saves unless asked.
  The owner signs in there himself if it ever shows /login · owner: "look
  always you dont need to ask" · v300 took 21 s live but 0.3 s on test data
- 2026-11-17 · Usage's WORKER PAY table sorts both money columns by the
  month the pictures were SAVED (v300). Paid KES = pictures paid, each at its
  run's rate (taken-back advances excluded); Pay KES = that plus the pictures
  still to pay at today's rate; a new To pay column counts them. Replaces the
  v298 "Paid = runs ending that month" and "Pay = pictures × today's rate",
  which made fully paid months look over- or under-paid. Live check: 22,247
  KES sent = Sep 5,165 + Oct 12,217 + Nov 4,865 (`MEASURED 2026-11-17`) ·
  owner: "whats difference between pay and paid", then "Yes, change it"
- 2026-11-17 · Usage shows the two server bills (Hetzner $14.09, Contabo
  €24.50, editable, stored as app settings `server_cost_*`) and each worker's
  pictures and KES per month, small, under the charts and never in them; a
  top-bar pill reminds from the 24th until Paid is ticked
  (`server_bill_paid_month`), then returns next 24th. Pay KES = payable
  pictures × today's rate, so an older month at an older rate is shown at
  today's rate; Paid KES is what runs ending that month really sent (v298); October 2026 counts as paid before
  any tick, so the first pill is 24 Nov (v299)
  · owner: "just like a small thing for me to see", "I can remove it only by
  ticking Paid" · `tools/test_server_bill.py`
- 2026-11-17 · The Google place check is removed (v297): no more Google
  Vision call after each save, no place pill, no PLACE CHECK THIS DAY panel,
  no "Google Vision key" or on/off box, no Diagnostics row. The
  `place_check_*` columns stay, unused, so no migration is needed. It cost
  about $5.60 in October (2,592 checks, 808 called "wrong place";
  `MEASURED 2026-11-17`) · owner: "it has no use really when I have to
  manually check anyway ... its not always accurate" · `AUDIT.md` Worker
  Images rows; the deleted module is described in `docs/HISTORY.md`
- 2026-11-05 · The Junique uploader waits only for the page, never a fixed
  time: no 4 s wait for a switch on a card marked "--disabled", no 3 s after
  the picture, checks every 0.1 s, and "Seconds between steps" 0.3 (was 1.0)
  for the few settles left. A last read of every switch before Save catches a
  click that landed on the wrong product. Target: 1 minute per design before
  Publish (Istanbul took about 5) · owner: "Junique needs no moment after each
  click" · `tools/junique/junique_upload.py`; the log's "took:" line measures it
- 2026-11-05 · All 318 old print files are remade with sharpening 80 and
  grain 4 (v295, `app/print_remake.py`); FineArtAmerica uses the same files
  for everything not yet live. The 201 already live there are left as they
  are · owner's instruction ("no need to complicate and separate things") ·
  `docs/FAILURES.md`
- 2026-11-05 · Junique uploads run from the owner's laptop, by clicking in a
  visible Chrome he watches, never by sending requests; the record of what
  went up stays on the laptop until the tool is proven, then moves to the
  server · owner's instruction · `tools/junique/README.md`
- 2026-11-05 · The AI session does every deploy, with `tools/deploy_cli.py`
  (this computer's SSH key, the same proof checks as the deploy window); the
  owner is never told to deploy. The node is still copied by the owner ·
  owner's instruction · `CLAUDE.md`
- 2026-11-05 · Every print file gets sharpening 80 and a grey grain of 4
  after the Lanczos enlargement, instead of AI upscaling · Junique refused 4
  of the first 10 as "upscaled"; this passed for all 4, costs nothing and
  needs no graphics card (Real-ESRGAN also passed but needs Vulkan or ~30 s
  of processor per poster) (v294) · `docs/FAILURES.md`
- 2026-11-05 · The daily upload limit counts the last 24 hours, not a
  calendar day; the node stops a run on a log-out or after 3 failures in a
  row · owner's ask after 101 extra uploads just after midnight Nairobi got
  the account logged out and 117 more failed (v293) · `PIPELINE.md`,
  `docs/FAILURES.md`
- 2026-11-04 · The Usage tab counts spend from the site's own token counts
  only, not OpenAI's admin usage API · owner's choice: the admin key would
  mix in his test keys. Days are UTC days, to match OpenAI's usage page; a
  repaint is any picture for a design that already had one of the same step
  (v292) · `app/ai_usage.py`, `PIPELINE.md`, `AUDIT.md`
- 2026-11-04 · The short "$60" painting prompt with the reworded title is the
  main prompt; the long one is deprecated and kept as a backup · it halves a
  painting's cost (about $60 a month at December volume) and letters about as
  creatively (test round 017) · `prompts/README.md`,
  `prompts/deprecated/painting_long_2026-11-01_NOTE.md`
- 2026-11-02 · Replies are short and in whole plain sentences. Every reply
  still says, in one line, what was checked by running, by reading, and what
  was not checked · owner chose this when TOKEN-SAVER's short status format
  clashed with `docs/PLAIN_WORDS.md` · `docs/PLAIN_WORDS.md`
- 2026-11-02 · CLAUDE.md holds rules and pointers only (about 150 lines); the
  detail moved, word for word, into `docs/` · it was 3,778 lines, about
  38,000 tokens loaded at every session start · this file's history in git
- 2026-11-02 · Bigger jobs go to helper agents on cheaper models: the lead
  keeps judgement and design; building from an existing pattern and running
  tests go to agents, which report back short · owner's choice (TOKEN-SAVER)
- 2026-11-01 · A review batch keeps its designs until it is saved; repaints
  join the next batch (v291) · owner's ask · `AUDIT.md`, `PIPELINE.md`
