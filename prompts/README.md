# Painting prompts (step 1)

| File | Status |
|---|---|
| `painting_main.txt` | **The main painting prompt** since 2026-11-04 (DECIDED by the owner) |
| `deprecated/painting_long_2026-11-01.txt` | The long prompt the site used until then. Kept as a backup; why it was dropped: `deprecated/painting_long_2026-11-01_NOTE.md` |

`painting_main.txt` is the "$60 prompt" with the reworded title, 4,567
characters and 643 words. It is the prompt tested in site_prompt_tester round
017 ("Iteration 2 $60") and is the same text as
`site_prompt_tester/prompt_short_60_v2.txt`.

**The site does not read this folder.** The prompt the site really uses is the
`openai_prompt` setting of the project, saved with SAVE PROMPT on the Pipeline
dashboard. When the prompt changes, paste the new text there AND here, so this
file always matches the site.

What it measured (MEASURED 2026-11-04, rounds 011, 016 and 017, 20 pictures each,
OpenAI's own token counts, batch prices):

- Prompt text: about 1,031 tokens per painting, against 6,978 for the long prompt.
- One painting: about $0.0149, against $0.0297. Half the price.
- At December volume (about 4,060 paintings) that saves about $60 a month.
- Lettering: letters inside a word vary in size and width, and the secondary
  line usually has its own colour and construction (15 of 20, against 11 of 20
  for the long prompt).
- Watch spelling: round 017 drew "DESRT" for Sonoran Desert, and round 016
  dropped "Royal" from Port Royal. That is one error in 20 each, too few to judge.
