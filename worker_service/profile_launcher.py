"""
Open one account's Chrome profile by hand.

════════════════════════════════════════════════════════════════════════════
WHAT IT IS FOR
════════════════════════════════════════════════════════════════════════════
Some marketplaces put a security check in front of the sign-in page. The
agent waits those out, but the reliable way to settle one is to sign in ONCE
as a human, in the very browser profile the agent will use afterwards — so
the clearance cookie and the session are already there when it runs.

Doing that from a command line means typing a long path with two flags that
have to be exactly right. Get either wrong and you have signed into a
different profile than the one the agent opens, which looks identical and
achieves nothing. Hence a list you click.

════════════════════════════════════════════════════════════════════════════
IT MUST AGREE WITH THE AGENT, SO IT ASKS THE AGENT
════════════════════════════════════════════════════════════════════════════
The profiles folder is NOT hardcoded here. It is derived from the same
`config.json` the agent reads, through the same rule — because a second copy
of "where do profiles live" is exactly how the launcher and the orphan
sweeper drifted apart and left the sweeper doing nothing for months.

Run it by double-clicking PROFILES.bat next to this file.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent


# ── Where things are ────────────────────────────────────────────────────────

def load_config() -> dict:
    """The agent's own config, so this tool cannot disagree with it."""
    for candidate in (HERE / "config.json", HERE.parent / "config.json"):
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return {}


def profiles_root(config: dict) -> Path:
    """
    Same rule the uploader uses: <temp_dir>/profiles.

    Imported from the uploader when possible so there is literally one
    definition; the copy below is only for running this file on its own.
    """
    try:
        from .uploader import profiles_root as real
        return real(config)
    except Exception:
        try:
            from uploader import profiles_root as real   # run as a loose script
            return real(config)
        except Exception:
            return Path(config.get("temp_dir", "C:/faa/temp")) / "profiles"


def find_chrome() -> str | None:
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ):
        if path and os.path.isfile(path):
            return path
    return None


def folder_size_mb(path: Path) -> int:
    try:
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // (1024 * 1024)
    except OSError:
        return 0


def in_use_by_chrome(profile: Path) -> bool:
    """
    Is a Chrome already holding this profile?

    Two browsers on one profile is the failure that cost a week of confused
    debugging, so it is worth refusing rather than discovering later.
    """
    try:
        import psutil
    except ImportError:
        return False
    key = os.path.normpath(str(profile)).lower()
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            if "chrome" not in (proc.info.get("name") or "").lower():
                continue
            if key in " ".join(proc.info.get("cmdline") or []).lower():
                return True
        except Exception:
            continue
    return False


def agent_running() -> bool:
    """The agent drives these same profiles; running both at once locks them."""
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            line = " ".join(proc.info.get("cmdline") or []).lower()
            if "worker_service.agent" in line or "worker_service\\agent.py" in line:
                return True
        except Exception:
            continue
    return False


# ── The window ──────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chrome profiles — sign in by hand")
        self.geometry("760x430")
        self.config_data = load_config()
        self.root_dir = profiles_root(self.config_data)
        self.chrome = find_chrome()

        tk.Label(self, text=str(self.root_dir), anchor="w",
                 fg="#555").pack(fill="x", padx=10, pady=(10, 0))

        self.warning = tk.Label(self, text="", anchor="w", fg="#a33",
                                wraplength=720, justify="left")
        self.warning.pack(fill="x", padx=10)

        columns = ("account", "size", "used")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=12)
        for key, label, width in (("account", "Account (folder)", 380),
                                  ("size", "Size", 90),
                                  ("used", "Last used", 200)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)
        self.tree.bind("<Double-1>", lambda _e: self.launch())

        row = tk.Frame(self)
        row.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(row, text="OPEN THIS PROFILE", command=self.launch,
                  height=2).pack(side="left")
        tk.Button(row, text="Refresh", command=self.refresh).pack(side="left", padx=6)
        tk.Label(row, fg="#555", justify="left",
                 text=("Sign in, clear any security check, then close Chrome "
                       "completely before starting the agent again.")
                 ).pack(side="left", padx=10)

        self.refresh()

    def refresh(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        notes = []
        if not self.chrome:
            notes.append("Chrome was not found in the usual places.")
        if agent_running():
            notes.append("The agent is RUNNING — stop it first, or Chrome and "
                         "the agent will fight over the same profile.")
        if not self.root_dir.is_dir():
            notes.append(f"No profiles folder yet at {self.root_dir}. It is "
                         f"created the first time the agent signs in.")
        self.warning.config(text="  ".join(notes))

        if not self.root_dir.is_dir():
            return
        folders = sorted((p for p in self.root_dir.iterdir() if p.is_dir()),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for path in folders:
            # Folders are named <account id>_<account name>. Only strip a
            # prefix that is actually an id — an older folder like
            # "TEST_WnB" has no id and splitting it would display "WnB".
            name = path.name
            pretty = re.sub(r"^\d+_", "", name).replace("_", " ") or name
            used = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self.tree.insert("", "end", iid=str(path),
                             values=(f"{pretty}   ({name})",
                                     f"{folder_size_mb(path)} MB", used))

    def launch(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Pick one", "Select a profile first.")
            return
        profile = Path(selection[0])

        if not self.chrome:
            messagebox.showerror(
                "Chrome not found",
                "Could not find chrome.exe in the usual places. Install Chrome, "
                "or launch it by hand with:\n\n"
                f'--user-data-dir="{profile}" --profile-directory=Default')
            return

        if in_use_by_chrome(profile):
            messagebox.showerror(
                "Already open",
                "A Chrome is already using this profile. Close it first — two "
                "browsers on one profile is what makes it fail to start.")
            return

        if agent_running() and not messagebox.askyesno(
                "The agent is running",
                "The agent is running and may open this same profile while you "
                "are using it, which locks it for both.\n\nOpen anyway?"):
            return

        # EXACTLY the two flags the agent uses for the profile. Getting either
        # wrong opens a different profile that looks identical and banks the
        # session somewhere the agent will never read.
        try:
            subprocess.Popen([self.chrome,
                              f"--user-data-dir={profile}",
                              "--profile-directory=Default"])
        except OSError as e:
            messagebox.showerror("Could not start Chrome", str(e))
            return

        messagebox.showinfo(
            "Chrome opening",
            "Sign in, clear any security check, and make sure you land on the "
            "account page.\n\nThen CLOSE EVERY CHROME WINDOW before starting "
            "the agent again.")


if __name__ == "__main__":
    App().mainloop()
