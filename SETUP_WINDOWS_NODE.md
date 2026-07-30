# Windows node setup — from scratch

Concrete, in order, with your real values filled in. Work top to bottom.

| | |
|---|---|
| **VPS** | `169.58.98.225` — Contabo Cloud VPS 6, Windows Server |
| **RDP user** | `administrator` (password you set at checkout) |
| **Dashboard** | `http://178.105.232.196` |
| **Storage Box** | `\\u642720.your-storagebox.de\backup`, user `u642720` |
| **Working root** | `C:\faa` |

**Phase 5 is the one that matters.** Everything before it is ten minutes of
easily-repeated work; Photoshop on Windows Server is the only step that can
fail in a way that changes your plan. Get there today.

---

## Phase 1 — Connect

Open **Remote Desktop Connection** (Win+R → `mstsc`). Click **Show Options**
*before* connecting:

1. **Local Resources** tab → **More…** → tick your **C:** drive → OK
   *(this is how files reach the VPS — it shows up there as `\\tsclient\C`)*
2. **General** tab:
   - Computer: `169.58.98.225`
   - User name: `administrator`
3. **Connect** → enter your checkout password → accept the certificate warning

If it refuses, wait 10–15 minutes; Windows sometimes finishes first boot after
the welcome email goes out. Still nothing? Use the VNC console in the Contabo
panel to check it actually booted into Windows.

---

## Phase 2 — Make Windows Server usable

Open **PowerShell as Administrator** (right-click Start → Windows PowerShell
(Admin)) and paste this whole block:

```powershell
# Stop IE Enhanced Security blocking every download
$AdminKey = "HKLM:\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A7-37EF-4b3f-8CFC-4F3A74704073}"
$UserKey  = "HKLM:\SOFTWARE\Microsoft\Active Setup\Installed Components\{A509B1A8-37EF-4b3f-8CFC-4F3A74704073}"
Set-ItemProperty -Path $AdminKey -Name "IsInstalled" -Value 0 -ErrorAction SilentlyContinue
Set-ItemProperty -Path $UserKey  -Name "IsInstalled" -Value 0 -ErrorAction SilentlyContinue

# Stop Server Manager opening on every login
New-ItemProperty -Path "HKCU:\Software\Microsoft\ServerManager" `
  -Name "DoNotOpenServerManagerAtLogon" -Value 1 -PropertyType DWORD -Force | Out-Null

# Audio service — disabled by default on Server; Adobe installers can trip on it
Set-Service -Name Audiosrv -StartupType Automatic
Start-Service Audiosrv

# Working folders
New-Item -ItemType Directory -Force -Path C:\faa, C:\faa\temp, C:\faa\logs, C:\faa\profiles | Out-Null

Write-Host "Phase 2 done." -ForegroundColor Green
```

---

## Phase 3 — Chrome and Python

Same PowerShell window:

```powershell
# Chrome
curl.exe -L -o "$env:TEMP\chrome.exe" https://dl.google.com/chrome/install/latest/chrome_installer.exe
Start-Process "$env:TEMP\chrome.exe" -ArgumentList "/silent /install" -Wait

# Python 3.11 — PATH and pip included
curl.exe -L -o "$env:TEMP\python.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
Start-Process "$env:TEMP\python.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1" -Wait
```

**Close and reopen PowerShell** (so PATH refreshes), then check:

```powershell
python --version      # Python 3.11.9
pip --version
```

If `python` isn't recognised, reopen PowerShell again — the PATH change only
applies to new sessions.

---

## Phase 4 — Mount the Storage Box

```powershell
net use S: \\u642720.your-storagebox.de\backup /user:u642720 * /persistent:yes
```

Enter the Storage Box password when prompted (not your Contabo one). Then:

```powershell
dir S:\
New-Item -ItemType Directory -Force -Path S:\processed | Out-Null
```

If you get **Access is denied**, External Reachability or SMB is off — see
`SETUP_VPS.md` step 1. You already enabled both, so this should just work.

---

## Phase 5 — Photoshop  ← the real test

### 5a. Install

Open Chrome on the VPS, sign in at <https://creativecloud.adobe.com>, install
the Creative Cloud desktop app, then:

**Photoshop → ⋯ → Other versions → 2023 → Install**

Install **2023 specifically**, not the latest:

- Your Real Paint FX plugin is proven against 2023
- Older releases are less strict about Windows Server
- Your dashboard settings already point at the 2023 paths

> If Creative Cloud refuses with an unsupported-OS error, that's the known
> Server restriction. Fall back to your own standalone installer — copy it over
> via `\\tsclient\C` (the drive you ticked in Phase 1) or `S:\installers\`.

### 5b. Install Real Paint FX

Extract it into the Photoshop version's folder. Confirm both exist (swap the
version number for whatever you installed):

```powershell
Test-Path "C:\Program Files\Adobe\Adobe Photoshop 2026\Photoshop.exe"
Test-Path "C:\Program Files\Adobe\Adobe Photoshop 2026\Real Paint FX\Scripts (actions)\Real-Paint-FX.jsx"
```

Both must print `True`. Note the real paths — they go into the dashboard in
Phase 7.

> **You do NOT need the FX Box panel installer.** The pipeline runs
> `Real Paint FX\Scripts (actions)\Real-Paint-FX.jsx` directly via
> `$.evalFile()`. The separate `Fx Tool\Real-Paint-FX_installer.jsx` only
> registers the effect into the FX Box panel for interactive clicking, which
> automation never uses.
>
> That installer fails on Photoshop 2026 with
> `TypeError: undefined is not an object` — the plugin dates from 2018/2019 and
> the panel API has moved on. **Ignore it.** It has no bearing on the pipeline.

### 5c. Load the presets — REQUIRED, and easy to forget

Real Paint FX needs its **pattern set (`.pat`)** and **action set (`.atn`)**
loaded into Photoshop before the script will run. These live in Photoshop's own
preferences, not in the script — so the pipeline cannot supply them and a fresh
machine fails until they're loaded by hand, once.

Find them:

```powershell
Get-ChildItem "C:\Program Files\Adobe\Adobe Photoshop 2026\Real Paint FX" -Recurse -Include *.atn,*.pat |
  Select-Object FullName
```

Load each one — double-clicking the file with Photoshop open is usually enough;
otherwise **Edit → Presets → Preset Manager** for the pattern, and the Actions
panel's ⋯ menu → **Load Actions** for the `.atn`.

**Then copy both files somewhere that outlives the VPS:**

```powershell
New-Item -ItemType Directory -Force -Path S:\installers\realpaintfx-presets | Out-Null
Get-ChildItem "C:\Program Files\Adobe\Adobe Photoshop 2026\Real Paint FX" -Recurse -Include *.atn,*.pat |
  Copy-Item -Destination S:\installers\realpaintfx-presets
```

> **Why this matters later.** If Photoshop's preferences reset, or you rebuild
> this box, processing starts failing with plugin errors and nothing in the
> pipeline points at the cause — the script and paths are unchanged, the
> presets are simply gone. This is also why the effect "just worked" on the
> laptop for years: the presets were loaded there once and forgotten.

### 5d. Prove it manually

Open Photoshop **by hand** and:

1. Clear every first-run dialog, sign-in prompt and "what's new" screen. An
   unattended run cannot dismiss a modal.
2. Open any image → **File → Scripts → Browse** → pick
   `Real Paint FX\Scripts (actions)\Real-Paint-FX.jsx`
3. Confirm the painterly effect applies.

**Do not continue until this works.**

### 5e. Snapshot — do this immediately

Contabo panel → your VPS → **Snapshots** → create one, name it
`photoshop-working`.

This is the step that preserves the loaded presets, since they're part of
Photoshop's preferences rather than any file the pipeline manages. It's the
most expensive state on this machine to recreate, and the snapshot is free.

---

## Phase 6 — The worker agent

### 6a. Get the code

Git isn't installed on Server, and you don't need it — pull the repo as a zip:

```powershell
cd C:\faa
curl.exe -L -o repo.zip https://github.com/zawarudo714/poster-downloader-test/archive/refs/heads/main.zip
Expand-Archive repo.zip -DestinationPath C:\faa\_repo -Force
Copy-Item C:\faa\_repo\poster-downloader-test-main\worker_service C:\faa\worker_service -Recurse -Force
Remove-Item C:\faa\repo.zip, C:\faa\_repo -Recurse -Force
dir C:\faa\worker_service
```

You should see `agent.py`, `client.py`, `processor.py`, `uploader.py`.

### 6b. Python environment

```powershell
cd C:\faa
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r worker_service\requirements.txt
```

### 6c. Register the node and get a token

In your browser (on either machine), go to
`http://178.105.232.196/admin/pipeline` → **NODES** tab → **REGISTER NODE**.

- Name: `contabo-win-1`
- Leave both capabilities ticked

**Copy the token immediately** — only its hash is stored and it can't be shown
again. (Lost it? Use ROTATE TOKEN.)

### 6d. Write the config

```powershell
notepad C:\faa\worker_service\config.json
```

Paste, substituting your token:

```json
{
  "server_url": "http://178.105.232.196",
  "token": "PASTE_THE_TOKEN_HERE",
  "temp_dir": "C:/faa/temp",
  "log_dir": "C:/faa/logs",
  "storage_root_override": null
}
```

Note: no `:8000` — the app is on port 80. Forward slashes in the paths.

### 6e. Verify the connection

```powershell
cd C:\faa
.\.venv\Scripts\python.exe -m worker_service.agent --once
```

Expect it to announce itself and report no work. Then check
**Pipeline → Nodes** in the dashboard — `contabo-win-1` should show
**ONLINE**.

If it errors, the message names the cause: a bad token gives 401, a wrong URL
gives a connection error.

---

## Phase 7 — Point the dashboard at the real paths

**Pipeline → PROCESSING**:

| Setting | Value |
|---|---|
| Photoshop executable | `C:/Program Files/Adobe/Adobe Photoshop 2023/Photoshop.exe` |
| FX plugin script path | `C:/Program Files/Adobe/Adobe Photoshop 2023/Real Paint FX/Scripts (actions)/Real-Paint-FX.jsx` |

**Pipeline → PROCESSING → Storage**:

| Setting | Value |
|---|---|
| Storage root | `S:/processed` |

Forward slashes throughout — they avoid escaping problems in the JSX. Click
**SAVE** on each section.

---

## Phase 8 — Test one stage at a time

Leave the agent running in a PowerShell window so it picks up jobs:

```powershell
cd C:\faa
.\.venv\Scripts\python.exe -m worker_service.agent
```

Then in the dashboard, **Pipeline → TEST & DEBUG**:

1. **Test Download** — enter any completed title's id. Proves the URL, token
   and file transfer. Should finish in seconds.
2. **Test Process** — enter one poster id. Proves Photoshop, the plugin, the
   script and the storage mount. Watch the Live Console: you should get output
   dimensions and a duration, and a new file under `S:\processed\_tests\`.

Test Upload comes later — it needs a marketplace account, and on a test server
you don't want to create real listings.

Each test streams a per-phase log. If one fails, the log names the phase and
the exact error.

---

## Phase 9 — Survive reboots

Photoshop **cannot** run as a service; it needs a logged-in desktop. So:

### 9a. Auto-logon

```powershell
netplwiz
```

Untick **Users must enter a user name and password to use this computer** →
Apply → enter the administrator password twice.

### 9b. Start the agent at logon

```powershell
notepad "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\agent.bat"
```

Paste:

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

### 9c. The habit that matters

**When you finish with RDP, click the X to disconnect. Never Log Off.**

Logging off ends the desktop session and Photoshop stops working. Disconnecting
leaves the session alive and the agent running.

Test it: reboot the VPS from the Contabo panel, wait a few minutes, and check
the node still reports **ONLINE** in the dashboard without you connecting.

---

## Updating the agent later

```powershell
cd C:\faa
curl.exe -L -o repo.zip https://github.com/zawarudo714/poster-downloader-test/archive/refs/heads/main.zip
Expand-Archive repo.zip -DestinationPath C:\faa\_repo -Force
Copy-Item C:\faa\_repo\poster-downloader-test-main\worker_service C:\faa\ -Recurse -Force
Remove-Item C:\faa\repo.zip, C:\faa\_repo -Recurse -Force
.\.venv\Scripts\python.exe -m pip install -r worker_service\requirements.txt
```

Stop the agent first (Ctrl+C in its window), then restart it.

Note this **only** updates the agent code. The script, selectors, timings,
templates and credentials all come from the dashboard at runtime — those never
need a redeploy here.

---

## Quick reference

```
C:\faa\
├── .venv\                    Python environment
├── worker_service\
│   ├── agent.py
│   └── config.json           server URL + token (the only local config)
├── temp\                     scratch, auto-cleaned
├── logs\                     agent_YYYY-MM-DD.log
└── profiles\                 Chrome profiles per marketplace account

S:\processed\                 the archive
S:\processed\_tests\          test output, never overwrites live files
```

**Where to look when something breaks**

| Symptom | Look at |
|---|---|
| Node shows OFFLINE | Is the agent window still open? `C:\faa\logs\` |
| Photoshop step fails | Live Console in Test & Debug; try the JSX by hand |
| "Image not readable" | `dir S:\processed` — the mount dropped |
| Uploads fail | Failures tab — the screenshot shows what the browser saw |
