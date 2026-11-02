<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 7. Every bug becomes a rule — this file is the memory

**Standing instruction from the owner: whenever a bug is found — whether he
reports it or you trip over it yourself — you update this file before you
finish.** Not a note about that one bug: the GENERAL shape of it, written so
that the next session catches the next instance of that shape while building,
instead of after a deploy.

A future session starts with no memory of this one. The only thing that
carries forward is what is written down, so a fix that leaves no rule behind
guarantees the same class of defect comes back.

Write the rule at the right altitude. Too specific and it never fires again:
*"check the marketplace account modal"* is worthless. Too vague and it is
noise: *"be careful"* is worthless too. The test is whether the rule, applied
blind to a DIFFERENT part of the system, would have caught this. Each rule
above earned its place that way — 3c came from a deleted panel breaking an
unrelated button, 3d from reading upload code when a whole machine was the
problem.

**The rule goes in the section it belongs to, and it names the real incident
in one line.** The incident is what makes it believable enough to obey. A
rule with no story behind it reads like generic advice and gets skipped.

**REVISE, do not append. This is the iron rule.** Before writing anything
down, search these files for what is already said about it. Then:

  * The topic already has a rule → **edit that rule.** A bug with a new
    wrinkle adds a sentence to the existing entry; it does not get a second
    entry of its own.
  * The instruction has CHANGED → **rewrite the old text, do not stack a
    contradiction next to it.** If he ever asks for more technical language,
    the fix is to find "Write in plain words" and change it — not to add a
    second, opposite instruction and leave the next session to guess which
    one wins.
  * Something written earlier was later fixed, or turned out wrong → **go
    back and correct it.** The "make project_id nullable rather than
    inventing a parallel table" note was confidently wrong for two weeks. A
    stale note is worse than no note, because it is trusted.
  * Genuinely new and unrelated → then, and only then, a new entry.

The test is length. This file should get SHARPER as it grows, not longer.
If an edit makes it longer without making it more useful, it is the wrong
edit. Not everything is worth writing down — the bar is "would this have
changed what a future session did".

**Every bug also gets the 5e treatment**, which is the other half of this
rule: a rule tells the NEXT session what to look for, and a mechanical check
looks whether or not anybody remembers. Write both, and say plainly when
only one of them was possible.

**Three things every new rule must do**, because the owner has asked for a
net rather than a list of anecdotes:

1. **Generalise past its own domain.** Write it so it fires on a DIFFERENT
   subsystem. 3c came from HTML and its real target is any structural edit;
   3c-ter came from a marketplace login and its real target is any new path
   to anything already reachable.
2. **Link to the rules it neighbours**, and add the row to the table at the
   top of this section. Defects arrive in families — the same evening
   produced 3c-bis (invented a URL) and 3c-ter (invented a whole mechanism),
   and either rule alone would have let the other happen.
3. **Say what the untested assumption was.** Nearly every entry here is a
   case of one unexamined belief — "the panel ends here", "the form field is
   called that", "plain HTTP is enough" — carrying a large build on top of
   it. Naming the assumption is what lets the next session test it FIRST,
   in five minutes, instead of last, after three deploys.

