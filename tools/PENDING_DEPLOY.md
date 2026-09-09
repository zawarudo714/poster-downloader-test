# Not yet deployed

**v168 — the signature UPLOAD button, a second bug the same shape, and the
check that finds both.**

**No schema change. No node copy** — `worker_service/` is untouched and
`AGENT_VERSION` stays at 1.31.0.

## What was broken

`admin_pipeline.js` holds two separate wrappers. The big one declares a
shortcut called `q`; the small GPT panel declares one called `$`. I wrote
the signature UPLOAD handler in the second wrapper and called `q(...)`,
which does not exist there. The file parses, the handler exists, and the
error lands inside an `async` click handler — where it becomes a rejected
promise nobody is waiting on. So the button did nothing at all and said
nothing at all.

## The second one, which the new check found

`toast` was declared inside `admin_pipeline.js`'s wrapper while
`admin_review_images.js` called it too. Those two scripts never load on the
same page, so on Approve Artwork the name did not exist:

- the eyedropper's "Click a colour in the poster" message threw every time,
  so the eyedropper armed silently and never told you
- worse, the one line that reports a pixel it could not read was itself
  failing

`toast` now lives in `static/js/toast.js`, on `window`, loaded by
`base.html` on every page. One definition, reachable from everywhere.

## The check

`check_js_helpers_are_in_scope` asks one narrow question: **is this name
somebody's private helper, being called from outside?** A name only counts
if it is DECLARED inside some wrapper, so no list of browser globals is
needed and no CDN library can trip it.

The first version tried to be a scope analyser and had to be thrown away —
it needed a real JavaScript lexer, my hand-rolled string-blanker silently
ate real code, and it reported `jobTone` as undefined while
`function jobTone` sat forty lines below.

Getting the narrow version right still took four holes, every one at the
edge of the pattern I had written:

- `async function` declarations were not recognised
- `//` comments were not stripped, so "every 4th tick (~12s)" read as a call
- declarations nested deeper than two spaces were not seen
- names the browser also provides — `open`, `load`, `close` — collided

Sabotage-tested both ways: putting the `q` bug back fails the deploy, and
so does making `toast` private again.

Whoever changes code writes here what is waiting and why; the deploy tool
empties this file once the server is confirmed to be running it.
