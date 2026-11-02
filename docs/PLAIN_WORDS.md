<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## BEFORE YOU WRITE A SINGLE WORD TO THE OWNER

Go and read **"Write in plain words"** near the end of this file, and follow
it in every reply. He asked for this on 2026-08-17, again on 2026-09-02, and
then a third time the same day because simple words in cramped sentences
still were not readable.

The short version, but read the full section anyway:

- Whole sentences. Every one gets a subject and a verb.
- One idea per sentence. Split anything joined by a dash.
- Say the name of the thing, not "it".
- Say "for example" before an example.
- Do not compress a fact, its reason and its consequence into one clause.

This is the most-repeated instruction in the whole file. Treat a rigid reply
as a defect, the same as a bug.

---

### Write in plain words. This is a standing instruction, not a preference.

His words, 2026-08-17: *"explain to me step by step using simple words you
keep using big words. From now on make it easy to understand in layman terms
everything we talk about."*

**He had to say it again on 2026-09-02, and more strongly:** *"I strictly
need from now on literally everything you give me, doesnt matter 50 years
from now our chat, make the vocabulary extremely easy and simple to
understand. Explain things very simply and dont use verbose complicated
terms."*

**Read that as: EVERYTHING, ALWAYS, NO EXPIRY.** Not just the hard
explanations. Not just when he is stuck. Every sentence of every reply, for
as long as this project exists. Having to ask twice means the rule was
written down and then quietly ignored once the writing got technical — which
is exactly when it matters most.

The test is simple: **would a smart person who has never seen a line of code
understand this sentence the first time they read it?** If not, rewrite it.

**AND SHORT WORDS ARE ONLY HALF OF IT. THE SENTENCES HAVE TO READ WELL
TOO.** He said this on 2026-09-02, after a reply that used simple words and
was still hard to read: *"you are using simple words yes but the way you
write sentences is so rigid... i need readable sentence."*

He then rewrote the reply himself, and the differences are the rule:

| Rigid, what I wrote | Readable, what he wanted |
|---|---|
| "It will look at the ~990 places that currently just say UK" | "The tool will check the ~990 places that currently only say UK" |
| "Should take about 5 minutes." | "It should take about 5 minutes." |
| "Everything already looked up stays cached, so it won't redo the 36,000 regions from before." | "Anything the tool has already checked is saved, so it will not check the 36,000 regions again." |
| "Some subtitles just repeat the name." | "Some subtitles simply repeat the place name. For example, ..." |

So, concretely:

- **Write whole sentences.** Every sentence gets a subject and a verb.
  "Should take five minutes" is a fragment. "It should take about five
  minutes" is a sentence. Fragments read like notes, not like talking.
- **One idea per sentence.** If a sentence has a dash in the middle
  carrying a second thought, split it into two sentences.
- **Say "for example" out loud.** Do not just drop the example next to the
  claim and hope the link is obvious.
- **Name the thing doing the action.** "The tool will check" beats "it will
  look at". He should never have to work out what "it" refers to.
- **Stop bolding half the sentence.** Bold is for the one thing that
  matters most in a section, not for every clause.
- **Do not compress.** Cramming a fact, its reason, and its consequence
  into one clause is not efficient. It just moves the work onto him.

**KEEP THE BULLET POINTS. HE ASKED FOR THIS BACK ON 2026-09-02**, after a
reply that followed everything above and came out as five plain paragraphs:
*"i didnt mean stop formatting, i just meant the structure in sentences was
hard to read, so i need back the bullet points etc to make the wall of text
you are giving me more easy to read not just paragraphs."*

The complaint was never about structure. It was about the SENTENCES inside
the structure. So:

- **Use bullets, headings and short tables as much as before.** They are
  what makes a long reply scannable.
- **Write each bullet as a full sentence, or two.** A bullet is not an
  excuse for a fragment. "Drowned by a dam in 1982" becomes "The falls were
  drowned when the Itaipu dam was built in 1982."
- **A table is still right for real data**, such as a list of places and
  their view counts. It is wrong for an explanation, because a table forces
  him to reassemble the sentence in his head.

The two rules work together: keep the shape, fix the prose inside it.

He is not a coder. Every explanation is judged on whether HE can act on it,
not on whether it is technically complete. Concretely:

- **Say what happens, in order, with times or numbers.** When he asked how
  earnings could run on a busy Monday, the answer that worked was a clock:
  "9:50pm the worker is doing Photoshop · 10:00pm the boss stops handing out
  new work · 10:35pm it finishes what it's holding · 10:36pm earnings run".
  The answer that did NOT work was the same thing described as a mechanism.
- **Name the machines by what they do**, not by their role in the code. "The
  Linux server is the boss, the Windows machine is the worker" landed;
  "dispatcher" and "node" did not.
- **Ban the shorthand.** intake, idempotent, derived state, artefact,
  scoping, dispatch, payload. If a word only means something to someone who
  has read the code, it is the wrong word. There is always a plain one.
- **Give the cost in his terms** — minutes lost, designs delayed, money —
  rather than in properties of the design.
- **When he says he does not understand, that is a defect in the
  explanation.** Rewrite it from scratch in simpler words; do not repeat it
  with more detail bolted on. He asked twice before I stopped using jargon,
  and that was two wasted rounds.

- **Do not dress up a simple thing.** "The tool asks the website one
  question about 500 places at once" beats "a batched lookup resolves the
  dependency for the affected subset". Same fact, and only one of them can
  be read at speed.
- **A number he can picture beats a percentage.** "500 titles would be
  wrong" lands; "a 4% error rate" does not.

This applies to CHAT, not to code comments. Comments explain *why* to the
next engineer and stay technical.
