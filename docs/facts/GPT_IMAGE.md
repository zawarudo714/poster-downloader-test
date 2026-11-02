<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## GPT Image 2 behaviour — `LEAD 2026-09-03`, from a third-party guide

Read from a WaveSpeed blog post, NOT from OpenAI. Tagged LEAD rather than
MEASURED on purpose: nothing here has been tested against the API by us. Two
of its claims matched what this repo knew independently — the model ID, and
the per-million-token prices the spend meter carried before v172 removed it —
which is why the rest is worth writing down at all. Confirm before acting.

* **`moderation` defaults to `auto`; the owner's playground uses `low`.** An
  omitted parameter is therefore NOT the same as the playground's setting.
  This is the general trap OpenAI's own troubleshooting note describes, and
  the only concrete mismatch found so far between the two surfaces.
* **`input_fidelity` must be OMITTED.** gpt-image-2 always works at high
  fidelity and refuses the request if the parameter is present. It also
  cannot explain any difference between surfaces, because both are high.
* **`background: transparent` WORKS, AND THE OWNER USES IT ON PURPOSE.**
  `MEASURED 2026-09-05`, and it overturns what this file said before, which
  came from the WaveSpeed post and claimed the model refuses it.

  It is not a background setting in any ordinary sense: asking for
  transparency changes HOW gpt-image-2 renders, and the owner's whole poster
  look depends on it. The see-through result is a side effect he flattens
  away afterwards, not the goal.

  Most posters flatten correctly onto black. A few come back with a
  semi-transparent sky, which goes muddy on black and reads correctly on its
  own colour — Bangkok was the specimen. So the review screen carries a
  colour per image with an eyedropper, the transparent original is kept as
  `ProcessedImage.master_path`, and changing the colour is a local re-render
  rather than another call to OpenAI.

  **The lesson is about the source, not the setting.** That WaveSpeed entry
  is tagged LEAD for a reason and this is the first of its claims anybody
  tested. One of five was wrong. Treat the other four the same way.
* **Every image input is processed at high fidelity**, so a 4K reference
  costs the same input tokens as a small one. Downscaling references is free
  money. Untested here, and it would change the picture, so not to be done
  in the middle of a comparison.
* **PIN THE MODEL SNAPSHOT IN PRODUCTION.** `gpt-image-2` is a floating
  alias, so OpenAI can change what it points at and the pictures drift with
  no deploy on our side and nothing on any screen to say why. That is the
  worst shape this project has: a silent change with a plausible-looking
  result. Worth doing before the first real batch — `openai_model` is
  already a dashboard setting, so it costs one edit.

