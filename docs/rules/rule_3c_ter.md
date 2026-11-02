<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 3c-ter. If something in this system ALREADY does it, reuse that path

The rule above says do not invent a value. This one is bigger: **do not
invent a mechanism either.** Before writing a new way to talk to anything —
a marketplace, an OS, a machine — find what already talks to it and ask why
that would not serve.

The incident, and it is the same evening as 3c-bis: the earnings reader was
written as a fresh `requests` session logging into FineArtAmerica. It got
HTTP 403 with "Verify Visitor — Are you human?". Meanwhile the uploader logs
into the SAME account successfully every single day, because it arrives in a
real browser on the node. The proven path was sitting right there. Three
deploy cycles were spent making a second, unproven path fail in new ways.

The questions, in order:

1. **What already does this successfully?** Name it. If the answer is "the
   uploader", the design is "do what the uploader does", not "do the same
   thing a different way".
2. **Why would that path not work here?** There must be a real reason —
   cost, a machine that is not running, a capability it lacks. "It felt
   heavier" is not a reason. A 150MB browser you ALREADY RUN costs nothing
   extra to reuse.
3. **What is the untested assumption?** Here it was "the pages are plain
   HTML so an HTTP client is enough", which was true of the PAGES and false
   of the LOGIN. State it, and test that specific thing first, before
   building everything downstream of it.

The general form, which links this to 3b and 3d: **a capability belongs to
whatever already has it.** 3b says a shared fact must be reported where it
is shared. 3d says a symptom belongs to the machine that does the work.
This one says a new feature must be built on the machine that can already
reach the thing — and for anything on the far side of a bot wall, a login
wall, or a browser check, that machine is the node.

**The nearest copy of the path you want is often in the file you are already
editing.** `_recall_targets` wrote its own project scoping and got it wrong,
while `_title_scope()` sat forty lines from the top of the same file and was
what every other endpoint in it used. Before writing a query, a scope or a
lookup, scroll to the top of the file you are in and read the helpers it
already has. That costs one screen and it is where this project's answers
usually are.

**And when TWO existing paths do the job, copy the one that has learned
more.** v239's USE MY OWN PICTURE copied the worker paste box's raw
download — it trusts the file name and measures size from the first 64 KB
— instead of the search grid's reader (`imagefetch`: checks the real
format from the bytes, lets Pillow read the whole file, converts WebP). So
a real JPEG with a large block of camera data, and an AVIF saved as ".jpg",
were both refused as "not a picture" with no reason given (owner,
2026-09-28). The same 64 KB gap had been quietly letting such pictures skip
the 350px floor at the worker paste doors, because "could not measure" is
let through; `read_file_dimensions` now falls back to Pillow, and
`check_pictures_have_a_measured_size` lists anything saved unmeasured.

**A corollary worth stating on its own: when the owner says "just copy how
X does it", that is not a shortcut, it is usually the correct architecture.**
He can see the working path from outside the code. If the answer is "that
would be awkward because…", the awkwardness is the thing to fix, not a
reason to build a second path.

**And when something ALREADY WORKS — even outside this repo — READ IT before
theorising about why ours does not.** He asked why the VPS meets Cloudflare
when his old laptop tool never had, and offered the tool. The answer was one
grep: it contains no login code at all. It opened a Chrome profile he had
signed into by hand and went straight to the work, while ours opened the
sign-in page on every single read. Everything else — datacentre address,
headless, timing — was secondary to the one behaviour we had and it did not.
A working precedent is EVIDENCE, and reading it costs a minute; reasoning
about the difference from the outside had already cost several deploys.

**Corollary, and it inverts an instinct: a retry can be the cause.** After a
failed read the account was queued again immediately — jobs #34 to #38 inside
ninety seconds — because the cooldown governed uploading only. Five sign-in
attempts a minute from one datacentre address is how a site that was merely
suspicious becomes a site that is certain. Before adding or permitting a
retry, ask whether the thing being retried is something the far side COUNTS.
Logins, searches and writes are counted; fetching a page you are entitled to
is not. Note also the fix here was NOT `account_is_available`, the obvious
reuse: it also refuses banned and disabled accounts, which is right for
uploading and backwards for reading money. Reuse the CONDITION that is
genuinely shared, not the function that happens to contain it.

