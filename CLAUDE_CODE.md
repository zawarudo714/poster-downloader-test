# Running the bug hunt in Claude Code

This file is for the OWNER. It tells him how to set Claude Code up on his own
machine and what to type, so the bug hunt runs where the code can actually be
executed.

It also tells a future AI session why this file exists, which is the second
half of the point.

---

## WHY THIS IS WORTH DOING AT ALL

Cowork (the desktop chat) and Claude Code are the same model. The thinking is
the same. What differs is what the machine around the model can reach.

The Cowork sandbox has **no network and no SQLAlchemy installed**. That means
a Cowork session cannot start this app, cannot run a real database query, and
cannot open the live site. Every "I checked this by reading the code rather
than by running it" line in the delivery notes comes from that one limit.

Claude Code runs on the owner's own Windows machine, or over SSH on the
server. From there it can:

* run `python tools/preflight.py` in a real environment;
* run the **Diagnostics** checks against the real database, which is the one
  rung that catches a fault nobody imagined;
* start the app and load a page;
* reproduce a bug instead of reasoning about it from the outside.

So the split is: **build here, verify there.** All the written memory —
`CLAUDE.md`, `tools/PENDING_DEPLOY.md`, `TO_TEST.md`, `MEGA_AUDIT.md` — lives
in this folder, and Claude Code reads the same files.

---

## THE UNCOMFORTABLE PART, SAID FIRST

**Do not type "fix all the bugs".** That instruction has no edge to it. The
model will start changing things it merely finds suspicious, across dozens of
files, and the owner is not a coder — he cannot review a hundred edits, so he
would end up deploying work nobody checked. The rule in `CLAUDE.md` about
inventing a finding out of a pattern exists because that failure has already
happened here three times in one day.

**Ask it to FIND and REPORT first. Fix in a second, separate pass, one item
at a time.** A list the owner can read is worth more than a pile of edits he
cannot.

---

## STEP 1 — INSTALL IT

Open PowerShell on the Windows machine and paste this whole line:

    curl -fsSL https://claude.ai/install.cmd -o install.cmd && install.cmd && del install.cmd

Then check it landed:

    claude doctor

If that command is not recognised, close PowerShell and open it again, so it
picks up the new program.

The other way to install it is through npm, if Node is already on the machine:

    npm install -g @anthropic-ai/claude-code

Do not put `sudo` or `Run as administrator` in front of the npm one. The
official notes warn against it.

---

## STEP 2 — OPEN THE PROJECT

Claude Code works on whatever folder it is standing in, so the `cd` matters
as much as it does over SSH:

    cd "C:\Users\Administrator\Documents\Claude\Projects\Print On Demand\poster_downloader_web"
    claude

The first run asks the owner to sign in with the same account he uses here.

Once it starts, it reads `CLAUDE.md` on its own. There is nothing to paste in
and nothing to explain — every standing rule, every measured fact about
FineArtAmerica, and every past bug is already in that file.

---

## STEP 3 — THE FIRST THING TO TYPE

Paste this, exactly:

    Read CLAUDE.md, tools/PENDING_DEPLOY.md, tools/DEPLOY_LOG.md and TO_TEST.md
    before anything else.

    Then set the project up so you can RUN it, not just read it:
      1. Make a virtual environment and install requirements.txt.
      2. Run python tools/preflight.py and show me what it says.
      3. Run python scripts/dev_setup.py --cli to build a local test database.
      4. Start the app and confirm the admin page loads.

    Report what worked and what did not. Do not change any code yet.

That is deliberately a setup job rather than a bug hunt. Until those four
steps pass, nothing Claude Code says about behaviour is worth more than what
a Cowork session can already say, because it still would not be running
anything.

---

## STEP 4 — THE BUG HUNT ITSELF

Only after step 3 works, paste this:

    Run the Diagnostics checks in app/diagnostics.py against the local test
    database and show me every finding.

    Then walk the seven questions of the MEGA AUDIT in ROADMAP.md stage 5,
    one at a time. For each finding, follow the rule in CLAUDE.md: nothing is
    a finding until you have tried to kill it. Give me a reproduction, a
    database row, or the code path traced from producer to consumer. If you
    have none of those, call it a question rather than a bug.

    Write everything into OPEN_ISSUES.md. Change no other file.

Then read the list, pick the ones that matter, and ask for them one at a
time.

---

## WHAT TO WATCH FOR, BECAUSE IT WILL HAPPEN

* **Claude Code can deploy.** It has a terminal, so it can SSH to the server
  and push. That is useful and it is also how a half-finished change reaches
  the live site. Tell it plainly at the start: *do not deploy, I will deploy
  myself.*
* **It cannot see the marketplace.** FineArtAmerica challenges the server and
  will challenge it too. Every measured fact about FAA is already written
  down in `CLAUDE.md`, so it must read rather than re-derive.
* **Two sessions must not edit at once.** If Claude Code is working in this
  folder, do not also ask Cowork to change files in it. Two writers, one
  file, is the same trouble as two programs owning `DEPLOY_LOG.md`.
* **`APP_VERSION` and the deploy notes are the handover.** Whichever session
  makes a change writes `tools/PENDING_DEPLOY.md`, so the other one knows.

---

Sources for the install commands, both read on 2026-09-09:
[Set up Claude Code](https://docs.claude.com/en/docs/claude-code/setup) ·
[Troubleshoot installation](https://support.claude.com/en/articles/14552646-troubleshoot-claude-code-installation-and-authentication)
