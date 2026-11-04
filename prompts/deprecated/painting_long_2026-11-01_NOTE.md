# DEPRECATED 2026-11-04: the long painting prompt

`painting_long_2026-11-01.txt` is an exact copy of the painting prompt the site
was using (the project's `openai_prompt` setting, read from the live database
on 2026-11-04; 30,931 characters). It is the same text as
`site_prompt_tester/prompt_current_2026-11-01.txt` and the prompt tested in
site_prompt_tester round 011 ("Website prompt as it is now v2").

**Replaced by** `../painting_main.txt`, at the owner's decision on 2026-11-04.

**Why it was deprecated:**

1. **Cost.** The prompt text alone was about 6,978 tokens per painting, about
   $0.0174 at batch prices and 58% of a painting's cost. The short prompt sends
   about 1,031 tokens. That halves a painting (from $0.0297 to $0.0149) and saves
   about $60 a month at December volume (about 4,060 paintings).
2. **Repetition.** The `title` section was 17,406 characters, 60% of the prompt.
   Four of its parts said the same thing ("invent the lettering from the scene"):
   scene_aware_geometry_system, scene_born_lettering, expressive_range and
   lettering_character.
3. **The short prompt now letters about as creatively as this one.** The first
   short prompt (rounds 015 and 016) had flatter lettering. Its title was reworded
   with ideas taken from this prompt, at the same word count:
   - every letter drawn individually (from `letter_construction`);
   - a secondary line that is never a smaller copy (from `secondary_line`);
   - a second ink (from `colour_system.second_ink`);
   - "never pale" (from `colour_system`).
   Round 017 then matched or beat this prompt on mixed letter sizes and on giving
   the secondary line its own colour.

**What this prompt still did better** (in case the main prompt is extended later):

- Letter surfaces textured like the subject, for example mossy stone on Beaufort
  Castle (`texture_system`).
- More scene colours for the main word, and fewer cream or white titles.

**To go back:** paste this file's text into the prompt box on the Pipeline
dashboard and press SAVE PROMPT.
