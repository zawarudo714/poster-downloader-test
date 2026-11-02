# Project brief for AI sessions

Rules and pointers only. The detail lives in `docs/` and the other files
listed below — read only the section a task needs (search first, then read
the lines). Trimmed from 3,778 lines on 2026-11-02; every word that was here
was moved, unchanged, into the files named in the map.

**Code comments that say "CLAUDE.md rule N" or name a CLAUDE.md section**
now mean the file docs/rules/rule_N.md (for example rule 8 is `docs/rules/rule_8.md`)
or the doc in the map that holds that section.

## Writing to the owner

The owner is a solo operator, not a developer. Full rules: `docs/PLAIN_WORDS.md`.

- Whole sentences, each with a subject and a verb. One idea per sentence.
- Say the name of the thing, not "it". Say "for example" before an example.
- Short: answer first; no long recaps (decided 2026-11-02, `docs/DECISIONS.md`).
- Every reply after work says in one line what was checked by RUNNING,
  what by READING, and what was NOT checked. Name the test.
- After every change say two things: "deploy this", and whether the node
  (`worker_service/`) needs copying — if it changed, bump `AGENT_VERSION`.
- If his instruction would break something, say so and stop before building
  (`docs/rules/rule_1b.md`).

## Every claim carries its provenance (`docs/PROVENANCE.md`)

| Tag | Means |
|---|---|
| `MEASURED <date>` | Somebody ran it. Trust it; re-run to doubt it |
| `DECIDED <date>` | A choice. Say why before overturning it |
| `LEAD` | Untested. Never act on it as a diagnosis |
| `RULE` | Timeless while the code stays the same |

Nothing is a finding until you have tried to kill it: a reproduction, a log
line or row, or the path traced from the producer to the consumer.

## Every session, first

1. Read `tools/PENDING_DEPLOY.md` (waiting) and `tools/DEPLOY_LOG.md` (live).
   Re-read before any claim about what is deployed — he ships mid-session.
   Say at the start if anything is undeployed.
2. `TO_TEST.md`: live but never clicked by a person.
3. Check `docs/FAILURES.md` before investigating anything.

**Versions:** `APP_VERSION` = top `DEPLOY_LOG.md` entry + 1. Never fold more
work into a version already announced. Write what is waiting in
`PENDING_DEPLOY.md`. When he deploys, say whether it worked.
Detail: `docs/DOCS_AND_DEPLOY_STATE.md`, `DEPLOY.md`, `docs/SERVER.md`.

## The server is shared (DECIDED 2026-10-24, `docs/SERVER.md`)

Touch only `/opt/poster`, the Compose project `poster` and its service
`web`, and port 80. Never `docker system prune` or any prune, never restart
Docker, reboot, change the firewall or SSH, or upgrade the OS.

## Money and the marketplace

Never spend his money or change the marketplace on your own. Never enter
passwords. The production database is read only for diagnostics (rollback,
never commit). No content anybody owns (films, people, brands): that is why
both FineArtAmerica accounts were closed in 2026-08 (`docs/HISTORY.md`).

## Before code: the contract

- **Multi-project** (`MULTIPROJECT.md`, `docs/ARCHITECTURE.md`): one project
  (`travel`) runs today; code must never assume it. Scope through
  `projects.scope_titles()`, `worker._worker_project()`,
  `pipeline_admin._title_scope()`, `pipeline_admin._project()`.
  `external_id` is unique only inside one project.
- **Settings** resolve through `pipeline.get_setting(db, key, project=...)`;
  every key is declared in `pipeline.DEFAULTS`; site-wide keys in `SITE_WIDE`.
- **Never invent an external value** (URL, field name, duration) — look it up,
  read it at runtime, or ask (`docs/rules/rule_3c_bis.md`). Reuse the path
  that already works (`docs/rules/rule_3c_ter.md`).
- **Impossible beats detectable** (`docs/rules/rule_5.md`); name the
  invariant and make it a Diagnostics check (`docs/rules/rule_5d.md`).
- **Claimed work always ends in a reported state** (`docs/rules/rule_8.md`).
- New columns need `schema_migrations.py`. Comments explain why, not what.
- Facts about the outside world, measured — do not re-derive:
  `docs/facts/FAA_BEHAVIOUR.md`, `docs/facts/FAA_LISTING_PAGES.md`,
  `docs/facts/FAA_TITLE_RULES.md`, `docs/facts/GPT_IMAGE.md`,
  `docs/facts/TEEPUBLIC.md` (removed from the site).

## Before saying done

- Run `python tools/preflight.py`. A JS edit also needs `node --check`.
- A new check or test must be sabotage-tested: break the thing, see it go
  red, restore exactly. A test of a refusal needs a positive control first.
- Write the new lesson the same day: a row in `docs/FAILURES.md`, a decision
  in `docs/DECISIONS.md`, a control in `AUDIT.md`. Prefer a check that fails
  over a written rule (`docs/rules/rule_7.md`).

## The standing rules (`docs/rules/00_INDEX.md` has the full shape table)

| Rule | The shape it catches |
|---|---|
| `docs/rules/rule_1.md` | Built without listing what is on the screen |
| `docs/rules/rule_1b.md` | Built exactly what was asked and lost a capability |
| `docs/rules/rule_2.md` | Fixed the asked-for thing, left neighbours broken |
| `docs/rules/rule_3.md` | Said "verified" without naming a test |
| `docs/rules/rule_3b.md` | Reasoned from where a thing is shown, not what depends on it |
| `docs/rules/rule_3c.md` | Trusted the edit you meant over the file that exists |
| `docs/rules/rule_3c_bis.md` | Invented a value instead of looking it up |
| `docs/rules/rule_3c_ter.md` | Built a second path to something that works |
| `docs/rules/rule_3d.md` | Debugged the screen, not the machine doing the work |
| `docs/rules/rule_4.md` | Shipped without saying what else could break |
| `docs/rules/rule_5.md` | Warned where the state should be impossible |
| `docs/rules/rule_5b.md` | One marketplace's behaviour made policy for all |
| `docs/rules/rule_5d.md` | A mechanism with no invariant watching it |
| `docs/rules/rule_5e.md` | Left the owner as the only thing that could find a bug |
| `docs/rules/rule_6.md` | Correct for two projects, wrong for the third |
| `docs/rules/rule_7.md` | Fixed a bug and left no rule behind |
| `docs/rules/rule_8.md` | Claimed work with an exit that reports nothing |
| `docs/rules/rule_9.md` | Node dependency not installed, or `AGENT_VERSION` not bumped |

## Map — which file to read when

| File | Read when |
|---|---|
| `docs/ARCHITECTURE.md` | What the app is; server, node, Storage Box; master/project split |
| `docs/CODE_MAP.md` | Where code lives; running it locally |
| `docs/CONVENTIONS.md` | Folder paths, soft deletes, ActivityLog, external_id, migrations |
| `docs/PLANS.md` | The owner's stated plans and the audits still to do |
| `docs/HISTORY.md` | Why the old niches went; the data state |
| `docs/OWNER.md` | How he works; numbers and labels that make sense to him |
| `docs/NEW_PROJECT.md` | Before adding a niche |
| `docs/DOCS_AND_DEPLOY_STATE.md` | Why the deploy files are split; dead-vocabulary lessons |
| `docs/SERVER.md` | Deploying; the shared server |
| `docs/FAILURES.md` · `docs/DECISIONS.md` | Known problems · decisions, newest first |
| `MULTIPROJECT.md` | Before writing any code |
| `AUDIT.md` | Every control on every screen — extend it, never start another |
| `TO_TEST.md` | Live, not yet clicked by the owner; delete an item when he says done |
| `ROADMAP.md` · `MEGA_AUDIT.md` · `OPEN_ISSUES.md` | What is outstanding · the 2026-09-06 audit · open defects |
| `TITLE_RULES.md` | Before touching titles, the importer or the folder builder |
| `PIPELINE.md` | Post-production: painting, step 2, uploading, the node |
| `DEPLOY.md` · `SETUP_VPS.md` · `SETUP_WINDOWS_NODE.md` | Deploying; building a box from nothing |
| `CLAUDE_CODE.md` | How the owner runs sessions on his machine |
| `README.md` · `CHANGELOG.md` | For a newcomer · old released changes |
| `extensions/poster_admin_extension/README_INSTALL.md` | The admin's Chrome add-on (master copy here) |
