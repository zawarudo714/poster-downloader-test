"""
Deploy tool — commit, push, and run the server-side commands, from one window.

════════════════════════════════════════════════════════════════════════════
WHY THIS EXISTS
════════════════════════════════════════════════════════════════════════════
A deploy is four steps in two different places, and every one of them has
bitten this project at least once:

  · pushing to the WRONG REMOTE, so the server pulls and reports
    "Already up to date" while nothing has changed
  · pulling without rebuilding, so the container keeps serving old code
  · typing an SSH password into a prompt that has not appeared yet
  · forgetting which of the two is the production box

So the window shows the remote it is about to push to, runs both halves in
order, stops at the first failure, and prints everything as it happens.

════════════════════════════════════════════════════════════════════════════
WHY paramiko AND NOT THE ssh COMMAND
════════════════════════════════════════════════════════════════════════════
You asked for the password prompt timing to be handled. The honest answer is
that it should not be handled — it should not exist.

Driving `ssh.exe` means waiting for a prompt that appears "in a second or
two", which is a guess that works until the network is slow. Worse, OpenSSH
deliberately refuses to read a password from a pipe, so it has to be fed
through a pseudo-terminal, which Windows makes awkward.

paramiko speaks the SSH protocol directly. The password is handed to the
authentication call as an argument. There is no prompt, so there is nothing
to time, and a slow network just means the connect takes longer.

════════════════════════════════════════════════════════════════════════════
THE PASSWORD IS NEVER WRITTEN DOWN
════════════════════════════════════════════════════════════════════════════
Everything else you type is remembered between runs, in settings.json next to
this file. The password is deliberately excluded — it is held in memory for
as long as the window is open and nowhere else. Tick "remember for this
session" if you would rather it stayed filled in between deploys; closing the
window still forgets it.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

HERE = Path(__file__).resolve().parent
SETTINGS_FILE = HERE / "settings.json"

# ════════════════════════════════════════════════════════════════════════════
#  THE DEPLOY NOTE
# ════════════════════════════════════════════════════════════════════════════
# next_deploy.txt is written by whoever made the changes, and read fresh every
# time this window opens. Its first line becomes the commit message; anything
# after a line of three dashes is added to the server commands for that deploy
# only.
#
# The point is that you should not have to know what changed in order to ship
# it. Open the window, type the password, press DEPLOY.
#
# It is CONSUMED on success — renamed to next_deploy.done.txt — so the same
# message can never be silently reused on a later, different deploy. If the
# file is missing, the window behaves exactly as it did before and you type
# your own message.
DEPLOY_NOTE = HERE / "next_deploy.txt"
DEPLOY_NOTE_DONE = HERE / "next_deploy.done.txt"

# ════════════════════════════════════════════════════════════════════════════
#  THE DEPLOY LOG
# ════════════════════════════════════════════════════════════════════════════
# One line per successful deploy, newest first, written only when the server
# is confirmed to be running what was pushed.
#
# It exists so a later session can answer "what is actually live?" by reading
# ONE SMALL FILE, instead of running git commands and reading their output.
# That is the whole design constraint: this file must stay cheap to read, so
# it is one line per deploy and capped — an unbounded log would eventually
# cost more to read than the thing it replaced.
#
# Nothing writes here on a failed deploy. A line in this file means the code
# reached the server; if the last line is older than the last change, the
# difference is exactly what has not shipped.
DEPLOY_LOG = HERE / "DEPLOY_LOG.md"
DEPLOY_LOG_KEEP = 30

DEPLOY_LOG_HEADER = """# Deploy log

What is actually live on the server, newest first. Written automatically by
the deploy tool, and only when the server was confirmed to be running the
commit that was just pushed.

**For a future session: read THIS file to see what shipped. Do not run git
log or diff to work it out — that costs far more to read than these lines.**
If the top entry looks older than the work in the repo, the difference is
what has not been deployed yet.

"""

DEFAULT_REPO = r"C:\Users\Administrator\Documents\Claude\Projects\Print On Demand\poster_downloader_web"
DEFAULT_SSH = "ssh root@178.105.232.196"
DEFAULT_SERVER_CMDS = "cd /opt/poster && git pull && docker compose up -d --build"

# Sent after the deploy commands, always, so the window can answer the
# question you would otherwise SSH in to ask.
VERIFY_CMDS = (
    "cd /opt/poster && "
    "echo '--- commit now on the server ---' && "
    "git --no-pager log --no-color --oneline -1 && "
    "echo 'SERVER_SHA=' $(git rev-parse HEAD) && "
    "echo '--- container ---' && "
    "docker compose ps --format '{{.Name}}  {{.Status}}' && "
    # ── ASK THE RUNNING APP WHAT VERSION IT IS ──────────────────────────
    #
    # The commit check above proves the code reached the DISK. It does not
    # prove the container was rebuilt with it — `git pull` can succeed while
    # the rebuild is skipped, fails, or quietly reuses a cached image, and
    # then every line on screen says success while the old code keeps
    # serving. That happened on 2026-08-24: the tool reported a clean
    # deploy, the SHA matched, and the site was four versions behind. It
    # cost a night's unattended run.
    #
    # ── DISK, EVERY TIME ────────────────────────────────────────────────
    #
    # Nothing was watching it. The test box reached 84% full from build cache
    # alone — forty deploys, nothing pruning — and the first symptom would
    # have been a deploy dying with "no space left" or SQLite failing a write
    # mid-transaction, at whatever hour it filled up.
    "echo '--- disk ---' && "
    "df -h / | tail -1 && "
    "echo \"SERVER_FREE_PCT=$(df --output=pcent / | tail -1 | tr -dc '0-9')\" && "
    "echo '--- what the running app says ---' && "
    # Retried because the container needs a moment to come up after a
    # rebuild; asking once would report a failure that is only earliness.
    "for i in $(seq 1 20); do "
    "  V=$(curl -fsS http://127.0.0.1/healthz 2>/dev/null) && break; "
    "  sleep 2; "
    "done; "
    "echo \"SERVER_HEALTHZ=$V\""
)

# Printed by the verify step so the tool can compare what the server is
# running against what was just pushed, rather than assuming that commands
# exiting zero means a deploy happened.
SERVER_SHA_MARKER = "SERVER_SHA="
SERVER_HEALTH_MARKER = "SERVER_HEALTHZ="
SERVER_FREE_MARKER = "SERVER_FREE_PCT="
# Above this, say so loudly. Docker build cache grows without
# limit and a full disk corrupts writes rather than refusing them.
DISK_WARN_PCT = 80


def local_app_version(repo: Path) -> str:
    """The APP_VERSION in the code about to be pushed, or '' if unreadable."""
    try:
        src = (repo / "app" / "config.py").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r"""^APP_VERSION\s*=\s*["']([^"']+)["']""", src, re.M)
    return m.group(1) if m else ""


def version_from_healthz(blob: str) -> str:
    """Pull the version out of /healthz, whatever else it carries."""
    m = re.search(r'"version"\s*:\s*"([^"]+)"', blob or "")
    return m.group(1) if m else ""


# ── Parsing what you typed ──────────────────────────────────────────────────

def parse_ssh_target(text: str) -> tuple[str, str, int]:
    """
    Accepts what you would type at a terminal and returns (user, host, port).

        ssh root@178.105.232.196
        root@178.105.232.196
        ssh -p 2222 root@example.com
        178.105.232.196                 -> assumes root

    Written to accept the whole `ssh ...` line because that is what is in
    your clipboard, and retyping it is where a wrong host comes from.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Enter an SSH target, e.g. ssh root@178.105.232.196")

    port = 22
    port_match = re.search(r"-p\s+(\d+)", text)
    if port_match:
        port = int(port_match.group(1))
        text = text[:port_match.start()] + text[port_match.end():]

    text = re.sub(r"^\s*ssh\s+", "", text.strip())
    text = re.sub(r"-[a-zA-Z]+\s+\S+", "", text).strip()
    text = text.split()[0] if text.split() else ""

    if "@" in text:
        user, host = text.split("@", 1)
    else:
        user, host = "root", text

    if not host:
        raise ValueError("Could not find a host in that SSH line.")
    return user, host, port


# Colour codes, cursor moves, progress-bar redraws. Docker Compose and git
# both emit these, and a Tk text widget has no idea what they mean — it prints
# them literally, which turns "did my commit land?" into an unreadable wall.
_ANSI = re.compile(r"\x1B\[[0-9;?]*[ -/]*[@-~]|\x1B[@-Z\\-_]|\x1B\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    """
    Plain text out of terminal output.

    Carriage returns are handled too, and deliberately by keeping only what
    comes AFTER the last one: a progress line rewrites itself in place, so the
    final state is the only part worth showing. Keeping all of it would print
    every intermediate percentage on its own line.
    """
    text = _ANSI.sub("", text)
    if "\r" in text:
        text = text.split("\r")[-1]
    return text.rstrip()


# ════════════════════════════════════════════════════════════════════════════
#  REMEMBERING THE PASSWORD
# ════════════════════════════════════════════════════════════════════════════
# Written through Windows DPAPI (CryptProtectData) rather than as plain text.
#
# The threat this actually addresses is not someone sitting at your desk —
# if they are there, they have your session anyway. It is the file LEAVING:
# settings.json sits inside a git repo, and a stray `git add -A` would put a
# root password in a public GitHub history forever. DPAPI ciphertext can only
# be decrypted by your Windows account on this machine, so the same accident
# leaks nothing usable.
#
# Reached through ctypes so there is no extra dependency to install. If the
# call is unavailable (not Windows), remembering is simply refused rather than
# silently falling back to plain text — quietly storing a root password as
# readable text is not a decision to make on someone's behalf.
def _dpapi(encrypt: bool, data: bytes) -> Optional[bytes]:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return None

    src = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                      ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect_password(plain: str) -> Optional[str]:
    blob = _dpapi(True, (plain or "").encode("utf-8"))
    return blob.hex() if blob else None


def unprotect_password(stored: str) -> str:
    if not stored:
        return ""
    try:
        blob = _dpapi(False, bytes.fromhex(stored))
    except Exception:
        return ""
    return blob.decode("utf-8", "replace") if blob else ""


def read_deploy_note() -> tuple[str, str]:
    """
    Return (commit_message, extra_server_commands) from next_deploy.txt.

        Fix the uploaded counter and the #A/#B titles
        ---
        cd /opt/poster && docker compose exec -T web python scripts/something.py

    Everything before the `---` is the message; everything after is extra
    server work for this deploy only. Both halves are optional, and a missing
    file is not an error — it just means nothing was prepared.
    """
    try:
        raw = DEPLOY_NOTE.read_text(encoding="utf-8")
    except Exception:
        return "", ""

    head, _sep, tail = raw.partition("\n---")
    lines = [l.strip() for l in head.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    return (lines[0] if lines else ""), tail.strip()


def split_commands(text: str) -> list[str]:
    """
    One command per line. Blank lines and #comments are dropped, and a
    trailing backslash joins a line to the next one so a long command can be
    pasted across several lines.
    """
    out, buffer = [], ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1].strip() + " "
            continue
        out.append((buffer + line).strip())
        buffer = ""
    if buffer.strip():
        out.append(buffer.strip())
    return out


# ── The window ──────────────────────────────────────────────────────────────

class DeployApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Deploy — Print On Demand")
        self.root.geometry("980x760")
        self.queue: queue.Queue = queue.Queue()
        self.running = False

        saved = self._load_settings()

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        row = 0

        # ── Repo ────────────────────────────────────────────────────────
        ttk.Label(frm, text="Local folder").grid(row=row, column=0, sticky="w", **pad)
        self.repo_var = tk.StringVar(value=saved.get("repo", DEFAULT_REPO))
        repo_box = ttk.Frame(frm)
        repo_box.grid(row=row, column=1, sticky="ew", **pad)
        repo_box.columnconfigure(0, weight=1)
        ttk.Entry(repo_box, textvariable=self.repo_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(repo_box, text="Browse", command=self._browse).grid(row=0, column=1, padx=(6, 0))
        row += 1

        # ── Remote + branch ─────────────────────────────────────────────
        # Shown because pushing to the wrong one of two remotes is the single
        # most expensive mistake available here: everything reports success
        # and the server never sees the change.
        ttk.Label(frm, text="Push to remote").grid(row=row, column=0, sticky="w", **pad)
        remote_box = ttk.Frame(frm)
        remote_box.grid(row=row, column=1, sticky="ew", **pad)
        self.remote_var = tk.StringVar(value=saved.get("remote", "origin"))
        self.remote_menu = ttk.Combobox(remote_box, textvariable=self.remote_var,
                                        width=18, state="readonly")
        self.remote_menu.grid(row=0, column=0, sticky="w")
        self.remote_hint = ttk.Label(remote_box, text="", foreground="#666")
        self.remote_hint.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(remote_box, text="Refresh",
                   command=self._load_remotes).grid(row=0, column=2, padx=(10, 0))
        row += 1

        # ── Commit message ──────────────────────────────────────────────
        ttk.Label(frm, text="Commit message").grid(row=row, column=0, sticky="w", **pad)
        note_msg, self.note_server = read_deploy_note()
        self.msg_var = tk.StringVar(value=note_msg)
        msg_box = ttk.Frame(frm)
        msg_box.grid(row=row, column=1, sticky="ew", **pad)
        msg_box.columnconfigure(0, weight=1)
        ttk.Entry(msg_box, textvariable=self.msg_var).grid(row=0, column=0, sticky="ew")
        self.note_hint = ttk.Label(
            msg_box,
            text="prepared for you" if note_msg else "",
            foreground="#2e7d32")
        self.note_hint.grid(row=0, column=1, padx=(10, 0))
        row += 1

        # ── SSH ─────────────────────────────────────────────────────────
        ttk.Label(frm, text="SSH").grid(row=row, column=0, sticky="w", **pad)
        self.ssh_var = tk.StringVar(value=saved.get("ssh", DEFAULT_SSH))
        ttk.Entry(frm, textvariable=self.ssh_var).grid(row=row, column=1, sticky="ew", **pad)
        row += 1

        ttk.Label(frm, text="Password").grid(row=row, column=0, sticky="w", **pad)
        pw_box = ttk.Frame(frm)
        pw_box.grid(row=row, column=1, sticky="ew", **pad)
        pw_box.columnconfigure(0, weight=1)
        self.pw_var = tk.StringVar(value=unprotect_password(saved.get("password", "")))
        ttk.Entry(pw_box, textvariable=self.pw_var, show="•").grid(row=0, column=0, sticky="ew")
        self.remember_var = tk.BooleanVar(value=bool(saved.get("password")))
        ttk.Checkbutton(pw_box, text="Remember", variable=self.remember_var
                        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Label(pw_box, text="locked to this Windows account",
                  foreground="#666").grid(row=0, column=2, padx=(6, 0))
        row += 1

        # ── Command boxes ───────────────────────────────────────────────
        ttk.Label(frm, text="Local (git)").grid(row=row, column=0, sticky="nw", **pad)
        self.local_text = tk.Text(frm, height=4, wrap="none")
        self.local_text.grid(row=row, column=1, sticky="ew", **pad)
        self.local_text.insert("1.0", saved.get("local_cmds", "git add -A"))
        row += 1

        ttk.Label(frm, text="On the server").grid(row=row, column=0, sticky="nw", **pad)
        self.server_text = tk.Text(frm, height=5, wrap="none")
        self.server_text.grid(row=row, column=1, sticky="ew", **pad)
        self.server_text.insert("1.0", saved.get("server_cmds", DEFAULT_SERVER_CMDS))
        if self.note_server:
            # Appended rather than replacing what is there: the normal pull and
            # rebuild still has to happen, and a one-off migration runs after
            # it, not instead of it.
            self.server_text.insert("end", "\n" + self.note_server)
        row += 1

        # ── Buttons ─────────────────────────────────────────────────────
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=1, sticky="w", **pad)
        self.go_btn = ttk.Button(btns, text="DEPLOY", command=self._start)
        self.go_btn.grid(row=0, column=0)
        ttk.Button(btns, text="Push only",
                   command=lambda: self._start(skip_server=True)).grid(row=0, column=1, padx=6)
        ttk.Button(btns, text="Server only",
                   command=lambda: self._start(skip_local=True)).grid(row=0, column=2)
        ttk.Button(btns, text="Clear log",
                   command=lambda: self._clear()).grid(row=0, column=3, padx=6)

        # ── WHAT IS ON THIS LAPTOP vs WHAT IS ACTUALLY SERVING ──────────
        #
        # Side by side and always visible, because the question "did my last
        # deploy land" was previously answerable only by reading scrollback
        # or opening an SSH session — and when it went unasked, two days of
        # work sat on the laptop while every screen said success.
        #
        # CHECK LIVE asks the server directly without deploying anything, so
        # it is safe to press at any time, including while wondering whether
        # to press DEPLOY at all.
        self.version_var = tk.StringVar(value="local —  ·  live —")
        ttk.Label(btns, textvariable=self.version_var).grid(
            row=0, column=4, padx=(18, 6))
        ttk.Button(btns, text="Check live",
                   command=self._start_version_check).grid(row=0, column=5)
        self._refresh_local_version()
        row += 1

        # ── Log ─────────────────────────────────────────────────────────
        frm.rowconfigure(row, weight=1)
        self.log = tk.Text(frm, wrap="word", background="#101014",
                           foreground="#d8d8e0", insertbackground="#d8d8e0")
        self.log.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=10, pady=(8, 4))
        scroll = ttk.Scrollbar(frm, command=self.log.yview)
        scroll.grid(row=row, column=2, sticky="ns", pady=(8, 4))
        self.log.configure(yscrollcommand=scroll.set)

        for tag, colour in (("ok", "#4ec98a"), ("err", "#e8554b"),
                            ("step", "#e8b84b"), ("dim", "#8a8799")):
            self.log.tag_configure(tag, foreground=colour)

        self.root.after(80, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Alt-tabbing back is exactly when a note written moments ago should
        # appear — the note is always written while this window sits in the
        # background. Bound on the ROOT only: child widgets raise FocusIn on
        # every click, and refilling then would fight whatever is being typed.
        self.root.bind(
            "<FocusIn>",
            lambda e: self._fill_message() if e.widget is self.root else None)
        self._fill_message()
        self._load_remotes()

    # ── Settings ────────────────────────────────────────────────────────

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _server_cmds_to_remember(self) -> str:
        """
        The server box MINUS anything a deploy note appended.

        A one-off migration must not become a permanent part of the deploy —
        it would run again on every future push, which for a script that
        deletes or rewrites rows is the kind of mistake you find out about
        much later.
        """
        text = self.server_text.get("1.0", "end").strip()
        if self.note_server and text.endswith(self.note_server):
            text = text[: -len(self.note_server)].strip()
        return text

    def _save_settings(self) -> None:
        payload = {
            "repo": self.repo_var.get(),
            "ssh": self.ssh_var.get(),
            "remote": self.remote_var.get(),
            "local_cmds": self.local_text.get("1.0", "end").strip(),
            "server_cmds": self._server_cmds_to_remember(),
        }

        if self.remember_var.get() and self.pw_var.get():
            sealed = protect_password(self.pw_var.get())
            if sealed:
                payload["password"] = sealed
            else:
                # DPAPI unavailable. The password is dropped rather than
                # written in the clear, and you are told — a "Remember" tick
                # that quietly did nothing would be worse than either.
                self._emit("Could not encrypt the password on this machine, "
                           "so it was not saved.", "err")

        try:
            SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self) -> None:
        self._save_settings()
        self.root.destroy()

    def _record_deploy(self, sha: str, message: str) -> None:
        """
        Append one line for a deploy that is confirmed live.

        Newest first so the useful line is the first one read, and capped at
        DEPLOY_LOG_KEEP so the file cannot grow into something expensive.
        """
        from datetime import datetime

        line = (f"- **{datetime.now():%Y-%m-%d %H:%M}** · `{sha[:8]}` · "
                f"{message or '(no message)'}")
        try:
            existing = DEPLOY_LOG.read_text(encoding="utf-8")
            entries = [l for l in existing.splitlines() if l.startswith("- ")]
        except Exception:
            entries = []

        # Same commit deployed twice (a rebuild, say) replaces its own entry
        # rather than adding a duplicate that says nothing new.
        entries = [e for e in entries if f"`{sha[:8]}`" not in e]
        entries.insert(0, line)

        try:
            DEPLOY_LOG.write_text(
                DEPLOY_LOG_HEADER + "\n".join(entries[:DEPLOY_LOG_KEEP]) + "\n",
                encoding="utf-8")
            self._emit(f"Logged to {DEPLOY_LOG.name}.", "dim")
        except Exception as e:
            self._emit(f"Could not write the deploy log: {e}", "err")

    def _consume_note(self) -> None:
        """
        Retire the deploy note once its deploy has actually succeeded.

        Only on success, and only after the server half finished — a note
        cleared on a failed run would leave you re-deploying with no message
        and no idea what was meant to go out.
        """
        if not DEPLOY_NOTE.exists():
            return
        try:
            DEPLOY_NOTE_DONE.write_text(DEPLOY_NOTE.read_text(encoding="utf-8"),
                                        encoding="utf-8")
            DEPLOY_NOTE.unlink()
            self._emit("Deploy note used up.", "dim")
            self.root.after(0, lambda: self.note_hint.config(text=""))
        except Exception:
            pass

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.repo_var.get() or ".")
        if chosen:
            self.repo_var.set(chosen)
            self._load_remotes()

    # ── Logging ─────────────────────────────────────────────────────────

    def _emit(self, text: str, tag: str = "") -> None:
        self.queue.put((strip_ansi(text), tag))

    def _drain(self) -> None:
        try:
            while True:
                text, tag = self.queue.get_nowait()
                self.log.insert("end", text + "\n", tag or ())
                self.log.see("end")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _clear(self) -> None:
        self.log.delete("1.0", "end")

    # ── Git remotes ─────────────────────────────────────────────────────

    def _load_remotes(self) -> None:
        repo = Path(self.repo_var.get())
        if not (repo / ".git").exists():
            self.remote_hint.config(text="not a git folder")
            return
        try:
            out = subprocess.run(["git", "remote", "-v"], cwd=str(repo),
                                 capture_output=True, text=True, timeout=15)
            names, urls = [], {}
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] not in names:
                    names.append(parts[0])
                    urls[parts[0]] = parts[1]
            self.remote_menu["values"] = names
            if self.remote_var.get() not in names and names:
                self.remote_var.set(names[0])

            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                    cwd=str(repo), capture_output=True,
                                    text=True, timeout=15).stdout.strip()
            self.branch = branch or "main"
            target = urls.get(self.remote_var.get(), "")
            self.remote_hint.config(text=f"{self.branch}  →  {target}")
        except Exception as e:
            self.remote_hint.config(text=str(e))

    # ── Running ─────────────────────────────────────────────────────────

    def _start(self, skip_local: bool = False, skip_server: bool = False) -> None:
        if self.running:
            return
        repo = Path(self.repo_var.get())
        if not skip_local and not (repo / ".git").exists():
            messagebox.showerror("Deploy", f"{repo} is not a git folder.")
            return
        if not skip_server and not self.pw_var.get():
            messagebox.showerror("Deploy", "Enter the SSH password.")
            return

        self._fill_message()
        self.running = True
        self.go_btn.config(state="disabled")
        self._save_settings()
        threading.Thread(target=self._run, daemon=True,
                         args=(skip_local, skip_server)).start()

    def _preflight(self, repo: Path) -> bool:
        """
        Mechanical checks before anything leaves this machine.

        Wired in HERE rather than left as something to remember, because a
        check you have to remember is a check that runs on the days you were
        already being careful. Every one of these caught something real; see
        tools/preflight.py.

        It only inspects the source — no database, no network — so it costs a
        few seconds and cannot itself break anything.
        """
        script = repo / "tools" / "preflight.py"
        if not script.is_file():
            return True                       # older checkout: nothing to run

        self._emit("\n$ python tools/preflight.py", "step")
        proc = subprocess.run([sys.executable, str(script)], cwd=str(repo),
                              capture_output=True, text=True)
        self._emit((proc.stdout + proc.stderr).strip(),
                   "" if proc.returncode == 0 else "err")
        if proc.returncode == 0:
            return True

        # Not a hard block. The owner sometimes needs to ship a fix while
        # something unrelated is mid-edit, and a tool that cannot be
        # overridden gets worked around instead of used.
        return messagebox.askyesno(
            "Preflight found problems",
            "The checks above found something that usually means a page will "
            "break.\n\nDeploy anyway?")

    def _run(self, skip_local: bool, skip_server: bool) -> None:
        try:
            if not skip_local and not self._preflight(Path(self.repo_var.get())):
                self._emit("\nSTOPPED — nothing was sent to the server.", "err")
                return
            if not skip_local and not self._run_local():
                self._emit("\nSTOPPED — nothing was sent to the server.", "err")
                return
            if not skip_server:
                self._run_server()
        except Exception as e:
            self._emit(f"\nUnexpected failure: {e}", "err")
        finally:
            self.running = False
            self.root.after(0, lambda: self.go_btn.config(state="normal"))

    # ── Local half ──────────────────────────────────────────────────────

    def _git(self, args: list[str], repo: Path) -> tuple[int, str]:
        self._emit(f"\n$ git {' '.join(args)}", "step")
        proc = subprocess.run(["git", *args], cwd=str(repo),
                              capture_output=True, text=True)
        output = (proc.stdout + proc.stderr).strip()
        if output:
            self._emit(output, "" if proc.returncode == 0 else "err")
        return proc.returncode, output

    # ── The version label ───────────────────────────────────────────────

    def _fill_message(self, event=None) -> None:
        """
        The commit box must NEVER be blank. Called on open, on focus, and
        again the moment DEPLOY is pressed.

        ════════════════════════════════════════════════════════════════════
        WHY A BLANK BOX IS THE PROBLEM, NOT THE MISSING NOTE
        ════════════════════════════════════════════════════════════════════
        Blank used to mean three different things and looked the same for
        all of them: no note was written, a note was written and consumed by
        the last deploy, or the note was written after this window opened.
        The third case was the common one — the note is written WHILE the
        changes are made, which is always after the window is already open.

        And blank used to mean "skip the commit", so two days of work sat on
        the laptop while every screen said success.

        Now the box always shows what WILL be committed. If there is a note
        it wins; otherwise the version number, which is a poor commit
        message and an excellent one compared to none. Anything typed by
        hand beats both — this only ever fills an EMPTY box.
        """
        if self.msg_var.get().strip():
            return
        note_msg, note_server = read_deploy_note()
        if note_msg:
            self.msg_var.set(note_msg)
            self.note_server = note_server
            return
        version = local_app_version(Path(self.repo_var.get()))
        if version:
            self.msg_var.set(f"deploy v{version}")

    def _refresh_local_version(self, live: str = "") -> None:
        """
        Redraw 'local X · live Y'. Called after a deploy and by CHECK LIVE.

        `live` is remembered between calls so a plain local refresh does not
        wipe an answer we already have — a label that forgets is a label you
        stop trusting.
        """
        if live:
            self._live_version = live
        local = local_app_version(Path(self.repo_var.get())) or "—"
        known = getattr(self, "_live_version", "") or "—"
        state = "  ✓" if (known == local and known != "—") else ""
        if known not in ("—", local) and known:
            state = "  ← NOT what you have"
        self.version_var.set(f"local {local}  ·  live {known}{state}")

    def _start_version_check(self) -> None:
        threading.Thread(target=self._check_live_version, daemon=True).start()

    def _check_live_version(self) -> None:
        """Ask the server what it is serving. Changes nothing."""
        try:
            import paramiko
        except ImportError:
            self._emit("\nparamiko is not installed. Run:  pip install paramiko",
                       "err")
            return
        try:
            user, host, port = parse_ssh_target(self.ssh_var.get())
        except ValueError as e:
            self._emit(f"\n{e}", "err")
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, port=port, username=user,
                           password=self.pw_var.get(), timeout=30,
                           allow_agent=False, look_for_keys=False)
            _in, out, _err = client.exec_command(
                "curl -fsS http://127.0.0.1/healthz", timeout=30)
            blob = out.read().decode("utf-8", "replace")
        except Exception as e:
            self._emit(f"\ncould not ask the server: {e}", "err")
            return
        finally:
            client.close()

        version = version_from_healthz(blob)
        if not version:
            # An older build has no version in /healthz, which is itself the
            # answer: whatever is running predates this check.
            self._emit(f"\nThe site answered but reported no version "
                       f"({blob.strip()[:80]}). It is running code older "
                       f"than the version check.", "err")
            self._refresh_local_version(live="older")
            return
        self._emit(f"\nThe site is serving version {version}.",
                   "ok" if version == local_app_version(
                       Path(self.repo_var.get())) else "err")
        self._refresh_local_version(live=version)

    def _run_local(self) -> bool:
        repo = Path(self.repo_var.get())
        remote = self.remote_var.get() or "origin"
        branch = getattr(self, "branch", "main")

        self._emit("═" * 70, "dim")
        self._emit(f"LOCAL — {repo}", "step")
        self._emit(f"pushing {branch} to {remote}", "dim")
        self._emit("═" * 70, "dim")

        for command in split_commands(self.local_text.get("1.0", "end")):
            code, _ = self._git(command.split()[1:] if command.startswith("git ")
                                else command.split(), repo)
            if code != 0:
                return False

        # ── NEVER PUSH STAGED WORK WITHOUT COMMITTING IT ─────────────────
        #
        # This used to say "(no commit message — skipping commit)" in dim
        # grey and carry on. `git add -A` had already staged everything, so
        # the push sent the PREVIOUS commit, the server pulled it, rebuilt
        # happily, and ran old code — while the next line said "Local half
        # done" in green. Two days of changes never left the laptop and
        # every screen reported success.
        #
        # The message box is empty on every launch by design, so "empty"
        # cannot be allowed to mean "silently do nothing". A version number
        # is a poor commit message and an excellent one compared to no
        # commit at all.
        message = self.msg_var.get().strip()
        staged = self._git(["diff", "--cached", "--quiet"], repo)[0] != 0
        if staged and not message:
            version = local_app_version(repo)
            message = f"deploy v{version}" if version else "deploy"
            self._emit(f"\nNo commit message given, and there are staged "
                       f"changes — committing as {message!r}.", "err")

        if message:
            code, output = self._git(["commit", "-m", message], repo)
            # "nothing to commit" is a normal outcome when only the server
            # half is being re-run. Treated as success rather than stopping
            # the deploy, which is what you actually meant.
            if code != 0 and "nothing to commit" not in output.lower():
                return False
        else:
            self._emit("\n(nothing staged — pushing the existing commit)",
                       "dim")

        # The remote and branch are explicit. A bare `git push` follows
        # whatever upstream happens to be set, which is exactly how a commit
        # ends up on the wrong one of two remotes.
        code, _ = self._git(["push", remote, branch], repo)
        if code != 0:
            return False

        # Recorded so the server half can prove it actually arrived.
        try:
            self.pushed_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=str(repo),
                capture_output=True, text=True, timeout=15).stdout.strip()
        except Exception:
            self.pushed_sha = ""

        self._emit(f"\nLocal half done — pushed {self.pushed_sha[:8]}.", "ok")
        return True

    # ── Server half ─────────────────────────────────────────────────────

    def _run_server(self) -> None:
        try:
            import paramiko
        except ImportError:
            self._emit("\nparamiko is not installed. Run:  pip install paramiko", "err")
            return

        try:
            user, host, port = parse_ssh_target(self.ssh_var.get())
        except ValueError as e:
            self._emit(f"\n{e}", "err")
            return

        self._emit("\n" + "═" * 70, "dim")
        self._emit(f"SERVER — {user}@{host}:{port}", "step")
        self._emit("═" * 70, "dim")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._emit("connecting…", "dim")
            client.connect(hostname=host, port=port, username=user,
                           password=self.pw_var.get(), timeout=30,
                           allow_agent=False, look_for_keys=False)
        except Exception as e:
            self._emit(f"could not connect: {e}", "err")
            return

        try:
            commands = split_commands(self.server_text.get("1.0", "end"))
            commands.append(VERIFY_CMDS)
            server_sha = ""
            server_health = ""
            server_used_pct = ""

            for command in commands:
                self._emit(f"\n$ {command}", "step")
                # Each command gets its own shell, so `cd` does NOT carry
                # over between lines. That is why the default is a single
                # chained line — write `cd /opt/poster && ...` on every line
                # that needs to be there.
                #
                # get_pty=False on purpose. With a pty, git and docker decide
                # they are talking to a terminal and emit colour codes and
                # progress-bar redraws; without one they print plain lines,
                # which is what this window can actually display. TERM=dumb
                # and --no-pager cover the tools that colour anyway.
                _stdin, stdout, stderr = client.exec_command(
                    f"export TERM=dumb GIT_PAGER=cat; {command}",
                    timeout=900, get_pty=False)
                for line in iter(stdout.readline, ""):
                    if not line:
                        continue
                    clean = strip_ansi(line)
                    if SERVER_SHA_MARKER in clean:
                        server_sha = clean.split(SERVER_SHA_MARKER, 1)[1].strip()
                        continue          # bookkeeping, not output worth showing
                    if SERVER_FREE_MARKER in clean:
                        server_used_pct = clean.split(
                            SERVER_FREE_MARKER, 1)[1].strip()
                        continue
                    if SERVER_HEALTH_MARKER in clean:
                        server_health = clean.split(
                            SERVER_HEALTH_MARKER, 1)[1].strip()
                        continue
                    self._emit(line.rstrip())
                code = stdout.channel.recv_exit_status()
                err = stderr.read().decode("utf-8", "replace").strip()
                if err:
                    self._emit(err, "" if code == 0 else "err")
                if code != 0:
                    self._emit(f"\nexit {code} — stopping here.", "err")
                    return

            self._emit("\nDeploy finished.", "ok")

            # ── Did the server actually move? ────────────────────────────
            # Commands exiting zero is not the same as a deploy happening.
            # `git pull` prints "Already up to date" and exits 0 when you
            # have pushed to a repo the server does not follow, and the
            # rebuild then runs happily on unchanged code — which is exactly
            # how a whole evening went out to the wrong remote while every
            # line on screen said success.
            # ── DOES THE RUNNING APP AGREE? ──────────────────────────────
            #
            # Checked BEFORE the commit comparison, because it is the
            # stronger claim and the one that was missing. The commit check
            # asks whether the code arrived; this asks whether the process
            # answering requests is actually running it. They can disagree,
            # and when they do it is always this one that is right.
            try:
                used = int(server_used_pct or 0)
            except ValueError:
                used = 0
            if used >= DISK_WARN_PCT:
                self._emit(
                    f"\nWARNING: the server disk is {used}% full. Docker build "
                    f"cache grows with every deploy and nothing prunes it. "
                    f"Reclaim it with:  docker builder prune -af", "err")

            want_version = local_app_version(Path(self.repo_var.get()))
            live_version = version_from_healthz(server_health)
            version_ok = True

            if not server_health:
                self._emit(
                    "\nWARNING: the site did not answer /healthz, so I cannot "
                    "tell you which version is live. It may still be starting "
                    "— reload the page and check.", "err")
                version_ok = False
            elif not live_version:
                self._emit(
                    f"\nThe site answered but reported no version: "
                    f"{server_health[:120]}", "err")
                self._emit(
                    "That means it is running code older than this check — "
                    "which is itself the answer: the deploy did not land.",
                    "err")
                version_ok = False
            elif want_version and live_version != want_version:
                self._emit(
                    f"\nWARNING: you deployed version {want_version} but the "
                    f"site is serving version {live_version}.", "err")
                self._emit(
                    "The code reached the server and the container did not "
                    "pick it up. Usually the rebuild was skipped or failed. "
                    "Try:  cd /opt/poster && docker compose up -d --build "
                    "--force-recreate", "err")
                version_ok = False
            elif live_version:
                self._emit(f"Site is serving version {live_version} — matches "
                           f"the code you pushed.", "ok")

            # The label is updated whichever way it went, so the window keeps
            # showing the truth rather than the last good news.
            self._refresh_local_version(live=live_version or "older")

            pushed = getattr(self, "pushed_sha", "")
            if pushed and server_sha:
                if ((server_sha.startswith(pushed[:8])
                     or pushed.startswith(server_sha[:8])) and version_ok):
                    self._emit(f"Server is running {server_sha[:8]} — matches "
                               f"what you pushed.", "ok")
                    # Recorded only here, inside the branch that PROVED the
                    # server moved — the commit AND the running version. A
                    # log that also recorded failed attempts would be worse
                    # than none: it would answer "what is live?" with things
                    # that are not.
                    self._record_deploy(server_sha, self.msg_var.get().strip())
                    self._consume_note()
                elif not version_ok:
                    self._emit(
                        "\nNOT recorded as deployed — the commit is on the "
                        "server but the running site does not match it.", "err")
                else:
                    self._emit(
                        f"\nWARNING: you pushed {pushed[:8]} but the server is "
                        f"running {server_sha[:8]}.", "err")
                    self._emit(
                        "Your changes are NOT live. The usual cause is pushing "
                        "to a remote the server does not pull from — check the "
                        "remote dropdown against `git remote -v` on the server.",
                        "err")
            else:
                # Server-only run, or nothing to compare. The note is left
                # alone rather than guessed at.
                self._emit("Container status should read seconds, not hours.", "dim")
        finally:
            client.close()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    DeployApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
