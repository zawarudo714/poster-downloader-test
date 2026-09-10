# Not yet deployed

## v177 — the reset was about to delete your signature

Found while writing the reset commands for the test box, BEFORE you ran
anything.

**`reset_workflow.py` deleted every folder under the workspace**, including
`_signature/travel.png` and `_style/travel.png` — the signature image and the
style reference you upload on the Settings page. Its own docstring claimed it
removed "only the per-user directories". It did not.

**Why that was worse than losing two files.** The SETTINGS that name those
files are not work, so a reset keeps them. You would have had a setting
pointing at a signature that no longer existed — and `_build_print_file`
REFUSES outright in that state. The pipeline would have stopped dead on the
first poster after the reset, blaming a missing file you never knowingly
deleted.

Fixed: folders whose name starts with an underscore are assets and are kept.
The script now PRINTS what it kept, so silence cannot read as "those went
too". A new preflight check enforces the convention that makes the skip
correct, so a future asset written outside it fails before deploy.

**Two wrong versions of that check first, and both are recorded in it.** The
first matched only a literal written directly after `WORKSPACE_DIR /`, and
the real code assigns the path to a variable one line earlier — so removing
the underscore left it green. The second widened too far and reported a
Storage Box path four times. The one that shipped follows the variable that
is joined to WORKSPACE_DIR back to its assignment. Sabotage-tested on both
the signature and the style reference.

**Deploy this before running any reset.** The Windows node did NOT change,
so nothing to copy and `AGENT_VERSION` stays where it is.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
