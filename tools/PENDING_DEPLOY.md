# Not yet deployed

## v249 — PROMPT TEST MODE on Approve Artwork (2026-09-29)

- New panel under WAITING FOR REVIEW: a switch, the pile count, a prompt
  name and text box (with saved prompts and START FROM THE MAIN PROMPT),
  PICTURES PER ROUND + TEST THE PROMPT ON N, a table of rounds with their
  scores, REVIEW and TRY THIS ROUND AGAIN, and DELETE ALL TEST DATA.
- While on, every repaint waits in the pile (`SavedPoster.rerun_hold_at`),
  filled by RERUN and also by the GPT painter's claim, so a RETRY of the
  32 no-credit pictures cannot slip past it. A round's pictures carry
  `prompt_round_id`; the painter paints them with the round's prompt and
  clears the mark when it files the painting. Off = reruns paint straight
  away as before; the pile keeps waiting for the button.
- Rounds are judged KEEP or RERUN only (no Photoshop, Photopea or
  unusable; the server refuses them too) and are left out of every other
  review door.
- New tables `prompt_test_prompts`, `prompt_test_rounds`,
  `prompt_test_items` (created on start); new columns on saved_posters
  (migration). New settings `prompt_test_mode`, `prompt_test_round_size`.
  New Diagnostics check `check_prompt_test_marks_are_sound`.
- The five "waiting to be painted" counts and the node's claim skip the
  pile.
- Files: `app/prompt_test.py` (new), `app/models.py`,
  `app/schema_migrations.py`, `app/pipeline.py`, `app/gpt_worker.py`,
  `app/gpt_images.py`, `app/diagnostics.py`, `routes/pipeline_admin.py`,
  `routes/admin.py`, `static/js/admin_review_images.js`,
  `static/js/admin_prompt_test.js` (new), `templates/admin_review_images.html`,
  `static/css/style.css`, `scripts/reset_workflow.py`, `config.py`.
- The Windows node is not affected. No copying needed.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
