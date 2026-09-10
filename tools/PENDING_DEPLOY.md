# Not yet deployed

## v176 — the six things found on the reset test box

All six came out of one afternoon on `178.105.232.196`. Five were mine.

### 1 · THE PAINTED POSTER WOULD NOT LOAD — and approving would have failed too

**The one that was blocking you.** Your Storage Box details are saved as a
project setting, at `pipeline.travel.storage_sftp_host`. Writing a painted
picture asked for that setting AND named the project, so it found them and
the files landed on `S:` perfectly. Reading it back did NOT name the project,
found nothing, decided there was no Storage Box at all, and looked in a local
folder that has never held anything — so the pane stayed empty.

The same unnamed read sits inside APPROVE, which has to fetch the transparent
original to flatten the print file. **Approving that poster would have failed
the same way**, with a worker's whole day behind it. Deleting files had it
too, so "send back to the start" was removing nothing from the box.

Fixed by making `project` a REQUIRED argument on every storage function, so
forgetting it is now an error at the call site rather than a wrong answer.
Five call sites corrected. A new preflight check fails the deploy on any
future call that omits it — sabotage-tested.

### 2 · "(null)" beside the title

Seven screens drew the year with nothing checking whether there is one. A
travel place has no year, so JavaScript printed `null`, Jinja printed `None`,
and one panel printed empty brackets. My v174 sweep fixed the two you had
complained about and I wrongly called it complete.

All seven fixed. A new preflight check now finds every line that draws a year
and fails if it is unguarded — sabotage-tested in both languages.

### 3 · WHAT SOLD ignored the start date

You set the date and the old albums stayed. The unmatched queue honoured it;
that table did not. It does now. The money totals still include the old sales
on purpose, because gross has to keep landing on FineArtAmerica's own Current
Balance — that agreement is the only proof no rows were missed.

### 4 · Every rapid click written to the Activity Log

The log was telling the truth: four presses sent four requests. Fixed in both
places. The browser now collapses a burst of presses into one request, and —
the half that matters, because a browser guard can always be got round — the
server writes no line when nothing actually changed.

### 5 · "Waiting on you 2" with one artwork

The number added up six kinds of work; the panel below drew cards for four.
Your second one was the skipped title, counted and invisible. Skipped and
Greenlight now have cards. A new preflight check compares the two lists.

### 6 · TITLE HEALTH — the master list you asked for

`TITLE_RULES.md` is new: every way a title can be wrong, which check covers
it, and which gaps are deliberate. Five Diagnostics checks back it —
marketplace collisions including truncation at 100 characters, titles the
marketplace would refuse, titles that would share one Windows folder, titles
carrying invisible characters, and title numbering. Each was exercised
against the shipped folding code.

---

**Deploy this.** The Windows node did NOT change, so nothing to copy and
`AGENT_VERSION` stays where it is.

**After deploying, in this order:**

1. Open the artwork awaiting approval. **The painted poster should now
   appear.** If it does not, tell me before doing anything else.
2. Run **Diagnostics** and read the five new title checks against your real
   88,970 rows. Expect findings — the 20 duplicate names are only the first
   question of five.
3. Fix what they list in `IMPORT_titles.csv` and re-import. Doing it now is
   free; doing it after a listing exists does not give the name back.

---

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
