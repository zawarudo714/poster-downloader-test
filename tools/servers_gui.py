"""
Server launcher — click a button, get a terminal already logged in.

════════════════════════════════════════════════════════════════════════════
WHY A KEY AND NOT A SAVED PASSWORD
════════════════════════════════════════════════════════════════════════════
OpenSSH deliberately refuses to read a password from a pipe. Every "remember
my password" approach therefore ends up either shipping PuTTY's plink, or
typing into a prompt that appears "in a second or two" — which is a guess
that works until the network is slow. The deploy tool avoided the whole
problem by speaking the SSH protocol directly, but that gives you a command
runner, not a terminal you can work in.

A key solves it properly. `ssh root@host` just opens, in the real Windows
terminal, with colours and scrollback and everything else that makes a shell
usable — and there is no password in the loop at all.

It is also SAFER than what this replaces. A stored password can be replayed
by anything that can read it; a key lives in the Windows profile, and the
server can be told to stop trusting it in one line.

════════════════════════════════════════════════════════════════════════════
THE PASSWORD IS USED ONCE, TO END THE NEED FOR IT
════════════════════════════════════════════════════════════════════════════
Setting up a key needs one authenticated connection. That is the only time
this tool touches the password — it reads what the deploy and migration
tools already stored, installs the public key, verifies it works, and from
then on never needs it again.

Nothing is overwritten on the server: the key is APPENDED to
authorized_keys, and only if it is not already there. Wiping that file would
lock out anything else that uses it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from deploy_gui import (  # noqa: E402
    parse_ssh_target, protect_password, unprotect_password,
)

MIGRATE_SETTINGS = HERE / "migrate_settings.json"
DEPLOY_SETTINGS = HERE / "settings.json"
OWN_SETTINGS = HERE / "servers_settings.json"

KEY_PATH = Path.home() / ".ssh" / "id_ed25519"

SERVERS = [
    ("production", "PRODUCTION", "ssh root@178.105.34.144",
     "the real one — 101,605 titles, your worker, the payments"),
    ("test", "TEST BOX", "ssh root@178.105.232.196",
     "where everything is built and rehearsed"),
]

# Things typed over and over during this migration work. One click each,
# in a terminal that stays open so the output can be read and copied.
SHORTCUTS = [
    ("disk and docker", "df -h /; echo; docker system df"),
    ("reclaim build cache",
     "docker builder prune -af; echo; df -h /"),
    ("what version is live",
     "curl -fsS http://127.0.0.1/healthz; echo"),
    ("container status",
     "cd $(dirname $(ls -d /root/*/docker-compose.yml "
     "/opt/*/docker-compose.yml 2>/dev/null | head -1)) && docker compose ps"),
    ("tail the log",
     "cd $(dirname $(ls -d /root/*/docker-compose.yml "
     "/opt/*/docker-compose.yml 2>/dev/null | head -1)) && "
     "docker compose logs --tail 60 web"),
]


def load_known() -> dict:
    """
    Addresses and passwords already entered in the other two tools.

    Read rather than asked for again — the same fact typed into three
    windows is three chances to have one of them subtly wrong, and the one
    that is wrong is always the one you are not looking at.
    """
    out: dict = {}
    try:
        m = json.loads(MIGRATE_SETTINGS.read_text(encoding="utf-8"))
        out["production"] = {"target": m.get("prod", ""),
                             "password": unprotect_password(m.get("prod_pw", ""))}
        out["test"] = {"target": m.get("test", ""),
                       "password": unprotect_password(m.get("test_pw", ""))}
    except Exception:
        pass
    try:
        d = json.loads(DEPLOY_SETTINGS.read_text(encoding="utf-8"))
        target = d.get("ssh", "")
        if target and not out.get("test", {}).get("target"):
            out.setdefault("test", {})["target"] = target
    except Exception:
        pass
    try:
        own = json.loads(OWN_SETTINGS.read_text(encoding="utf-8"))
        for key, saved in own.items():
            row = out.setdefault(key, {})
            row.setdefault("target", saved.get("target", ""))
            if saved.get("password") and not row.get("password"):
                row["password"] = unprotect_password(saved["password"])
    except Exception:
        pass
    return out


def key_exists() -> bool:
    return KEY_PATH.is_file() and KEY_PATH.with_suffix(".pub").is_file()


def make_key() -> str:
    """
    Create the Windows key pair if there isn't one. Returns the public key.

    No passphrase: a passphrase would put a prompt back into every launch,
    which is the thing being removed. The key sits in the Windows profile
    and is only as reachable as the account it belongs to.
    """
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not key_exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-q",
             "-f", str(KEY_PATH)],
            check=True, capture_output=True, text=True)
    return KEY_PATH.with_suffix(".pub").read_text(encoding="utf-8").strip()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Servers — Print On Demand")
        root.geometry("880x560")
        self.known = load_known()
        self.rows: dict[str, dict] = {}

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)

        ttk.Label(frm, text="Click a server to open a terminal already "
                            "logged in.", foreground="#0a7").grid(
            row=0, column=0, sticky="w", pady=(0, 8))

        r = 1
        for key, label, default, blurb in SERVERS:
            box = ttk.LabelFrame(frm, text=label, padding=8)
            box.grid(row=r, column=0, sticky="ew", pady=5)
            box.columnconfigure(1, weight=1)

            target = tk.StringVar(
                value=self.known.get(key, {}).get("target") or default)
            password = tk.StringVar(
                value=self.known.get(key, {}).get("password", ""))

            ttk.Label(box, text=blurb, foreground="#888").grid(
                row=0, column=0, columnspan=4, sticky="w")
            ttk.Label(box, text="address").grid(row=1, column=0, sticky="w")
            ttk.Entry(box, textvariable=target).grid(row=1, column=1,
                                                     sticky="ew", padx=6)
            ttk.Label(box, text="password").grid(row=2, column=0, sticky="w")
            ttk.Entry(box, textvariable=password, show="•").grid(
                row=2, column=1, sticky="ew", padx=6)
            ttk.Label(box, text="only needed once, to install the key",
                      foreground="#888").grid(row=2, column=2, sticky="w")

            status = tk.StringVar(value="")
            ttk.Label(box, textvariable=status).grid(row=3, column=1,
                                                     sticky="w", padx=6)

            btns = ttk.Frame(box)
            btns.grid(row=1, column=3, rowspan=2, padx=6)
            ttk.Button(btns, text=f"OPEN {label}", width=20,
                       command=lambda k=key: self._launch(k)).pack(pady=2)
            ttk.Button(btns, text="Set up key login", width=20,
                       command=lambda k=key: self._setup(k)).pack(pady=2)

            self.rows[key] = {"target": target, "password": password,
                              "status": status}
            r += 1

        short = ttk.LabelFrame(frm, text="Or run one of these", padding=8)
        short.grid(row=r, column=0, sticky="ew", pady=8)
        for i, (label, command) in enumerate(SHORTCUTS):
            ttk.Label(short, text=label, width=22).grid(row=i, column=0,
                                                        sticky="w", pady=1)
            for j, (key, name, _d, _b) in enumerate(SERVERS):
                ttk.Button(short, text=f"on {name.lower()}", width=18,
                           command=lambda k=key, c=command:
                           self._launch(k, c)).grid(row=i, column=1 + j, padx=3)
        r += 1

        frm.rowconfigure(r, weight=1)
        self.log = tk.Text(frm, height=8, wrap="word", background="#101014",
                           foreground="#d8d8e0")
        self.log.grid(row=r, column=0, sticky="nsew", pady=(8, 0))
        self.log.tag_config("ok", foreground="#6fd08c")
        self.log.tag_config("err", foreground="#ff7b72")

        root.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh_status()

    # ── plumbing ────────────────────────────────────────────────────────

    def _emit(self, text: str, tag: str = "") -> None:
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")

    def _close(self) -> None:
        data = {}
        for key, row in self.rows.items():
            data[key] = {"target": row["target"].get(),
                         "password": protect_password(row["password"].get()) or ""}
        try:
            OWN_SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        self.root.destroy()

    def _refresh_status(self) -> None:
        for key, row in self.rows.items():
            row["status"].set(
                "key login ready" if key_exists() else
                "no key yet — press Set up key login once")

    # ── launching ───────────────────────────────────────────────────────

    def _launch(self, key: str, command: str = "") -> None:
        """
        Open a real terminal. Deliberately a NEW window, not a pane in here.

        A Tk text widget is a poor terminal — no colours, no arrow keys, no
        `less`. Handing the work to Windows Terminal means everything that
        normally works, works.
        """
        try:
            user, host, port = parse_ssh_target(self.rows[key]["target"].get())
        except ValueError as e:
            messagebox.showerror("Servers", str(e))
            return

        ssh = f"ssh -p {port} {user}@{host}"
        if command:
            # `-t` forces a terminal so anything interactive still behaves,
            # and the shell stays open afterwards so the output can be read
            # rather than flashing past.
            ssh = (f'ssh -t -p {port} {user}@{host} '
                   f'"{command}; echo; echo ---- done, press enter ----; read x"')

        title = f"{key} — {host}"
        subprocess.Popen(f'start "{title}" cmd /k {ssh}', shell=True)
        self._emit(f"opened {title}"
                   + (f" running: {command[:60]}" if command else ""))
        if not key_exists():
            self._emit("No key installed yet, so it will ask for the "
                       "password. Press 'Set up key login' once to stop that.",
                       "err")

    # ── the one-time setup ──────────────────────────────────────────────

    def _setup(self, key: str) -> None:
        threading.Thread(target=self._setup_worker, args=(key,),
                         daemon=True).start()

    def _setup_worker(self, key: str) -> None:
        row = self.rows[key]
        password = row["password"].get()
        if not password:
            self._emit("Enter the password once — it is only used to install "
                       "the key, and then never again.", "err")
            return
        try:
            import paramiko
        except ImportError:
            self._emit("paramiko is not installed. Run: pip install paramiko",
                       "err")
            return

        try:
            user, host, port = parse_ssh_target(row["target"].get())
            pub = make_key()
            self._emit(f"using {KEY_PATH}")

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(hostname=host, port=port, username=user,
                           password=password, timeout=30,
                           allow_agent=False, look_for_keys=False)
            try:
                # APPENDED, and only if absent. Rewriting authorized_keys
                # would lock out anything else that already uses it — and on
                # a box you can only reach over SSH, that is unrecoverable.
                marker = pub.split()[1][:32]
                cmd = (
                    "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                    "touch ~/.ssh/authorized_keys && "
                    "chmod 600 ~/.ssh/authorized_keys && "
                    f"grep -q '{marker}' ~/.ssh/authorized_keys "
                    f"&& echo ALREADY || "
                    f"(printf '%s\\n' '{pub}' >> ~/.ssh/authorized_keys "
                    f"&& echo ADDED)")
                _in, out, _err = client.exec_command(cmd, timeout=30)
                result = out.read().decode("utf-8", "replace").strip()
                self._emit(f"{host}: {result}")
            finally:
                client.close()

            # ── PROVE IT, DO NOT ASSUME IT ──────────────────────────────
            # Reconnecting with the key and nothing else is the only thing
            # that shows this worked. Reporting success on the strength of
            # the append having not errored is how you find out at the next
            # deploy instead.
            check = paramiko.SSHClient()
            check.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                check.connect(hostname=host, port=port, username=user,
                              key_filename=str(KEY_PATH), timeout=30,
                              allow_agent=False, look_for_keys=False,
                              password=None)
                _in, out, _err = check.exec_command("echo works", timeout=20)
                if out.read().decode().strip() == "works":
                    self._emit(f"{host}: key login verified — no password "
                               f"needed from now on.", "ok")
                else:
                    self._emit(f"{host}: connected with the key but the test "
                               f"command gave nothing back.", "err")
            finally:
                check.close()
        except Exception as e:
            self._emit(f"{type(e).__name__}: {e}", "err")
            self._emit("The password is still needed. Check it and try again.",
                       "err")
            return

        self.root.after(0, self._refresh_status)


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
