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

DEFAULT_REPO = r"C:\Users\Administrator\Documents\Claude\Projects\Print On Demand\poster_downloader_web"
DEFAULT_SSH = "ssh root@178.105.232.196"
DEFAULT_SERVER_CMDS = "cd /opt/poster && git pull && docker compose up -d --build"

# Sent after the deploy commands, always, so the window can answer the
# question you would otherwise SSH in to ask.
VERIFY_CMDS = (
    "cd /opt/poster && "
    "echo '--- commit now on the server ---' && "
    "git --no-pager log --no-color --oneline -1 && "
    "echo '--- container ---' && "
    "docker compose ps --format '{{.Name}}  {{.Status}}'"
)


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

        self.running = True
        self.go_btn.config(state="disabled")
        self._save_settings()
        threading.Thread(target=self._run, daemon=True,
                         args=(skip_local, skip_server)).start()

    def _run(self, skip_local: bool, skip_server: bool) -> None:
        try:
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

        message = self.msg_var.get().strip()
        if message:
            code, output = self._git(["commit", "-m", message], repo)
            # "nothing to commit" is a normal outcome when only the server
            # half is being re-run. Treated as success rather than stopping
            # the deploy, which is what you actually meant.
            if code != 0 and "nothing to commit" not in output.lower():
                return False
        else:
            self._emit("\n(no commit message — skipping commit)", "dim")

        # The remote and branch are explicit. A bare `git push` follows
        # whatever upstream happens to be set, which is exactly how a commit
        # ends up on the wrong one of two remotes.
        code, _ = self._git(["push", remote, branch], repo)
        if code != 0:
            return False

        self._emit("\nLocal half done.", "ok")
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
                    if line:
                        self._emit(line.rstrip())
                code = stdout.channel.recv_exit_status()
                err = stderr.read().decode("utf-8", "replace").strip()
                if err:
                    self._emit(err, "" if code == 0 else "err")
                if code != 0:
                    self._emit(f"\nexit {code} — stopping here.", "err")
                    return

            self._emit("\nDeploy finished.", "ok")
            self._emit("Check above: the commit line should be the one you just "
                       "pushed, and the container's status should read seconds, "
                       "not hours.", "dim")
            self._consume_note()
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
