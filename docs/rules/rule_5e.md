<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

### 5e. FOR EVERY BUG, ASK WHAT WOULD HAVE CAUGHT IT WITHOUT HIM

**Standing instruction from the owner, 2026-08-24, and it applies to EVERY
issue — one he reports, or one you trip over yourself.** His words: *"always
ask yourself if I hadn't mentioned it, would it be detected if I decide to
run a defect check anytime. This needs to happen every time there is an
issue. Update the evolving notes to cast the net wider."*

**The point is not the bug. It is that HE is currently the detector.**
Almost every defect in this project was found because he watched a number,
read a log, or was about to depend on something. That does not scale, it
does not run at 3am, and it costs him an evening every time. The goal is
that a defect check he runs on a quiet Tuesday finds the things he would
otherwise have had to notice first.

So for every bug, answer these three IN THE DELIVERY, in plain words:

1. **What actually found it?** Name it honestly — "you did, by noticing the
   number going up" is the usual answer and pretending otherwise helps
   nobody.
2. **If you had said nothing, what would have found it, and when?** Walk the
   rungs: preflight, `--map`, the behaviour tests, a Diagnostics invariant.
   If the answer is "nothing, ever", say exactly that.
3. **What is being added right now so the answer to 2 changes?** Not a rule
   to remember — a mechanical check. Then say which rung it landed on.

**Prefer the EARLIEST rung that can catch it**, because they differ in when
they fire and what they cost:

| rung | fires | catches |
|---|---|---|
| `preflight` | before deploy | anything disconnected, in seconds |
| `preflight --map` | when you read it | the odd one out among peers |
| behaviour tests | before deploy | a decision computed wrongly |
| Diagnostics invariant | unattended, for ever | it happening for real |

A Diagnostics check is the safety net, not the goal: it fires after the
listings are already switched off. If the defect is visible in the SOURCE,
it belongs in preflight or the map instead.

**The worked example, because it is the reason this rule exists.** The
AUTOMATIC tickbox was read by three buttons and dropped by a fourth. What
found it: he did, planning to leave it running overnight. What would have
found it otherwise: nothing — the checkbox existed, the hook matched, the
button had a handler, the endpoint was called, and `check_designs_left_
switched_off` counts `confirming` as "a run is working on it". What was
added: `--map` now prints the request body beside each endpoint, so a
handler that drops a shared setting is visible next to its peers; plus an
invariant that an automatic run must never sit at a gate.

**When the honest answer is "nothing would have caught this and nothing
mechanical can", say so rather than inventing a check that cannot fail.**
A green light from a check that was never looking is the failure this whole
section exists to prevent — see the sabotage rule in 3c.

**Applying this rule BACKWARDS pays immediately.** Running it over the other
two defects from the same day produced two new preflight checks — a query
inside a comprehension, and `Number(x) || default` swallowing a real zero.
Both were written badly first: the query check scored ZERO against its own
motivating bug (it knew `db.query` but not a helper that queries) and warned
86 times; the zero check flagged thirteen lines that were all `|| 0`, where
the fallback IS zero. Sabotage found both. **Always break the new check with
the original bug and confirm it goes red** — a check derived from a defect
that cannot detect that defect is the purest form of false comfort.

Fixed, the zero check then found a defect NOBODY had reported: `daily_limit`
0 became 100 in both the account form and `account_quota`. Setting an
account's daily limit to zero is the obvious way to say "do not upload here
today", and it did the opposite, on a real marketplace, silently. That is
the whole point of this rule — a check found it instead of the owner.
**But the check only looked at the JS, and the fix only fixed the JS:** the
server's save still did `int(x or 100)`, so the owner's 0 was stored as 100
anyway (v281). A value that crosses a layer is fixed on EVERY layer it
crosses, and the check covers every layer too (preflight "the server keeps
a chosen zero", list `ZERO_IS_A_CHOICE`; `tools/test_daily_limit_zero.py`).
The 2026-10-29 audit found four more (`min_image_px` 0 became 350, the
node's `daily_start_hour` 0 became 6, `rotation_order` 0 became 100, the
listing check's `gap_ms` 0 became 300) and Diagnostics calling a daily
limit of 0 a fault; v287 fixed them and the list now covers
`worker_service/` too.

