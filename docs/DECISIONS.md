# Decisions — newest first

One line per decision: date · what was decided · why · where the detail lives.
A replaced decision stays here, marked SUPERSEDED, never deleted.
Older decisions are tagged `DECIDED <date>` where they apply (in the docs and
in `AUDIT.md`); search for `DECIDED` to find them.

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
