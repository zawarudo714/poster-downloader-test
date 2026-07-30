# Deploying to a fresh server

For `178.105.232.196` — a clean install with no legacy import. You upload the
master sheet, work a few titles as a worker, then exercise the admin side.

The legacy migration (your 4,811 existing uploads) is a **separate, later**
step — see `PIPELINE.md` §5.2. Nothing here depends on it.

---

## Before you start — what changed in the deployment config

Two defects were fixed that would have hurt you on this server. Worth knowing
because they change how you deploy.

**1. Nothing used to persist.** The old `docker-compose.yml` mounted `./data`
but the app wrote its database, workspace and backups to `/app/*` — inside the
container's writable layer. Every `docker compose up --build` would have
destroyed the database and every saved poster. All persistent state now lives
under `/app/data`, which is bind-mounted to `./data`.

**2. Backups pointed at the wrong file.** `DB_PATH` was hardcoded, so once the
database moved via `DATABASE_URL`, the nightly backup would have copied a file
that wasn't the live database — and a restore would have written to the wrong
path. `DB_PATH` is now derived from `DATABASE_URL`.

**Consequence for you:** secrets now come from a `.env` file, and Compose
refuses to start without them. That's deliberate — see step 3.

---

## 1. Push your local changes to GitHub

You're testing on a **separate repo** first, then promoting to the real one:

```
your PC  ──push──►  poster-downloader-test  ──►  178.105.232.196  (test)
         └─push──►  poster-downloader       ──►  178.105.34.144   (production)
```

### 1a. Create the test repo and add it as a second remote

Create it on GitHub: <https://github.com/new> → name it
`poster-downloader-test`. **Don't** initialise it with a README, `.gitignore`
or licence — you're pushing an existing history into it.

> **Public or private?** Nothing sensitive is committed — `.env`, `data/`,
> `*.db` and `workspace/` are all gitignored, and step 1b verifies that. A
> **public** test repo needs no authentication on the server at all, which
> removes a whole setup step. Private is fine too; it just needs a deploy key
> (§2a).

Then add it locally as a *second* remote, keeping `origin` pointed at
production so you can't confuse the two:

```cmd
cd "C:\Users\Administrator\Documents\Claude\Projects\Print On Demand\poster_downloader_web"

git remote add test https://github.com/zawarudo714/poster-downloader-test.git
git remote -v
```

You should see both:

```
origin  https://github.com/zawarudo714/poster-downloader.git (fetch/push)
test    https://github.com/zawarudo714/poster-downloader-test.git (fetch/push)
```

From now on:

| Command | Goes to |
|---|---|
| `git push test main` | test repo → test server |
| `git push origin main` | production repo → production server |

Because both remotes hold the same commits, promoting to production later is
just `git push origin main` — no merging, no cherry-picking, and what you
tested is byte-for-byte what ships.

> **Simpler alternative if you'd rather not manage two repos:** use one repo
> with a `test` branch (`git checkout -b test`, `git push origin test`), and
> have the test server track that branch. Same isolation, one deploy key, and
> promoting becomes a merge into `main`. The two-repo setup below works fine —
> this is just the lower-maintenance option.

### 1b. Check what you're about to commit

Verify no secrets or data slip in. `.gitignore` already covers `.env`,
`data/`, `*.db` and `workspace/`, but confirm before the first push:

```cmd
cd "C:\Users\Administrator\Documents\Claude\Projects\Print On Demand\poster_downloader_web"

git status
git check-ignore -v .env data poster.db workspace
```

The second command should print a matching `.gitignore` rule for each. If it
prints nothing for one of them, that file is **not** ignored — stop and fix it
before continuing.

Sanity-check nothing large or private is already tracked:

```cmd
git ls-files | findstr /I ".db .env workspace/ data/"
```

Should return nothing.

### 1c. Authenticate to GitHub

GitHub stopped accepting account passwords for git operations in 2021. Use one
of these:

**Personal Access Token (simplest)**

1. <https://github.com/settings/tokens> → *Generate new token (classic)*
2. Scope: **`repo`**. Expiry: your call — 90 days is a reasonable default.
3. Copy the token.
4. When git prompts for a password, paste the **token**, not your password.

Cache it so you're not pasting it every time:

```cmd
git config --global credential.helper manager
```

**Or SSH** — no expiry to manage:

```cmd
ssh-keygen -t ed25519 -C "windows-dev"
type %USERPROFILE%\.ssh\id_ed25519.pub
```

Paste that key at <https://github.com/settings/keys>, then point the remote at
SSH:

```cmd
git remote set-url origin git@github.com:zawarudo714/poster-downloader.git
ssh -T git@github.com
```

### 1d. Commit and push to the TEST repo

```cmd
git add -A
git commit -m "Post-production pipeline, deployment persistence fixes, dev setup tool"

git push test main
```

Note `test`, not `origin` — production stays untouched until you've proven
this deployment.

If the push is rejected because the remote already has commits you don't have
locally:

```cmd
git pull --rebase test main
git push test main
```

If the test repo was created empty, the very first push may need:

```cmd
git push -u test main
```

### 1e. Later — promoting to production

Once the test server has run through §5–§9 successfully:

```cmd
git push origin main
```

Then on the production box (`178.105.34.144`):

```bash
cd /path/to/poster_downloader_web
# Back up before any schema change.
docker compose exec web python scripts/migrate_pipeline.py --schema-only
git pull
docker compose up -d --build
```

Same commits, already exercised on the test server.

---

## 2. Prepare the server

Log in with your password as usual — you don't need an SSH key for this:

```bash
ssh root@178.105.232.196
# enter your password when prompted
```

Then install Docker:

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

### 2a. Get the code onto the server

> **The keys below are not about how *you* log in.** You keep using your
> password for that. This is about how the **server** authenticates to
> **GitHub** when it pulls code — a separate connection that never involves
> your password.
>
> If your test repo is **public**, skip straight to the public option and
> there's no key to create at all.

**Public test repo — nothing to configure:**

```bash
git clone https://github.com/zawarudo714/poster-downloader-test.git /opt/poster
cd /opt/poster
```

**Private repo — create a deploy key on the server.** It's a read-only,
per-repository key that can't reach your other repos and doesn't expire:

```bash
ssh-keygen -t ed25519 -C "poster-deploy-178.105.232.196" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Copy the whole line it prints (starts `ssh-ed25519 …`), then in GitHub:

**your test repo → Settings → Deploy keys → Add deploy key**

- Title: `hetzner-178.105.232.196`
- Key: paste it
- **Leave "Allow write access" unticked** — the server only pulls

Back on the server:

```bash
ssh -T git@github.com
# type "yes" to accept the fingerprint; expect:
#   Hi zawarudo714/poster-downloader-test! You've successfully authenticated...

git clone git@github.com:zawarudo714/poster-downloader-test.git /opt/poster
cd /opt/poster
```

> Not sure if the repo is private? Open its URL in a logged-out browser window.
> A 404 means private.
>
> **Each server needs its own deploy key**, and a key can only be attached to
> one repository. When you later deploy the production repo to
> `178.105.34.144`, repeat this there against `poster-downloader`.

## 3. Create the secrets file

```bash
cp .env.example .env
openssl rand -hex 32   # -> SESSION_SECRET
openssl rand -hex 32   # -> PIPELINE_SECRET
nano .env
```

Paste one value into each. Then:

```bash
chmod 600 .env
```

> **`PIPELINE_SECRET` is effectively permanent.** It encrypts marketplace
> account passwords. Change it later and those passwords stop decrypting —
> accounts pause with a clear reason rather than crashing, but you re-enter
> every password by hand. Save it somewhere safe now.
>
> Compose will refuse to start if either secret is missing. That's intentional:
> an unset `SESSION_SECRET` silently regenerates per process and logs everyone
> out on each restart.

## 4. Build and start

```bash
docker compose up -d --build
docker compose logs -f          # Ctrl-C once you see uvicorn listening
```

Create the pipeline tables and columns (harmless on a fresh database, and it
keeps the migration exercised):

```bash
docker compose exec web python scripts/migrate_pipeline.py --schema-only
```

Create your admin account:

```bash
docker compose exec web python scripts/create_admin.py
```

Check it's alive:

```bash
curl -s http://localhost/healthz     # {"ok":true}
```

## 5. Open the firewall

The app is published on **port 80**, so no port suffix in the URL.

In the Hetzner Cloud console → Firewalls, allow inbound **TCP 80** (and 22 for
SSH). Then open:

```
http://178.105.232.196/login
```

> Plain HTTP means passwords cross the network in the clear. Fine for a test
> box. Before real workers use it, put Caddy or nginx in front for TLS — a
> domain plus Caddy is about five lines and gets you an automatic certificate.

## 6. Import the master sheet

**Admin → Title List → Import.** Accepts `.csv` or `.xlsx`. Headers are
lower-cased and trimmed, so capitalisation doesn't matter.

| Column | Required | Notes |
|---|---|---|
| `title` | **yes** | Rows without one are skipped |
| `0` *(or `num`, `external_id`)* | strongly recommended | The universal key — folder prefix, tracking key, everything joins on it |
| `releaseYear` *(or `year`)* | no | First 4-digit number is taken; missing becomes `N/A` |
| `contentType` | no | `movie` / `tvSeries` |
| `description` | no | Used verbatim as the marketplace listing description |
| `votes`, `rating` | no | Display only |

Your existing sheet already has these — it's the same file `faa_content_data.json`
was built from.

> **Use the same `0` column values as before.** It's the key that ties poster
> folders, the master list and upload history together. If it shifts, the later
> migration can't match anything.

Import runs in the background; the page shows progress. 101,605 rows takes a
few minutes.

## 7. Create a worker and test the worker flow

**Admin → Users → Create User**, role `worker`.

Then in a private window (so you stay logged in as admin), sign in as that
worker and run through:

1. **GET** — claims a batch of titles
2. Click a title → **Open TMDB** → copy a poster's *link address*
3. Paste, save. Try 2–3 posters on one title
4. Deliberately paste a preview-sized URL to see the low-quality warning
5. **DONE** on one title, **SKIP** another with a reason
6. **RETURN UNWORKED** to release the rest

## 8. Test the admin side

- **Review Posters** — the gallery. Flag a poster with a comment; check the
  sub-800px red border shows on a small image
- **Changes Requested** — the worker sees the flag, replaces the poster, it
  arrives here for approval. Approve one, reject one
- **Skipped Titles** — send one back with a note; confirm the worker sees it
- **Payments** — pick the worker and date range, check the eligible count,
  mark paid, push the receipt, acknowledge it as the worker
- **Backups** — take a manual snapshot. Confirm it appears (this also proves
  the `DB_PATH` fix: the snapshot should be non-trivial in size, not 0 bytes)
- **Pipeline** — after marking paid, the funnel should show posters under
  **Greenlit**, because paying auto-greenlights by default

## 9. Confirm persistence before trusting it

The most important check on this server:

```bash
docker compose down
docker compose up -d --build
```

Log back in. Your users, titles and posters must all still be there. If
anything vanished, stop and re-check `.env` and the `volumes:` block — do not
put real data on it until a rebuild is provably safe.

You can also see the state on the host directly:

```bash
ls -la /opt/poster/data/
du -sh /opt/poster/data/workspace
```

## 10. Only then, the Windows node

Once the above is solid, continue with `SETUP_VPS.md` step 3. The node's
`config.json` points at:

```json
"server_url": "http://178.105.232.196"
```

No `:8000` — the app is on port 80.

---

## Day-to-day

**Deploy an update**

On Windows — push to whichever repo that server tracks:

```cmd
git add -A
git commit -m "what changed"

git push test main        # test server  178.105.232.196
git push origin main      # production   178.105.34.144
```

On the server:

```bash
cd /opt/poster
git pull
docker compose up -d --build
```

Safe now that state lives in `./data`. If the release adds database columns,
run the migration *before* rebuilding:

```bash
docker compose exec web python scripts/migrate_pipeline.py --schema-only
```

Bump `APP_VERSION` in `app/config.py` whenever you change JS or CSS — it's the
cache-buster on every static asset, and without it browsers keep serving the
old files.

**If `git pull` complains about local changes on the server**

You shouldn't be editing files there, but if it happens:

```bash
git status                  # see what changed
git stash                   # park it
git pull
git stash pop               # or: git stash drop  to discard
```

To force the server back to exactly what's on GitHub — **discards local edits,
but never touches `data/` or `.env`, which are gitignored**:

```bash
git fetch origin
git reset --hard origin/main
```

**Back up off the box.** The nightly backup writes to `data/backups/` on the
same disk, which doesn't help if the server dies. Once the Storage Box is
mounted, pull it down periodically:

```bash
rsync -avz -e 'ssh -p23' /opt/poster/data/backups/ u642720@u642720.your-storagebox.de:./db-backups/
```

**Logs**

```bash
docker compose logs -f web
docker compose logs --tail=200 web
```

---

## Do not run `dev_setup.py` on this server

It wipes the database, the workspace and the backups. It refuses to run once
there are more than 5,000 master titles or more than 3 payment runs — which
your imported sheet will trip immediately — but don't rely on that as your
only line of defence. It's a local development tool.
