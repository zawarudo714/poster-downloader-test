<!-- Moved out of CLAUDE.md on 2026-11-02 (TOKEN-SAVER tidy), text unchanged. -->

## The one thing to understand

**This project will keep growing in scope. Do not make choices that are
painful to retrofit.**

Stated plans, in order. `DECIDED` by the owner — intentions, not
commitments with dates.

1. **Get Travel Locations earning.** The only active work. Needs the master
   sheet, the Brave query, the FAA keywords and the GPT prompt — all four are
   the owner's to state, and none can be guessed.
2. **More marketplaces.** A new one needs a reader module, an entry in
   `service.READERS` and a `CAPABILITIES` row — nothing else. (TeePublic is
   NOT a candidate any more: the owner runs everything TeePublic from a tool
   on his own laptop — see the 2026-09-06 entry below.)
3. **A second travel-adjacent project** if the sheet justifies splitting
   parks from cities. Deliberately one project for now.

**Built and still live. Each carries its own provenance — read the tag
before acting on it:**

4. **Marketplace reconciliation. BUILT 2026-08-24** — `app/listing_check.py`,
   `routes/listing_admin.py`, the Listing check tab, and the node's
   `listing_check` job. Manual only, no schedule, by instruction.

   Deliberately simple, for measured reasons in the FineArtAmerica
   section below: a listing is live or deleted with no
   hidden state, a missing one returns a real 404, and HEAD is honoured. So
   there are no stages, no gates, no cure, and NO PIPELINE HOLD.

   What it DOES borrow, because those lessons were general: one chunk at a
   time, the reply to each report carries the stop signal, and "is it
   finished" is derived from the rows rather than counted.

   Still to build: the reverse direction — listings on the site that are not
   in the database. Needs the shop's own pages paged and parsed, which is a
   different mechanism and a rarer question.

5. **Earnings tab (master level).** NOT a pipeline.
   **BUILT for FineArtAmerica 2026-08-20.** It reads FineArtAmerica only:
   the TeePublic reader was REMOVED 2026-09-06 with the rest of that
   marketplace (see the entry below). The design stays multi-marketplace —
   READERS and CAPABILITIES are still the way a new site is added.

   The rules that held, and are the reason it works:

     * An ACCOUNT EXISTS ONCE, enforced by the `account_projects` link table
       rather than a nullable column, because one account serves several
       projects and a single column cannot say that. `project_id` survives as
       a dead legacy column.
     * Reading does NOT happen on the server. FAA challenges it as a bot, so
       it is a node job using the account's own Chrome profile.
     * ABSOLUTE rows, never stored deltas. Every figure on screen is
       arithmetic over rows computed at read time.
     * Figures revise DOWNWARD and a drop is not a bug — said on the page so
       the owner is not left wondering.
     * `Current Balance` is authoritative. Anything derived is an estimate
       and must be worded as one.

6. **Ban recovery / account handover. DROPPED `2026-09-03`,** at the owner's
   instruction. `ban_account()` and `hand_over_account()` still exist and
   still work; they are simply not on the list any more.

   His reasoning, and it follows from the whole point of this niche: the
   accounts were closed over CONTENT, and nobody owns Kyoto. If a closure
   ever happens anyway, the catalogue and the upload records are enough to
   rebuild against a new account at the time.

   What the real closure exposed is therefore also dropped rather than
   fixed: the design assumes ONE account is banned and its catalogue moves
   to a surviving sibling, and both accounts were closed at the same moment
   by one person for one reason.

7. **THE INTERACTION AUDIT — now part of THE MEGA AUDIT.** `DECIDED 2026-08-24` — not started. Not a code
   review and not a test suite: a deliberate trace of every action against
   every OTHER thing it can touch, written out as a tree.

   **The class of bug it exists to catch** is the one this project keeps
   producing: two or three mechanisms that are each individually correct,
   combining into something wrong. Nothing is broken in isolation, so nothing
   finds it except asking "what else is true while this happens".

   The specimen, 2026-08-23: PAUSE worked. The node's stop-reporting worked.
   RESUME worked. But a pause ended the scan JOB cleanly, the server read a
   clean job end as a finished SCAN, and resume therefore dispatched a mass
   deactivation off a sweep covering 199 designs of 1,543. Three correct
   parts, one wrong outcome.

   **How to do it, because "be thorough" is worthless:**

   * Enumerate ACTIONS, not screens — every button, scheduled trigger, node
     job, stage transition.
   * For each: what else is RUNNING while this happens; what does this leave
     half-done if it stops midway; what does the far side (a marketplace, the
     node, the clock) do that we have not seen.
   * Pay special attention where TWO mechanisms claim the same resource — the
     node, an account, a browser profile, the pipeline hold. Every serious
     bug so far has been at one of those seams.
   * Write the tree down as a file. It is the artefact, not the fixes.

   **His own example, not yet handled:** what happens to the earnings read
   when FineArtAmerica serves a MAINTENANCE page? The parser would fail to
   find the figures and report "FAA has changed its page" — sending the next
   session hunting a redesign that never happened.

8. **THE RETROFIT AUDIT — now part of THE MEGA AUDIT — build the extension points before the next thing
   needs them.** `DECIDED 2026-08-25` — not started. His words: *"we should
   revamp everything for as little retrospect headache as possible."*

   The pattern is the same every time: **a field that describes one thing
   gets used for two, and a mechanism built for one marketplace becomes the
   template for the next.** Every one of these cost a session:

     * `UploadAccount.project_id` — one column could not say "this account
       serves two niches". Became the `account_projects` link table.
     * `paused_until` — one field for an account that cannot upload AND one
       that cannot be read for money. Two unrelated failures, each silencing
       the other. Became `earnings_paused_until` beside it.
     * `action_error` — one field for "could not switch off" and "could not
       switch back on", which are opposites. While it was one field, a live
       listing sat hidden.
     * Marketplace behaviour as if-statements — became `CAPABILITIES` rows
       only after TeePublic's habits were applied to FineArtAmerica, where
       they were exactly wrong.
     * A stage counter — became work derived from the catalogue only after
       one account finishing ended the stage for five.

   So the audit is: **for every field and every mechanism, ask what the
   SECOND user of it will need, and whether it can express that today.**

     * Every column naming a single owner (`project_id`, `account_id`,
       `target_site`) — can the thing it points at ever be plural?
     * Every status field — can the thing it describes fail in more than one
       way independently?
     * Every mechanism that exists once — the listing sweep, the store sweep
       and the earnings read each grew their own chunked-job machinery. Which
       parts are genuinely one shared mechanism?

   **The concrete test is now travel's own growth**, plus TeePublic as an
   uploading project and Redbubble as a third marketplace. For each, say
   which existing pieces would be DEAD — BEFORE building.

8b. **THE MEGA AUDIT.** `DECIDED 2026-09-03` — the owner's name for it.
   Items 7 and 8 above, plus five more questions, done as ONE pass, AFTER
   the UI revamp and as the last thing before he starts testing. The seven
   questions and the method are written out in `ROADMAP.md` stage 5; do not
   restate them here.

   Two things about it that belong in this file rather than that one:

   * **It is one pass because seven separate ones never started.** Each
     alone reads like a week nobody has. Together they are one walk over the
     same code asking seven questions, and the answers overlap — the field
     that cannot express a second user is usually the same field whose two
     failure modes share one column.
   * **After the UI, not before.** An audit of a structure that is still
     moving has to be redone, and the UI stage is explicitly the one that
     moves templates around.

9. **SPEED. `MEASURED 2026-08-27` — IT WAS THE NETWORK, NOT THE DATABASE.**
   Kept because it is the worked example of why the provenance tags exist.

       login page built by the app          8.7 ms
       dashboard group-by over 201,133 rows 0.16 s
       server pulling from Hetzner's mirror  130 MB/s
       laptop pulling from THIS server       12.6 KB/s
       laptop pulling from the OLD server    16.3 KB/s

   Two servers in two datacentres, equally slow to the same laptop within a
   minute, while the box itself pulled at 130 MB/s. It was the link to Kenya,
   and it recovered on its own.

   **This entry previously blamed the row count, confidently, having timed
   nothing — and cost an evening.** A lead and a finding must not sit in the
   same list in the same voice.

   Done since and worth keeping whatever the link does: the
   `(project_id, status)` index, and gzip, which cuts HTML/CSS/JS by 70-80%
   and costs the server nothing.

   The invariant: **a page that scans a whole table is a defect once the
   table is real.**

Concretely, this means:

- Never hardcode a niche, a source site, a marketplace, or a marketplace's DOM.
- Anything the owner might want to change goes in the **dashboard**, backed by
  `app_settings`. Not a constant, not a config file on a server, not a code
  edit. This is an explicit requirement he has repeated.
- New capability should be **new rows, not new columns**. Pipeline tables
  already carry `project_id` and `target_site` for exactly this reason.
- Prefer a generic mechanism with one caller today over a specific one you'd
  have to unpick later.

---

