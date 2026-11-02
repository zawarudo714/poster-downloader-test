<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 9. A dependency is not deployed by writing it down

The node is updated by COPYING A FOLDER, and copying a folder installs
nothing. `beautifulsoup4` sat in `requirements.txt` while being absent from
the box, and the only code using it had a try/except fallback — sensible
there, fatal to noticing. It surfaced hours after a deploy as a job dying
once per poll cycle with `No module named 'bs4'`.

So, whenever you add an import the node needs:

1. Add it to `worker_service/requirements.txt` **under its PIP name**, which
   is not always its import name. `bs4` is a stub package that merely depends
   on `beautifulsoup4`; installing the stub has left real machines without
   the library.
2. Add it to `REQUIRED_MODULES` in `agent.py`, so the agent refuses to start
   and prints the exact command. A check at startup is loud; a failure at
   first use is a job that fails forever in a log nobody is reading.
3. Say in the deploy note that the node needs `pip install -r`.

The general form: **for any machine updated by copying files, a new
dependency needs a startup check, because there is no install step to hook
into.** Same reasoning as `preflight()` in `scripts/dev_setup.py`, which
exists because adding `cryptography` to requirements after a venv already
existed left setup dying mid-run with a wiped database.

**AND A FOLDER COPY HAS NO CONFIRMATION, so bump `AGENT_VERSION` whenever
anything in `worker_service/` changes.** That number, reported on every
handshake and shown on the Nodes tab, is the ONLY thing that can tell the
owner from the site whether his copy actually happened. It sat at 1.24.0
through two releases that both changed that folder, which made it worse than
having no number at all: it read "up to date" either way, for a machine that
might have been running week-old code.

Now watched by `check_worker_agent_current`, which compares what the machine
reports against what `worker_service/agent.py` says — READ from that file,
never copied into a constant on the server, because a second copy of the
version is the same defect one level up.

---

