<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## THE DOCUMENTS, AND WHEN TO READ THEM

`preflight.py` fails if a `.md` in this repo is missing from this list —
because `AUDIT.md` sat unreferenced for ten days and a session redid most of
its work from scratch.

**Every session, before anything else:**

| File | Answers |
|---|---|
| `tools/PENDING_DEPLOY.md` | What is written but NOT on the server |
| `tools/DEPLOY_LOG.md` | What IS on the server, newest first |
| `TO_TEST.md` | What is LIVE but no person has ever clicked. **Asked for by the owner 2026-09-06, and kept separate on purpose.** Deployed and passing its checks is not the same as working; only he can judge a screen. An item is DELETED when he says he has done it, never ticked and left — see the file's own rules |

**Before writing any code:**

| File | Answers |
|---|---|
| `MULTIPROJECT.md` | The multi-project contract. Short, and nearly every bug here came from ignoring it |
| `AUDIT.md` | Every control on every screen, and whether it does anything in THIS project. **A living inventory — extend it, do not start a new one** |

**When the work touches these areas:**

| File | Answers |
|---|---|
| `ROADMAP.md` | Everything outstanding, in order, and why that order |
| `MEGA_AUDIT.md` | The seven-question audit of 2026-09-06 — what was asked, found, fixed, and NOT covered |
| `OPEN_ISSUES.md` | Individual defects not yet fixed, with what is known about each |
| `TITLE_RULES.md` | **Every way a title can be wrong**, which check covers it, and which gaps are deliberate. Read before touching titles, the importer, or the folder builder — a title is half of a primary key, not a label |
| `PIPELINE.md` | Post-production: processing, uploading, the node |
| `DEPLOY.md` | How to deploy |
| `CLAUDE_CODE.md` | How the owner runs a session in Claude Code on his own machine, and what to type. **The split is: build in Cowork, VERIFY there** — the Cowork sandbox has no network and no SQLAlchemy, so it can never run this app or query a real database |
| `SETUP_VPS.md` · `SETUP_WINDOWS_NODE.md` | Building a box from nothing |
| `README.md` | What the app is, for someone who has never seen it |
| `extensions/poster_admin_extension/README_INSTALL.md` | The admin's Chrome add-on ("Google Beside Me"). **This folder is the master copy** — the site zips it for download (Worker Images, ⬇ GOOGLE ADD-ON), so change the add-on here, not in the old loose copy beside the repo |
| `CHANGELOG.md` | Released changes, by version |

---

Then:

- **`tools/PENDING_DEPLOY.md` then `tools/DEPLOY_LOG.md` — what is WAITING,
  and what is LIVE. READ BOTH FIRST, EVERY SESSION, BEFORE ANYTHING ELSE.**
  Twenty-odd lines between them.

  They are two files on purpose. The deploy tool REWRITES `DEPLOY_LOG.md`
  from a header plus its own lines, so anything hand-written there is
  destroyed on the next deploy — which is what happened on 2026-08-27, an
  hour after a pending-work block was added to it. **Two programs cannot own
  one file.** `PENDING_DEPLOY.md` is only ever emptied by the tool, never
  rewritten, so a note in it survives.

  Read that file rather than running `git log`, `git diff` or `git status`:
  those cost far more and answer a different question (what is committed,
  not what shipped).

  **AND RE-READ IT. A deploy state read at the START of a session is stale
  by the middle of it.** It happened again on 2026-09-12: three deploys
  landed DURING one conversation, two were caught by re-reading, and the
  third was missed in the very reply that planned the production move —
  because that reply was written from the conversation's memory. Treat
  every pause in the conversation as having contained a deploy; he ships
  the moment a thing is ready. On 2026-09-03 I told him v144 was not
  deployed and that a test would fail because the file was not on the
  server. It had been live for some time. I had read the log hours earlier, then reasoned from
  the SCROLLBACK of his terminal — which showed an older session — instead
  of opening the file that answers the question. He had to send a screenshot
  of the deploy tool reading `live 144` to correct me.

  The general shape, and it is rule 3b wearing a new hat: **his terminal
  output, his screenshots and anything said earlier in the conversation are
  all a record of a MOMENT, not of the present.** Before making any claim
  about what is deployed, running, or present on a machine, open
  `DEPLOY_LOG.md` again. It costs one read. Being wrong about it sends him
  to run commands that cannot work. On a mounted working copy `git status` cannot even
  refresh its index — it reports stale answers with no warning, and on
  2026-08-27 it showed two edited files as unmodified.

  **The two standing rules, both added 2026-08-27 at the owner's request
  after a version collision:**

  * **Say at the START of the session if anything is undeployed**, before
    he asks. He cannot tell by looking at the site, and anything built on
    top of an undeployed change is work he tests twice.
  * **EVERY time you finish a change, say TWO things: deploy, and whether
    the NODE also needs copying.** Added 2026-08-27 at his request, after
    v133 changed `worker_service/` and he only copied it because it came up
    in conversation. The deploy tool touches the SERVER only — the Windows
    box is updated by copying a folder, which has no receipt, so if nobody
    says it the node silently keeps running old code. If `worker_service/`
    changed at all, bump `AGENT_VERSION` and say so in the same breath as
    "deploy this".
  * **When he deploys, SAY WHETHER IT WORKED** — "v124 is live" or "v124
    failed, here is what it said" — then move the entry out of the waiting
    block and into the log. Never leave it ambiguous.

  **Bump `APP_VERSION` to one MORE than the top log entry, not one more
  than whatever the file happens to say.** On 2026-08-27 the file read 122
  while v123 was already live, so the bump produced a second v123 — and then
  two different commits both shipped as v124. A version number that means
  two different things is worse than none. The log line now carries the
  version as its own field and the deploy tool warns when one is about to be
  reused, so this is caught by the tool rather than remembered.

  **And never fold more work into a version you have already told him to
  deploy.** On 2026-09-27 v234 was announced ready, then the Google-tab
  change was merged into it after one read of the log — and he deployed in
  the minute between, so v234 shipped twice (12:42 and 12:43). Both happened
  to carry the same code, but only a file-by-file comparison could prove it.
  Once "deploy this" has been said, the next change takes the next number.

- **`MULTIPROJECT.md` — read it before writing ANY code.** This app runs
  several niches side by side. Nearly every bug it has had came from code
  assuming there was only one. That file is the contract: how to say the
  right words, how to scope a query, which stage claims what, and the traps
  already hit in production.
- `PIPELINE.md` if the work touches post-production.

**The single most important thing to understand:** this is a MULTI-PROJECT
system that currently runs ONE project. Two earlier niches were deleted on
2026-09-01 (see WHERE THIS STANDS), and more are planned.

**One project is the dangerous number, not two.** With two, a wrong
assumption shows up immediately. With one, code that quietly assumes "the
project" is always this project looks perfectly correct until the day it
isn't — which is exactly how the first version of this system got into
trouble. Every scoping helper, every `project_id` filter and every
`CAPABILITIES` row still earns its place.

When the owner asks for a new feature or a new niche, it must not inherit
mechanisms that do nothing for it, and it must not inherit another project's
vocabulary either — the `{artist}` placeholder sitting in shared search code
for a project about mountains is the specimen. Ask which pieces would be dead
for it and say so explicitly, rather than copying an existing definition.

**AND A DELETED PROJECT'S VOCABULARY OUTLIVES ITS CODE, because a field NAME
costs nothing to leave behind.** Long after the movie project was deleted,
the worker payload still carried a key called `tmdb_search`, the outside-link
button was `.att-tmdb`, a helper built film-database search URLs that nothing
could reach, and the "this image is too small" warning told travel workers to
go and click a poster thumbnail on a site this system no longer touches. None
of it broke anything, which is why it survived — and it is precisely what
makes the next session reason as though a film database were part of the
system. Cleared out on 2026-09-09 at the owner's instruction ("remove all
tmdb movie/poster traces, go through the whole code").

So when a project or a marketplace is removed, **grep for its NAME, not just
its code**, and rename the fields, classes and payload keys it left behind.
Same rule as deleting a capability rather than disabling it (5b), applied to
words.

**And grep for the deleted FILE's name too, because an import inside a
function is only resolved when it runs.** Deleting `routes/store_admin.py`
with TeePublic (v147, 2026-09-06) left two jobs in the scheduler importing
it. Each sat in a broad `except Exception`, so a missing file became a
traceback in the server log every minute for seven weeks, instead of a
failure anyone saw; it was found only by reading that log while checking
v266. The untested assumption was "if a file were missing, startup would
fail". Now mechanical: `check_imports_resolve` in `preflight.py` fails any
import of our own code that names a missing file or a missing name inside
one, wherever the import sits. Sabotage-tested with the old lines and with
a misspelt name.

**AND THE WORST PLACE IT HIDES IS A FALLBACK, WHERE NOTHING IS WRONG UNTIL
THE DAY IT IS.** That grep was run on 2026-09-09 and called done. On
2026-09-10 the owner opened a delete dialog reading *"Only 0 usable posters
available"* — a word this project does not use, about a count that cannot
mean anything when a title takes ONE image. Behind it were **thirty-one
sentences** across the admin screens still saying "poster", and worse, the
dead word was the FALLBACK in eleven places: the database column default,
the template layer, the project context and six templates, all shaped
`item_noun || 'poster'`.

Three things generalise:

  * **A fallback is not a harmless default — it is what the whole site says
    the day a value fails to arrive.** Every one of those would have spoken
    the deleted niche's vocabulary to a travel worker, with nothing on any
    screen to explain why.
  * **Pick the fallback that is true of EVERY project**, not the one that
    was true of the first. It is "image" now: a film poster is an image and
    a photograph of Kyoto is an image.
  * **A word can change owner, so re-read the dead list.** "artist" was
    MUSIK's word — and FineArtAmerica calls the account's display name the
    artist name, which this system stores and shows. It stopped being dead.
    A check that does not know that spends its time crying about the Listing
    check screen.

Now mechanical: `check_noun_fallbacks_agree` in `preflight.py` fails on any
dead niche's word inside a SENTENCE, and on the four places that state the
fallback disagreeing. It ignores CSS class names, element ids and anything
inside `${...}` — the first two versions did not, reported forty false
alarms, and a check that shouts about class names is one nobody reads.

**And the check itself had a blind spot that survived the whole v177
sweep:** a quote mark INSIDE a `${...}` interpolation — the everyday
plural `poster${n === 1 ? '' : 's'}` — stopped the string-finder before it
ever saw the sentence, so seven "poster" sentences sat on live screens
while the check read green (found 2026-09-10, while touching one of them
for an unrelated reason). The check now strips interpolations from the
LINE before hunting for strings, with a non-space stand-in so a URL does
not turn into a false sentence. The general shape: **a scanner that skips
region X must ask what region X can HIDE — a skipped span that can contain
the scanner's own delimiters blinds it to everything around the span.**
Known residual: a LITERAL quote in a template string's own text still
hides that one string, accepted because handling it needs a real JS lexer,
which is the wrong check (see 3c).

---

