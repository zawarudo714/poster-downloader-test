# Buying and setting up the infrastructure

Step-by-step for the three pieces the automated pipeline needs. Follow the
order below — it puts the cheapest, most reversible purchases first, so you
find out about any blocker before spending on the expensive box.

> **Read `PIPELINE.md` §2 first** if you haven't. It explains why the Windows
> node holds no configuration, which is what makes it disposable.

---

## What you're buying, and why

| # | Thing | Runs | Cost (checked Jul 2026) |
|---|---|---|---|
| 1 | **Hetzner Storage Box** (BX11, 1 TB) | The permanent archive of processed images | ~€3.20/mo |
| 2 | **Hetzner Cloud CPX12** (1 vCPU / 2 GB / 40 GB) | `poster_downloader_web` — the brain | $13.49/mo |
| 3 | **Contabo Windows VPS** (VPS M or better) | Photoshop + Chrome — the muscle | ~€13–17/mo |

Hetzner raised cloud prices steeply during 2026, so confirm at checkout.
Contabo's Windows licence is charged on top of the Linux price.

### Sizing, measured from the real data

**Processed images average 5.8 MB.** 1 TB holds ~176,000 of them — roughly
76,000 titles. Hetzner lets you upsize a Storage Box later without migrating.

**Raw posters average 550 KB**, at 2.33 posters per completed title, so about
**1.28 MB per title** on the cloud server. CPX12's 40 GB allocates roughly:

| | |
|---|---|
| Ubuntu + Docker + image | ~7 GB |
| `poster.db` + 14 daily backups | ~1 GB |
| Left for raw posters | ~32 GB ≈ **25,000 titles** |

At 3,467 titles today that's about 7× headroom, so **CPX12 is the right
starting point**. 1 vCPU / 2 GB is ample for the workload — the app is
I/O-bound with a couple of workers and one admin; the only place you notice
it is `docker compose --build` taking a few minutes.

**Don't reach for a bigger server when disk gets tight.** CPX22 costs $9.50/mo
more for 40 GB; a Storage Box is €3.20/mo for 1,000 GB — roughly 60× cheaper
per GB. Raw posters only have to stay on the server long enough to be
processed (they're kept so you can reprocess if the effect changes later), so
archiving them to the Storage Box is the economical scaling path.

Hetzner servers can be rescaled afterwards. Growing the disk is the one-way
part — CPU/RAM-only rescales can be reversed — so starting small keeps your
options open.

Projection if you want to plan further ahead:

| Titles completed | Raw storage on the server |
|---|---|
| 3,467 (today) | 4.2 GB |
| 10,000 | 12 GB |
| 25,000 | 30 GB — CPX12 full |
| 50,000 | 61 GB |
| 101,605 (all) | 124 GB |

---

## Three gotchas to know before you start

These are the things that waste a day if you meet them by surprise.

### Photoshop needs a logged-in desktop

Photoshop will **not** run as a Windows service or in a non-interactive
session. It needs a real desktop. That means the worker agent has to run
inside a logged-in user session, and when you finish with RDP you must
**disconnect, not log off** — logging off kills the session and Photoshop
with it.

Step 4 below sets up auto-logon so the machine recovers by itself after a
reboot.

### Outbound port 445 may be blocked

SMB (mapping the Storage Box as `S:`) uses port 445. Some hosts block it
outbound because of old SMB worms. Whether Contabo does is worth finding out
*before* you depend on it — hence the cheap test in Step 1. If it is blocked,
Step 3b gives you an SFTP-based alternative that works identically as far as
the pipeline is concerned.

### Adobe licensing

A standard Creative Cloud individual licence permits installation on two
machines, with only one in use at a time. Your laptop plus the VPS is two. If
you want to keep using Photoshop locally while the VPS is processing, check
your plan — you may need a second seat.

---

## Step 1 — Hetzner Storage Box (do this first)

Cheapest purchase, and it de-risks the SMB question for €3.20.

1. Go to <https://www.hetzner.com/storage/storage-box/> and order **BX11**
   (1 TB). Pick the datacentre nearest your other servers.
2. Open <https://console.hetzner.com/>, find the Storage Box, choose
   **Actions → Change settings**, and enable **both**:
   - **SMB support** — off by default
   - **External Reachability** — off by default, and this is the one that
     catches people out

   Allow a few minutes after saving; activation isn't instant.
3. Set a password (**Actions → Reset password**). Use *Generate a password* —
   this credential ends up stored on a rented server, and a generated one
   avoids the mangling that punctuation causes in Windows credential prompts.
   Confirm the dialog actually closes; if you dismiss it without clicking
   **Save Password**, the old password stays active.
4. Note the username (`u######`) and the share path shown as SAMBA/CIFS SHARE.

> **Why External Reachability matters.** With it off, the Storage Box only
> accepts connections from inside Hetzner's own network. Your Contabo Windows
> VPS is *not* in that network, so it must stay enabled. The failure mode is
> misleading: Hetzner's SMB endpoint answers and then rejects the connection,
> so you get **System error 5 — Access is denied**, exactly as if the password
> were wrong. A blocked port would give you a timeout instead.
>
> Because the box is then reachable from anywhere with just a username and
> password, use a strong generated password and don't reuse it.

### Test the mount from your own PC right now

Don't wait for the VPS. On your Windows machine:

- File Explorer → **This PC** → three dots (Win 11) or *Computer* tab
  (Win 10) → **Map network drive**
- Drive letter: `S:`
- Folder: `\\u######.your-storagebox.de\backup`
- Tick **Reconnect at sign-in**, click Finish, enter the username and password

If the GUI dialog gives you trouble, skip it — this avoids both common traps
(Windows silently sending your *Windows* account instead of the Storage Box
one, and it prefixing the username with your computer name):

```cmd
net use S: \\u######.your-storagebox.de\backup /user:u###### * /persistent:yes
```

The `*` prompts for the password without echoing it.

If `S:` appears and you can create a folder in it, SMB works from a normal
internet connection and will work from the VPS too.

### If it says "Access is denied"

Work through these in order — each takes under a minute:

1. **External Reachability enabled?** (see the box above) — most likely cause.
2. **SMB support enabled?** The overview page shows the SAMBA/CIFS share path
   whether or not the protocol is on, so that field is not proof.
3. **Test the password over SFTP.** Port 22 is *always* active with no toggle
   needed, which makes this a clean isolation test:
   ```cmd
   sftp u######@u######.your-storagebox.de
   ```
   An `sftp>` prompt means the password is right and the problem is one of the
   two toggles above. "Permission denied" means the password reset didn't save.
4. **Clear cached credentials.** Windows re-sends a rejected credential
   indefinitely:
   ```cmd
   net use S: /delete
   cmdkey /delete:u######.your-storagebox.de
   ```
5. **If using the GUI:** tick **Connect using different credentials**. Without
   it Windows never sends `u######` at all.

Then copy your existing processed archive up — the migration records paths in
exactly this layout, so nothing needs renaming:

```cmd
robocopy "C:\Users\Administrator\Desktop\FineArtAmerica Tell-A-Vision\Outputs\Straight From Photoshop" S:\processed /E /Z /R:3 /LOG:C:\faa_copy.log
```

That's ~28 GB, so leave it running. **It isn't blocking anything** — the
pipeline works without it. The archive only matters for re-uploading to a
replacement account later.

---

## Step 2 — Hetzner Cloud server (the brain)

1. In <https://console.hetzner.com/>, create a new project, then a new server.
2. **CPX12** (1 vCPU / 2 GB / 40 GB) — see the sizing section above. Disk is
   the constraint here, not CPU, and 40 GB covers ~25,000 titles.
3. Image: **Ubuntu 24.04**. Add your SSH key.
4. Note the IP.

> **Housekeeping that buys you margin later:** lower
> `AUTO_BACKUP_RETENTION_DAYS` (default 14) if disk gets tight, and avoid
> running a large ZIP export when the disk is near full — the builder writes
> the archive to `workspace/_zips/` before you download it. Once the pipeline
> is automated you won't need ZIP exports at all; that was the manual
> workflow.

### Install Docker and deploy

```bash
ssh root@<NEW_SERVER_IP>

# Docker
curl -fsSL https://get.docker.com | sh

# Get the code
git clone <your-repo-url> /opt/poster
cd /opt/poster
```

Set the two secrets before the first start. `PIPELINE_SECRET` encrypts
marketplace passwords — if you lose or change it later, stored account
passwords stop decrypting and you have to re-enter them.

```bash
openssl rand -hex 32   # copy this -> SESSION_SECRET
openssl rand -hex 32   # copy this -> PIPELINE_SECRET
```

Add them to `docker-compose.yml` under the web service:

```yaml
    environment:
      - TZ=Africa/Nairobi
      - APP_TZ=Africa/Nairobi
      - SESSION_SECRET=<first value>
      - PIPELINE_SECRET=<second value>
```

Then build and initialise:

```bash
docker compose up -d --build
docker compose exec web python scripts/migrate_pipeline.py --schema-only
docker compose exec web python scripts/create_admin.py
```

Check it responds:

```bash
curl http://localhost:8000/healthz     # {"ok":true}
```

Open `http://<NEW_SERVER_IP>:8000/login` in a browser and sign in.

> **Firewall:** in the Hetzner console, allow inbound TCP 8000 (or put it
> behind a reverse proxy with TLS later). The Windows node needs to reach
> this port.

Since this is your test instance, you can seed it with sample data instead of
importing the real history:

```bash
docker compose exec web python scripts/dev_setup.py --cli
```

That gives you `admin` / `123456`, master titles, and completed work with real
PNG files — the same thing you have locally, but reachable from the VPS.

---

## Step 3 — the Windows machine (the muscle)

Buy this **last**, once Steps 1 and 2 are working.

### First: decide how much Windows you actually need

**Only the Photoshop stage requires Windows.** The upload stage is plain
Selenium + headless Chrome and runs perfectly well on Linux. The agent
supports running one stage or both:

```bash
python -m worker_service.agent --stages upload     # uploads only
python -m worker_service.agent --stages process    # Photoshop only
```

and each registered node declares its own capabilities server-side. That gives
you three deployments, in ascending cost:

| Option | Photoshop runs | Uploads run | Extra cost |
|---|---|---|---|
| **A. Split** | your own PC, when it's on | Hetzner Linux box you already pay for | **€0** |
| **B. Split + small Windows box** | small Windows VPS | Hetzner Linux box | Windows VPS only |
| **C. All-in-one** | Windows VPS | same Windows VPS | Windows VPS only |

**Option A is worth taking seriously.** The upload side is the one that must
run every day, and it's capped at 100/day by the marketplace — that runs free
on the Linux server. Photoshop is bursty: at roughly 1–2 minutes an image,
ongoing output of ~100–150 posters/day is 2–5 hours, which your own machine
can absorb overnight. The one-off backlog of ~3,161 images is the painful part
(50–100 hours), but it's one-off.

Option C is simplest to reason about and what the rest of this guide assumes.
If you want off your own hardware entirely, take it. Just know that the €17/mo
Windows licence is buying you *only* the Photoshop stage.

### Provider options

| Provider | Notes |
|---|---|
| **Contabo** | 6 vCPU / 12 GB at €7.50 + €17 Windows Server. Good specs per euro; known for shared-I/O contention at peak, which matters little here since Photoshop is CPU-bound. **Windows Server only** — see the Photoshop compatibility note below. |
| **DatabaseMart** | Offers **Windows 10/11**, not just Server — which removes the Adobe support question entirely. Worth pricing if the Contabo install refuses. |
| **Netcup** | Strong European value, but check Windows licensing availability on the plan you want. |
| **Hetzner dedicated** | The only route where your *own* Windows licence is permitted, but entry dedicated starts ~€40–50/mo — more than Contabo *with* the licence. |

Prices move; confirm at checkout.

1. <https://contabo.com/en/vps/> → **Cloud VPS 6** (6 vCPU / 12 GB / 200 GB)
   or larger. Photoshop wants the RAM and Chrome runs alongside it.
2. Checkout choices:

| Option | Choose | Why |
|---|---|---|
| **Term** | **1 Month** (€7.50) | 12/24 months save 15–20%, but don't commit before Photoshop is proven to install. Switch next cycle. |
| **Image** | **Windows Server** (+€17.00/mo) | Required — see the licensing note below |
| **Region** | European Union (free) | Close to Hetzner Falkenstein, where the server and Storage Box live |
| **Storage** | 200 GB SSD (free) | The node is stateless; don't pay for 400 GB |
| **Auto Backup** | **No Data Protection** | Saves €3.35/mo — see below |
| **Object Storage / Monitoring** | None | Not used |

3. Set a strong root password at checkout (Contabo won't email it to you).
4. RDP credentials arrive by email — usually hours, sometimes longer for
   Windows.

> **You cannot bring your own Windows licence to Contabo.** Their support
> documentation states it directly: *"It is not possible to use a Windows
> Custom Image on your Contabo server due to Contabo's license agreement with
> Microsoft."*
>
> This is Microsoft's licensing rather than a Contabo quirk — on shared
> virtualisation the provider must supply the Windows licence (SPLA), and
> desktop Windows 10/11 isn't licensed for hosting on shared infrastructure at
> all. Bringing your own generally requires dedicated hardware, and entry
> dedicated servers start around €40–50/mo, so the €17 add-on is still the
> cheaper route.
>
> Your own Photoshop installer and licence are unaffected — you install that
> yourself either way.

> **Skip Auto Backup deliberately.** The architecture treats this box as
> disposable: it holds no state. The script, selectors, credentials and
> timings live in the dashboard; processed images live on the Storage Box. If
> it dies you reinstall and restore one token. Paying to back it up protects
> nothing.
>
> Do use one of the **2 free snapshots** once Photoshop, Chrome and Python are
> installed and working. That's the recovery point that actually saves you
> time, and it's included.

### Install Photoshop first, before anything else

Adobe does not officially support Creative Cloud on Windows Server editions,
and recent installers can block on OS detection. Your JSX targets **Photoshop
2023**, which is markedly less strict than the 2025/2026 releases, so this
will most likely be fine — but find out on day one.

If the installer refuses, you've spent one month rather than a year, and the
alternative is a dedicated server where your own Windows 10/11 licence is
permitted.

### 3a. First login

Connect with Windows Remote Desktop (`mstsc`) using the supplied IP and
credentials. Change the administrator password immediately.

### 3b. Mount the Storage Box

Same as Step 1, on the VPS:

```
\\u######.your-storagebox.de\backup   →   S:
```

Tick **Reconnect at sign-in**.

**If it fails** — port 445 is blocked outbound. Use SFTP instead, which the
pipeline can't tell apart:

1. Install [WinFsp](https://winfsp.dev/rel/) and
   [rclone](https://rclone.org/downloads/).
2. `rclone config` → new remote → type `sftp` → host
   `u######.your-storagebox.de`, user `u######`, port `23`, your password.
3. Mount it at startup:
   ```cmd
   rclone mount storagebox:/ S: --vfs-cache-mode writes
   ```

Either way the pipeline just sees `S:`.

### 3c. Install the software

- **Photoshop** with the **Real Paint FX** plugin. Launch it once manually and
  clear any first-run dialogs, sign-in prompts and "what's new" screens — an
  unattended run cannot dismiss a modal.
- **Google Chrome**.
- **Python 3.11+** — tick *Add Python to PATH* in the installer.

Note the exact install paths; you'll put them in the dashboard:

```
C:\Program Files\Adobe\Adobe Photoshop 2023\Photoshop.exe
C:\Program Files\Adobe\Adobe Photoshop 2023\Real Paint FX\Scripts (actions)\Real-Paint-FX.jsx
```

### 3d. Install the worker agent

Copy the `worker_service/` folder onto the VPS — `C:\faa\worker_service` is a
reasonable home. Then:

```cmd
cd C:\faa
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r worker_service\requirements.txt
mkdir C:\faa\temp C:\faa\logs
```

---

## Step 4 — Connect everything

### 4a. Register the node

In the dashboard on your Hetzner server: **Pipeline → Nodes → REGISTER NODE**.
Name it something like `win-vps-1`, leave both capabilities ticked.

**Copy the token immediately** — only its hash is stored and it cannot be
shown again. (If you lose it, use ROTATE TOKEN.)

### 4b. Write the node's config

Create `C:\faa\worker_service\config.json`:

```json
{
  "server_url": "http://<HETZNER_SERVER_IP>:8000",
  "token": "<the token you just copied>",
  "temp_dir": "C:/faa/temp",
  "log_dir": "C:/faa/logs",
  "storage_root_override": null
}
```

This is the **only** configuration on this machine. Everything else — the
Photoshop script, selectors, timings, credentials — arrives from the dashboard
on every cycle.

### 4c. Point the dashboard at the real paths

**Pipeline → Processing**, set:

| Setting | Value |
|---|---|
| Photoshop executable | `C:/Program Files/Adobe/Adobe Photoshop 2023/Photoshop.exe` |
| FX plugin script path | `C:/Program Files/.../Real-Paint-FX.jsx` |

**Pipeline → Processing → Storage**:

| Setting | Value |
|---|---|
| Storage root | `S:/processed` |

Forward slashes are fine and avoid escaping problems.

### 4d. Verify the connection

On the VPS:

```cmd
cd C:\faa
.venv\Scripts\python.exe -m worker_service.agent --once
```

You should see it announce itself and report no work. Back in the dashboard,
**Pipeline → Nodes** should show it **ONLINE**.

### 4e. Make it survive reboots

Because Photoshop needs a desktop session:

1. **Auto-logon** — run `netplwiz`, untick *Users must enter a user name and
   password*, enter the administrator password when prompted.
2. **Start the agent at logon** — press `Win+R`, type `shell:startup`, and put
   a file `agent.bat` in the folder that opens:

   ```bat
   @echo off
   cd /d C:\faa
   :loop
   .venv\Scripts\python.exe -m worker_service.agent
   echo Agent exited, restarting in 30s...
   timeout /t 30
   goto loop
   ```

   The loop restarts it if it ever dies.
3. **When you finish with RDP, click the X to disconnect — do not Log Off.**
   Logging off ends the desktop session and Photoshop stops working.

---

## Step 5 — Prove each stage, one image at a time

Do not start a batch yet. **Pipeline → Test & Debug**, in order:

1. **Test Download** — enter any completed title's id. Proves the URL, token
   and network path.
2. **Test Process** — enter one poster id. Proves Photoshop, the plugin, the
   script and the storage mount. Watch the Live Console; you should get output
   dimensions and a duration.
3. **Test Upload** — needs a marketplace account first.

For the upload test, add an account under **Pipeline → Upload → ADD ACCOUNT**.
For a *test* server, either use a throwaway FineArtAmerica account or leave it
disabled until you're ready — a test upload against your real account creates
a real listing.

Each test streams a per-phase log. When one fails, the log shows exactly which
phase and which selector, with a screenshot.

Only once all three pass: greenlight one date and watch it run end to end.

---

## Order of operations, summarised

```
1. Storage Box (€3.20)      -> test the SMB mount from your own PC
2. Hetzner Cloud server     -> deploy, migrate schema, create admin, seed
3. Contabo Windows VPS      -> Photoshop, Chrome, Python, mount S:
4. Register node, config.json, set the real paths in the dashboard
5. Test Download -> Test Process -> Test Upload, one image each
6. Greenlight one date, watch it, then release the backlog
```

Steps 1 and 2 are independently useful and cost under €10/month combined. Only
Step 3 commits you to the larger monthly spend, and by then you'll have proven
the storage and the server.

---

## When you switch from testing to production

You said you'll wipe and redo this with the real data. At that point:

- Point the node's `config.json` at the production server instead, or register
  a second node there.
- Run the real import rather than `dev_setup.py`:
  ```bash
  docker compose exec web python scripts/migrate_pipeline.py --dry-run \
    --tracking /data/faa_upload_tracking.json \
    --processed-root "/data/Straight From Photoshop"
  ```
  See `PIPELINE.md` §5.2 for the full sequence.
- Set the real marketplace password and enable the account.
- Keep `PIPELINE_SECRET` stable from that point on.

---

## Sources

- [Hetzner Storage Box — SAMBA/CIFS access](https://docs.hetzner.com/storage/storage-box/access/access-samba-cifs/)
- [Hetzner Storage Box BX11](https://www.hetzner.com/storage/storage-box/bx11/)
- [Hetzner Storage Box overview](https://www.hetzner.com/storage/storage-box/)
- [Hetzner price adjustment notice](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)
- [Contabo VPS](https://contabo.com/en/vps/)
