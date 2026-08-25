"""
Migration rehearsal — one window. Run: MIGRATE.bat

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════
The production box runs version 15 from June: TEN tables, sourcing only, no
pipeline at all. The test box runs the current code with thirty-odd tables.
Production holds the irreplaceable half — 101,605 titles, 10,086 saved
posters, 15 payment runs, and a worker who is still adding to it daily.

Bringing those together has never been done, and it is one-way. So this tool
does NOT migrate production. It REHEARSES the migration, as many times as it
takes, on a throwaway copy — and production is only ever read.

════════════════════════════════════════════════════════════════════════════
WHY A REHEARSAL AND NOT A ONE-CLICK MIGRATOR
════════════════════════════════════════════════════════════════════════════
A tool that runs ONCE, on data that cannot be recreated, is the worst kind of
software: its bugs are catastrophic and, by definition, untested. Automating
the typing does not reduce that risk, it hides it.

What genuinely helps is being able to run the dangerous part over and over
somewhere it does not matter, and to compare the before and after precisely
rather than by eye. That is what this is.

════════════════════════════════════════════════════════════════════════════
THE THIRD STACK
════════════════════════════════════════════════════════════════════════════
    test box
      /opt/poster             your working system, port 80 — NEVER TOUCHED
      /opt/poster-rehearsal   throwaway, port 8081, its own database

The 49 MB database always travels. The 8.5 GB of posters is OPTIONAL and is
copied server to server, because both boxes sit in the same datacentre.

That used to be impossible: the test box had 9 GB free, because the
Dockerfile ended with `COPY . .` and there was no .dockerignore, so every
build packaged the whole data folder into the image — 25 GB of build cache
from forty deploys. With that fixed the box has 32 GB free and the real
tree fits, which is the only way to rehearse the workspace reshape against
anything other than fabricated folders.

Skip the posters and everything still works except the galleries. The
report says which of the two was done rather than leaving you to guess.

════════════════════════════════════════════════════════════════════════════
ONE STEP PER BUTTON
════════════════════════════════════════════════════════════════════════════
Every step reports what it found and stops on the first failure. Nothing
runs automatically in sequence, because a sequence that keeps going after a
surprise is how you end up three steps past the thing that went wrong.

The password fields, SSH parsing and log pane are borrowed from the deploy
tool rather than rewritten — two copies of "how do we talk to a server"
would drift, and the deploy tool's version is the one that has been used in
anger.
"""

from __future__ import annotations

import io
import json
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Reused deliberately — see the module docstring.
from deploy_gui import (  # noqa: E402
    parse_ssh_target, protect_password, strip_ansi, unprotect_password,
)

SETTINGS_FILE = HERE / "migrate_settings.json"
REPORT_FILE = HERE / "MIGRATION_REHEARSAL.md"

DEFAULT_PROD = "ssh root@178.105.34.144"
DEFAULT_TEST = "ssh root@178.105.232.196"

# Where things live. Discovered rather than assumed where possible, but these
# are the answers measured on 2026-08-24 and they are the sensible defaults.
PROD_DIR = "/root/poster-downloader"
TEST_DIR = "/opt/poster"
REHEARSAL_DIR = "/opt/poster-rehearsal"
REHEARSAL_PORT = 8081

# ════════════════════════════════════════════════════════════════════════════
#  WHAT TRAVELS FROM THE TEST BOX
# ════════════════════════════════════════════════════════════════════════════
# The whole reason for going this direction rather than moving 8.5 GB of
# posters: everything worth keeping from the test box is a few thousand rows.
#
# `upload_accounts` carries passwords encrypted with PIPELINE_SECRET, so that
# secret must travel too or every one of them becomes undecryptable. That is
# the single most important fact in this file.
SETTINGS_TABLES = [
    ("upload_accounts",  "marketplace accounts, with their encrypted passwords"),
    ("account_projects", "which projects each account serves"),
    ("wall_paths",       "the recorded mouse paths — tedious to redo"),
    ("app_settings",     "every tuned setting, and the API keys"),
    ("worker_nodes",     "the worker machine's token"),
]
OPTIONAL_TABLES = [
    ("store_listings",   "the TeePublic catalogue — 1,543 designs and their history"),
    ("store_scan_runs",  "past TeePublic sweeps"),
]

# Tables whose row counts are compared before and after the upgrade. If a
# number moves, the migration did something to real data and we want to know
# before it happens to the only copy.
WATCH_TABLES = [
    "master_titles", "saved_posters", "users", "payment_runs",
    "revisions", "activity_log", "app_settings",
]


# ════════════════════════════════════════════════════════════════════════════
#  THINGS THAT DO NOT NEED A SERVER — so they can be tested
# ════════════════════════════════════════════════════════════════════════════

def compose_file(port: int) -> str:
    """
    The rehearsal stack's compose file.

    Deliberately NOT a copy of the repo's own: a different port so it cannot
    collide with the working system, its own ./data so it shares no state,
    and no restart policy — a throwaway must not come back after a reboot.
    """
    return f"""# Generated by tools/migrate_gui.py — THROWAWAY REHEARSAL STACK.
#
# Not the real deployment. Different port, its own data directory, and no
# restart policy on purpose: this must never survive a reboot or be mistaken
# for the working system.
services:
  web:
    build: .
    ports:
      - "{port}:8000"
    environment:
      - TZ=Africa/Nairobi
      - APP_TZ=Africa/Nairobi
      - SESSION_SECRET=rehearsal-only-not-a-secret
      - PIPELINE_SECRET=${{PIPELINE_SECRET:?PIPELINE_SECRET must match the test box}}
      - DATABASE_URL=sqlite:////app/data/poster.db
      - WORKSPACE_DIR=/app/data/workspace
      - BACKUPS_DIR=/app/data/backups
    volumes:
      - ./data:/app/data
"""


def count_script(tables: list[str]) -> str:
    """A tiny python program that prints row counts as JSON. Read-only."""
    return (
        "import json,sqlite3,sys\n"
        "con=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True)\n"
        "have={r[0] for r in con.execute("
        "\"SELECT name FROM sqlite_master WHERE type='table'\")}\n"
        f"want={tables!r}\n"
        "out={'_tables':sorted(have)}\n"
        "for t in want:\n"
        "    out[t]=con.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] "
        "if t in have else None\n"
        "print(json.dumps(out))\n"
    )


def compare_counts(before: dict, after: dict) -> list[tuple[str, str, str]]:
    """
    (table, verdict, detail) for every watched table plus the table list.

    A row count that MOVED during a schema migration is the thing worth
    stopping for. A table that appeared is expected — that is most of what
    this upgrade does — so it is reported as information rather than alarm.
    """
    out: list[tuple[str, str, str]] = []

    was = set(before.get("_tables") or [])
    now = set(after.get("_tables") or [])
    for name in sorted(now - was):
        out.append((name, "new", "table created by the upgrade"))
    for name in sorted(was - now):
        out.append((name, "LOST", "table existed before and does not now"))

    for table in WATCH_TABLES:
        b, a = before.get(table), after.get(table)
        if b is None and a is None:
            continue
        if b is None:
            out.append((table, "new", f"{a} rows, created by the upgrade"))
        elif a is None:
            out.append((table, "LOST", f"had {b} rows, table is gone"))
        elif a == b:
            out.append((table, "same", f"{a} rows"))
        else:
            out.append((table, "CHANGED",
                        f"{b} rows before, {a} after ({a - b:+d})"))
    return out


def verdict_of(rows: list[tuple[str, str, str]]) -> tuple[bool, str]:
    """(is it safe, one sentence). LOST or CHANGED means stop."""
    bad = [r for r in rows if r[1] in ("LOST", "CHANGED")]
    if bad:
        return False, (f"{len(bad)} table(s) lost rows or changed count — "
                       f"do NOT run this against production yet.")
    created = sum(1 for r in rows if r[1] == "new")
    return True, (f"No existing row counts moved. {created} table(s) were "
                  f"created by the upgrade, which is what it is for.")


# ════════════════════════════════════════════════════════════════════════════
#  THE WINDOW
# ════════════════════════════════════════════════════════════════════════════

STEPS = [
    ("1. LOOK AT BOTH SERVERS",
     "Read-only. Versions, disk, and what is in each database.",
     "_step_survey"),
    ("2. BACK UP PRODUCTION AND COPY IT OVER",
     "Takes a fresh consistent backup, brings it to this laptop, then puts "
     "it on the test box. Production is only read.",
     "_step_backup"),
    ("3. BUILD THE REHEARSAL STACK",
     "Creates /opt/poster-rehearsal on the TEST box with its own database on "
     "port 8081. Your working system is untouched.",
     "_step_build"),
    ("4. COPY THE POSTER FILES  (optional)",
     "8.5 GB of saved posters, straight from one server to the other. "
     "Without this the galleries are empty but everything else works.",
     "_step_workspace"),
    ("5. UPGRADE IT AND COMPARE",
     "Starts it, lets the migrations run, then compares every row count "
     "before and after. This is the answer we came for.",
     "_step_upgrade"),
    ("6. BRING THE SETTINGS ACROSS",
     "Copies accounts, mouse paths and settings from the test box into the "
     "rehearsal, then proves a password still decrypts.",
     "_step_settings"),
    ("7. PROMOTE IT TO YOUR TEST BOX",
     "Makes the rehearsed copy your everyday test system, so you build and "
     "click against real data. The old one is kept.",
     "_step_promote"),
    ("8. START OVER",
     "Deletes the rehearsal stack so the next run begins from a clean copy. "
     "Nothing else is touched.",
     "_step_reset"),
]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Migration rehearsal — Print On Demand")
        root.geometry("1150x820")

        self.q: queue.Queue = queue.Queue()
        self.running = False
        self.state: dict = {}
        saved = self._load()

        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        pad = {"padx": 4, "pady": 3}
        row = 0

        banner = ("Production is only ever READ. Everything else happens in a "
                  "throwaway stack on the test box.")
        ttk.Label(frm, text=banner, foreground="#0a7").grid(
            row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1

        self.prod_var = tk.StringVar(value=saved.get("prod", DEFAULT_PROD))
        self.prod_pw = tk.StringVar(value=unprotect_password(
            saved.get("prod_pw", "")))
        row = self._server_row(frm, row, "PRODUCTION (read-only)",
                               self.prod_var, self.prod_pw, pad)

        self.test_var = tk.StringVar(value=saved.get("test", DEFAULT_TEST))
        self.test_pw = tk.StringVar(value=unprotect_password(
            saved.get("test_pw", "")))
        row = self._server_row(frm, row, "TEST BOX", self.test_var,
                               self.test_pw, pad)

        self.remember = tk.BooleanVar(value=bool(saved.get("prod_pw")))
        ttk.Checkbutton(frm, text="Remember both passwords on this Windows "
                                  "account", variable=self.remember).grid(
            row=row, column=1, sticky="w", **pad)
        row += 1

        ttk.Label(frm, text="PIPELINE_SECRET").grid(row=row, column=0,
                                                    sticky="w", **pad)
        self.secret_var = tk.StringVar(value=unprotect_password(
            saved.get("secret", "")))
        ttk.Entry(frm, textvariable=self.secret_var, show="•").grid(
            row=row, column=1, sticky="ew", **pad)
        ttk.Label(frm, text="from the test box's .env — the accounts' "
                            "passwords are encrypted with it",
                  foreground="#888").grid(row=row, column=2, sticky="w", **pad)
        row += 1

        ttk.Label(frm, text="Optional extras").grid(row=row, column=0,
                                                    sticky="w", **pad)
        self.extras = tk.BooleanVar(value=bool(saved.get("extras", True)))
        ttk.Checkbutton(
            frm, variable=self.extras,
            text="also bring the TeePublic catalogue across (1,543 designs "
                 "and their history)").grid(row=row, column=1, sticky="w", **pad)
        row += 1

        steps = ttk.LabelFrame(frm, text="Run these in order", padding=8)
        steps.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        steps.columnconfigure(1, weight=1)
        self.buttons = []
        for i, (label, blurb, fn) in enumerate(STEPS):
            btn = ttk.Button(steps, text=label, width=34,
                             command=lambda f=fn: self._go(f))
            btn.grid(row=i, column=0, sticky="w", pady=2)
            ttk.Label(steps, text=blurb, foreground="#888",
                      wraplength=720, justify="left").grid(
                row=i, column=1, sticky="w", padx=10)
            self.buttons.append(btn)
        row += 1

        frm.rowconfigure(row, weight=1)
        self.log = tk.Text(frm, wrap="word", background="#101014",
                           foreground="#d8d8e0", insertbackground="#d8d8e0")
        self.log.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        for tag, colour in (("step", "#7fd1ff"), ("ok", "#6fd08c"),
                            ("err", "#ff7b72"), ("dim", "#7a7a88")):
            self.log.tag_config(tag, foreground=colour)

        root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(80, self._drain)
        self._emit("Ready. Step 1 changes nothing — start there.", "dim")

    def _server_row(self, frm, row, label, target_var, pw_var, pad):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=target_var).grid(row=row, column=1,
                                                     sticky="ew", **pad)
        row += 1
        ttk.Label(frm, text="password").grid(row=row, column=0, sticky="w",
                                             **pad)
        ttk.Entry(frm, textvariable=pw_var, show="•").grid(
            row=row, column=1, sticky="ew", **pad)
        return row + 1

    # ── plumbing ────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self) -> None:
        data = {"prod": self.prod_var.get(), "test": self.test_var.get(),
                "extras": self.extras.get()}
        if self.remember.get():
            data["prod_pw"] = protect_password(self.prod_pw.get()) or ""
            data["test_pw"] = protect_password(self.test_pw.get()) or ""
            data["secret"] = protect_password(self.secret_var.get()) or ""
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=2),
                                     encoding="utf-8")
        except OSError:
            pass

    def _close(self) -> None:
        self._save()
        self.root.destroy()

    def _emit(self, text: str, tag: str = "") -> None:
        self.q.put((text, tag))

    def _drain(self) -> None:
        while True:
            try:
                text, tag = self.q.get_nowait()
            except queue.Empty:
                break
            self.log.insert("end", text + "\n", tag)
            self.log.see("end")
        self.root.after(80, self._drain)

    def _go(self, fn_name: str) -> None:
        if self.running:
            return
        self.running = True
        for b in self.buttons:
            b.config(state="disabled")
        self._save()

        def work():
            try:
                getattr(self, fn_name)()
            except Exception as e:
                self._emit(f"\nSTOPPED — {type(e).__name__}: {e}", "err")
            finally:
                self.running = False
                self.root.after(0, lambda: [b.config(state="normal")
                                            for b in self.buttons])
        threading.Thread(target=work, daemon=True).start()

    # ── talking to a server ─────────────────────────────────────────────

    def _connect(self, which: str):
        import paramiko
        target = (self.prod_var if which == "prod" else self.test_var).get()
        password = (self.prod_pw if which == "prod" else self.test_pw).get()
        if not password:
            raise RuntimeError(f"Enter the {which} password first.")
        user, host, port = parse_ssh_target(target)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=port, username=user,
                       password=password, timeout=30,
                       allow_agent=False, look_for_keys=False)
        return client

    def _run(self, client, command: str, *, quiet: bool = False,
             timeout: int = 900) -> tuple[int, str]:
        """One command, its own shell. Returns (exit code, output)."""
        if not quiet:
            self._emit(f"$ {command[:150]}", "dim")
        _in, out, err = client.exec_command(
            f"export TERM=dumb GIT_PAGER=cat; {command}", timeout=timeout)
        text = strip_ansi(out.read().decode("utf-8", "replace"))
        code = out.channel.recv_exit_status()
        stderr = strip_ansi(err.read().decode("utf-8", "replace")).strip()
        if stderr and not quiet:
            self._emit(stderr, "" if code == 0 else "err")
        return code, text + ("\n" + stderr if stderr else "")

    def _must(self, client, command: str, why: str, **kw) -> str:
        code, text = self._run(client, command, **kw)
        if code != 0:
            raise RuntimeError(f"{why} — exit {code}\n{text.strip()[:400]}")
        return text

    def _header(self, title: str) -> None:
        self._emit("\n" + "═" * 74, "dim")
        self._emit(title, "step")
        self._emit("═" * 74, "dim")

    # ════════════════════════════════════════════════════════════════════
    #  STEP 1 — look, change nothing
    # ════════════════════════════════════════════════════════════════════

    def _step_survey(self) -> None:
        self._header("1. LOOKING AT BOTH SERVERS — nothing is changed")

        for which, folder in (("prod", PROD_DIR), ("test", TEST_DIR)):
            client = self._connect(which)
            try:
                name = "PRODUCTION" if which == "prod" else "TEST BOX"
                self._emit(f"\n── {name} ──", "step")

                found = self._must(
                    client,
                    "ls -d /root/*/docker-compose.yml /opt/*/docker-compose.yml "
                    "2>/dev/null | head -1",
                    "could not find the project folder", quiet=True).strip()
                if not found:
                    raise RuntimeError(f"No docker-compose.yml found on {name}.")
                folder = str(Path(found).parent).replace("\\", "/")
                self.state[f"{which}_dir"] = folder
                self._emit(f"folder: {folder}")

                version = self._run(
                    client,
                    f"grep -m1 '^APP_VERSION' {folder}/app/config.py "
                    "| cut -d'\"' -f2", quiet=True)[1].strip()
                self._emit(f"code version: {version or '(unreadable)'}")

                disk = self._run(client, "df -h / | tail -1", quiet=True)[1]
                self._emit(f"disk: {' '.join(disk.split())}")

                db = self._run(
                    client,
                    f"ls -1 {folder}/data/poster.db {folder}/poster.db "
                    "2>/dev/null | head -1", quiet=True)[1].strip()
                if not db:
                    raise RuntimeError(f"No poster.db found under {folder}.")
                self.state[f"{which}_db"] = db

                counts = self._counts(client, db)
                self._emit(f"database: {db}")
                self._emit(f"tables: {len(counts.get('_tables') or [])}")
                for table in WATCH_TABLES:
                    if counts.get(table) is not None:
                        self._emit(f"   {table:<16} {counts[table]:,}")
                self.state[f"{which}_counts"] = counts
            finally:
                client.close()

        # ── THE ONE NUMBER THAT DECIDES THE WHOLE APPROACH ──────────────
        prod = self.state.get("prod_counts", {})
        titles = prod.get("master_titles") or 0
        if titles < 50_000:
            self._emit(
                f"\nWARNING: production shows only {titles:,} master titles. "
                f"That is far fewer than the 101,605 on record — check you "
                f"are pointed at the right server before going further.", "err")
        else:
            self._emit(f"\nProduction holds {titles:,} titles and "
                       f"{prod.get('saved_posters') or 0:,} saved posters. "
                       f"That is the irreplaceable half.", "ok")
        self._emit("Nothing was changed. Step 2 when you are ready.", "dim")

    def _counts(self, client, db_path: str) -> dict:
        """Row counts straight out of a sqlite file, opened read-only."""
        script = count_script(WATCH_TABLES).replace("'", "'\\''")
        code, text = self._run(
            client, f"python3 -c '{script}' {db_path}", quiet=True)
        if code != 0:
            raise RuntimeError(f"could not read {db_path}\n{text[:300]}")
        return json.loads(text.strip().splitlines()[-1])

    # ════════════════════════════════════════════════════════════════════
    #  STEP 2 — back up production, bring it here, put it there
    # ════════════════════════════════════════════════════════════════════

    def _step_backup(self) -> None:
        self._header("2. BACKING UP PRODUCTION AND COPYING IT OVER")
        prod_db = self.state.get("prod_db")
        if not prod_db:
            raise RuntimeError("Run step 1 first — I need to know where "
                               "production's database is.")

        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        remote = f"/tmp/poster-rehearsal-{stamp}.db"
        local = HERE / f"poster-rehearsal-{stamp}.db"

        client = self._connect("prod")
        try:
            # sqlite's own backup API, not `cp`. A plain copy of a live
            # database can catch it mid-write; this takes a consistent
            # snapshot with the app still running and nobody logged out.
            self._emit("taking a consistent snapshot (the site stays up)…")
            self._must(client, (
                f"python3 -c \"import sqlite3;"
                f"s=sqlite3.connect('{prod_db}');"
                f"d=sqlite3.connect('{remote}');"
                f"s.backup(d);d.close();s.close();print('ok')\""),
                "the snapshot failed")

            size = self._run(client, f"stat -c %s {remote}", quiet=True)[1].strip()
            self._emit(f"snapshot: {int(size):,} bytes", "ok")

            self._emit(f"downloading to {local.name} …")
            sftp = client.open_sftp()
            try:
                sftp.get(remote, str(local))
            finally:
                sftp.close()
            self._run(client, f"rm -f {remote}", quiet=True)
        finally:
            client.close()

        self._emit(f"a copy is now on this laptop: {local}", "ok")
        self._emit("That copy is yours to keep — it is the off-server backup "
                   "this system did not have.", "dim")

        client = self._connect("test")
        try:
            self._must(client, f"mkdir -p {REHEARSAL_DIR}/data",
                       "could not create the rehearsal folder")
            self._emit("uploading to the test box…")
            sftp = client.open_sftp()
            try:
                sftp.put(str(local), f"{REHEARSAL_DIR}/data/poster.db")
            finally:
                sftp.close()
            there = self._run(
                client, f"stat -c %s {REHEARSAL_DIR}/data/poster.db",
                quiet=True)[1].strip()
            if there != size:
                raise RuntimeError(
                    f"the copy does not match: {size} bytes on production, "
                    f"{there} on the test box")
            self._emit(f"copied and verified — {int(there):,} bytes both "
                       f"ends.", "ok")
        finally:
            client.close()

        self.state["backup_local"] = str(local)
        self.state["backup_size"] = size

    # ════════════════════════════════════════════════════════════════════
    #  STEP 3 — stand up the throwaway stack
    # ════════════════════════════════════════════════════════════════════

    def _step_build(self) -> None:
        self._header("3. BUILDING THE REHEARSAL STACK ON THE TEST BOX")
        if not self.secret_var.get().strip():
            raise RuntimeError(
                "PIPELINE_SECRET is empty. It is on the test box in "
                "/opt/poster/.env — without it the accounts' passwords "
                "cannot be decrypted and step 5 proves nothing.")

        client = self._connect("test")
        try:
            live = self.state.get("test_dir") or TEST_DIR
            if REHEARSAL_DIR.rstrip("/") == live.rstrip("/"):
                raise RuntimeError("The rehearsal folder and your working "
                                   "system are the same path. Refusing.")

            have = self._run(client, f"test -f {REHEARSAL_DIR}/data/poster.db "
                                     "&& echo yes", quiet=True)[1].strip()
            if have != "yes":
                raise RuntimeError("No database in the rehearsal folder — "
                                   "run step 2 first.")

            self._emit("copying the CODE from your working system…")
            # Everything except data/ and .git — the rehearsal builds the same
            # image from the same source, so a code difference cannot be
            # what makes the rehearsal disagree with the real thing.
            self._must(client, (
                f"cd {live} && tar --exclude=./data --exclude=./.git "
                f"--exclude=./backups -cf - . | tar -xf - -C {REHEARSAL_DIR}"),
                "could not copy the code")

            version = self._run(
                client, f"grep -m1 '^APP_VERSION' {REHEARSAL_DIR}/app/config.py"
                        " | cut -d'\"' -f2", quiet=True)[1].strip()
            self._emit(f"rehearsing an upgrade to version {version}", "ok")

            compose = compose_file(REHEARSAL_PORT).replace("'", "'\\''")
            self._must(client,
                       f"cat > {REHEARSAL_DIR}/docker-compose.yml <<'YAML'\n"
                       f"{compose_file(REHEARSAL_PORT)}YAML",
                       "could not write the compose file")
            self._must(client,
                       f"printf 'PIPELINE_SECRET=%s\\n' "
                       f"'{self.secret_var.get().strip()}' "
                       f"> {REHEARSAL_DIR}/.env",
                       "could not write .env")

            # ── A FABRICATED WORKSPACE, AND IT IS SAID OUT LOUD ─────────
            #
            # The real tree is 8.5 GB and would not fit. The startup
            # migration renames ONE directory per worker, so a handful of
            # folders exercises exactly the same code path — but the report
            # must say this was a sample, not the real thing.
            self._emit("making a small fake workspace to exercise the "
                       "folder reshape…")
            self._must(client, (
                f"cd {REHEARSAL_DIR} && python3 - <<'PY'\n"
                "import os,sqlite3\n"
                "con=sqlite3.connect('data/poster.db')\n"
                "try:\n"
                "    rows=con.execute('SELECT DISTINCT user_id FROM "
                "saved_posters LIMIT 5').fetchall()\n"
                "except Exception: rows=[]\n"
                "names={r[0] for r in rows}\n"
                "users={}\n"
                "try:\n"
                "    users={u[0]:u[1] for u in con.execute("
                "'SELECT id,username FROM users')}\n"
                "except Exception: pass\n"
                "made=0\n"
                "for uid in names:\n"
                "    u=users.get(uid,'worker%s'%uid)\n"
                "    p=os.path.join('data','workspace',u,'2026-01-01','1. Sample')\n"
                "    os.makedirs(p,exist_ok=True)\n"
                "    open(os.path.join(p,'1_Sample.png'),'wb').write(b'x')\n"
                "    made+=1\n"
                "print('fabricated %d worker folder(s)'%made)\n"
                "PY"), "could not fabricate the workspace")

            before = self._counts(client, f"{REHEARSAL_DIR}/data/poster.db")
            self.state["before"] = before
            self.state["built"] = True
            self._emit(f"\nbefore the upgrade: "
                       f"{len(before.get('_tables') or [])} tables", "ok")
            for table in WATCH_TABLES:
                if before.get(table) is not None:
                    self._emit(f"   {table:<16} {before[table]:,}")
            self._emit("\nReady. Step 4 runs the upgrade.", "dim")
        finally:
            client.close()

    # ════════════════════════════════════════════════════════════════════
    #  STEP 4 — the posters, server to server
    # ════════════════════════════════════════════════════════════════════

    def _step_workspace(self) -> None:
        """
        8.5 GB of saved posters, production straight to the test box.

        ════════════════════════════════════════════════════════════════════
        NOT VIA THE LAPTOP
        ════════════════════════════════════════════════════════════════════
        Both machines are in the same Hetzner datacentre, so server to server
        is minutes. Down to a home connection and back up again is an evening
        — and the laptop gains nothing by being in the middle.

        That needs the TEST box to be able to reach production, which it
        cannot today. So a key is installed for the copy and REMOVED
        afterwards: leaving it would mean anyone who got into the test box
        could walk into production, which is a poor trade for saving one
        step next time.
        """
        self._header("4. COPYING THE POSTER FILES")
        prod_dir = self.state.get("prod_dir") or PROD_DIR
        if not self.state.get("before") and not self.state.get("built"):
            self._emit("Run step 3 first — there is nowhere to put them.",
                       "err")
            return

        source = f"{prod_dir}/data/workspace"
        prod = self._connect("prod")
        test = self._connect("test")
        marker = ""
        try:
            size = self._run(prod, f"du -sh {source} 2>/dev/null | cut -f1",
                             quiet=True)[1].strip()
            if not size:
                self._emit(f"No workspace at {source} — nothing to copy.",
                           "err")
                return
            free = self._run(test, "df -BG --output=avail / | tail -1",
                             quiet=True)[1].strip()
            self._emit(f"{size} to copy · {free} free on the test box")

            self._emit("giving the test box a key to production, for this "
                       "copy only…")
            pub = self._must(test, (
                "test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N '' -q "
                "-f ~/.ssh/id_ed25519; cat ~/.ssh/id_ed25519.pub"),
                "could not make a key on the test box").strip().splitlines()[-1]
            marker = pub.split()[1][:32]
            self._must(prod, (
                "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
                f"grep -q '{marker}' ~/.ssh/authorized_keys || "
                f"printf '%s\\n' '{pub}' >> ~/.ssh/authorized_keys"),
                "could not authorise the test box on production")

            user, host, _port = parse_ssh_target(self.prod_var.get())
            self._emit(f"copying {size} — this is the long part…")
            # rsync when it is there because it can be re-run without
            # starting over; tar when it is not, so nothing has to be
            # installed on a machine mid-migration.
            code, text = self._run(test, (
                f"mkdir -p {REHEARSAL_DIR}/data/workspace && "
                f"if command -v rsync >/dev/null; then "
                f"  rsync -a -e 'ssh -o StrictHostKeyChecking=no' "
                f"    {user}@{host}:{source}/ {REHEARSAL_DIR}/data/workspace/ "
                f"    && echo COPIED_WITH_RSYNC; "
                f"else "
                f"  ssh -o StrictHostKeyChecking=no {user}@{host} "
                f"    'tar -C {prod_dir}/data -cf - workspace' "
                f"  | tar -C {REHEARSAL_DIR}/data -xf - && echo COPIED_WITH_TAR; "
                f"fi"), timeout=3600)
            if code != 0 or "COPIED_WITH" not in text:
                raise RuntimeError(f"the copy failed\n{text.strip()[:400]}")
            self._emit(text.strip().splitlines()[-1], "ok")

            # ── COUNT BOTH ENDS. "It finished" is not "it arrived." ─────
            here = self._run(test, f"find {REHEARSAL_DIR}/data/workspace "
                                   "-type f | wc -l", quiet=True)[1].strip()
            there = self._run(prod, f"find {source} -type f | wc -l",
                              quiet=True)[1].strip()
            if here != there:
                self._emit(f"WARNING: {there} files on production, {here} "
                           f"here. Run this step again — rsync will only "
                           f"fetch what is missing.", "err")
            else:
                self._emit(f"{int(here):,} files, both ends. Matched.", "ok")
                self.state["workspace_copied"] = True
        finally:
            if marker:
                # Removed whether the copy worked or not. A key left behind
                # by a failure is exactly the one nobody remembers.
                self._run(prod, (
                    f"sed -i '/{marker}/d' ~/.ssh/authorized_keys && "
                    f"echo removed"), quiet=True)
                self._emit("test box's access to production removed again.",
                           "dim")
            prod.close()
            test.close()

    # ════════════════════════════════════════════════════════════════════
    #  STEP 5 — the actual question
    # ════════════════════════════════════════════════════════════════════

    def _step_upgrade(self) -> None:
        self._header("5. UPGRADING THE REHEARSAL AND COMPARING")
        if not self.state.get("before"):
            raise RuntimeError("Run step 3 first — I need the before counts.")

        client = self._connect("test")
        try:
            self._emit("building and starting (this takes a couple of "
                       "minutes the first time)…")
            code, text = self._run(
                client, f"cd {REHEARSAL_DIR} && docker compose up -d --build "
                        "2>&1 | tail -25", timeout=1800)
            self._emit(text.strip())
            if code != 0:
                raise RuntimeError("the rehearsal stack would not start")

            self._emit("\nwaiting for it to answer…")
            healthz = self._run(client, (
                f"for i in $(seq 1 30); do "
                f"V=$(curl -fsS http://127.0.0.1:{REHEARSAL_PORT}/healthz "
                f"2>/dev/null) && break; sleep 2; done; echo \"$V\""),
                quiet=True)[1].strip()
            if not healthz:
                logs = self._run(
                    client, f"cd {REHEARSAL_DIR} && docker compose logs "
                            "--tail 40 web", quiet=True)[1]
                self._emit(logs.strip(), "err")
                raise RuntimeError(
                    "it never answered. The log above is what the upgrade "
                    "said on its way down — that is the real answer.")
            self._emit(f"it answered: {healthz}", "ok")

            self._emit("\nwhat the startup migrations said:")
            logs = self._run(
                client, f"cd {REHEARSAL_DIR} && docker compose logs web 2>&1 "
                        "| grep -iE 'migrat|added column|created|project|"
                        "workspace|ERROR|Traceback' | head -40", quiet=True)[1]
            self._emit(logs.strip() or "(nothing worth reporting)", "dim")

            after = self._counts(client, f"{REHEARSAL_DIR}/data/poster.db")
            rows = compare_counts(self.state["before"], after)
            ok, sentence = verdict_of(rows)

            self._emit("\n── BEFORE AND AFTER ──", "step")
            for table, kind, detail in rows:
                tag = {"LOST": "err", "CHANGED": "err", "new": "dim"}.get(kind, "")
                self._emit(f"  {kind:<8} {table:<20} {detail}", tag)
            self._emit(f"\n{sentence}", "ok" if ok else "err")

            self.state["after"] = after
            self.state["compare"] = rows
            self._write_report(healthz, rows, ok, sentence)
        finally:
            client.close()

    def _write_report(self, healthz, rows, ok, sentence) -> None:
        """
        The report is the DELIVERABLE, not the green tick.

        Written to a file rather than left in a window, because the decision
        it informs — whether to touch production — happens later and possibly
        in a different session.
        """
        lines = [
            "# Migration rehearsal", "",
            f"Run {datetime.now():%Y-%m-%d %H:%M}. The rehearsal stack "
            f"answered `{healthz}`.", "",
            f"**{sentence}**", "",
            "| | table | detail |", "|---|---|---|",
        ]
        lines += [f"| {k} | `{t}` | {d} |" for t, k, d in rows]
        lines += [
            "", "## What this did NOT prove", "",
            ("* The workspace reshape ran against the REAL poster tree."
             if self.state.get("workspace_copied") else
             "* The workspace reshape ran against a handful of FABRICATED "
             "folders, not the real tree. One directory rename per worker is "
             "the same code path, but the real tree has not moved. Run step "
             "4 to test it properly."),
            "* Nothing here touched production. Its database was copied and "
            "read; the machine was not changed.",
            "* A clean rehearsal says the schema survives. It does not say "
            "the site behaves — click around the rehearsal on port "
            f"{REHEARSAL_PORT} before trusting it.",
        ]
        try:
            REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
            self._emit(f"\nWritten to {REPORT_FILE.name}", "ok")
        except OSError as e:
            self._emit(f"could not write the report: {e}", "err")

    # ════════════════════════════════════════════════════════════════════
    #  STEP 6 — the settings, and whether passwords survive
    # ════════════════════════════════════════════════════════════════════

    def _step_settings(self) -> None:
        self._header("6. BRINGING THE SETTINGS ACROSS")
        tables = [t for t, _ in SETTINGS_TABLES]
        if self.extras.get():
            tables += [t for t, _ in OPTIONAL_TABLES]

        client = self._connect("test")
        try:
            live_db = self.state.get("test_db")
            if not live_db:
                raise RuntimeError("Run step 1 first.")
            target = f"{REHEARSAL_DIR}/data/poster.db"

            self._emit("copying these tables from your working system:")
            for name, why in SETTINGS_TABLES + (
                    OPTIONAL_TABLES if self.extras.get() else []):
                self._emit(f"   {name:<18} {why}", "dim")

            script = (
                "import sqlite3,sys\n"
                "src=sqlite3.connect(sys.argv[1]);dst=sqlite3.connect(sys.argv[2])\n"
                f"tables={tables!r}\n"
                "have={r[0] for r in dst.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\")}\n"
                "srch={r[0] for r in src.execute("
                "\"SELECT name FROM sqlite_master WHERE type='table'\")}\n"
                "for t in tables:\n"
                "    if t not in have or t not in srch:\n"
                "        print('SKIP %s (missing one side)'%t); continue\n"
                "    cols=[r[1] for r in dst.execute('PRAGMA table_info(%s)'%t)]\n"
                "    scols=[r[1] for r in src.execute('PRAGMA table_info(%s)'%t)]\n"
                "    use=[c for c in cols if c in scols]\n"
                "    rows=src.execute('SELECT %s FROM %s'%(','.join(use),t)).fetchall()\n"
                "    dst.execute('DELETE FROM %s'%t)\n"
                "    dst.executemany('INSERT INTO %s (%s) VALUES (%s)'%("
                "t,','.join(use),','.join('?'*len(use))),rows)\n"
                "    print('%-18s %d rows'%(t,len(rows)))\n"
                "dst.commit()\n"
            ).replace("'", "'\\''")

            out = self._must(
                client, f"python3 -c '{script}' {live_db} {target}",
                "the settings copy failed")
            self._emit(out.strip())

            # ── DOES A PASSWORD STILL DECRYPT? ──────────────────────────
            #
            # The whole point. Account passwords are encrypted with
            # PIPELINE_SECRET; if that does not travel intact, every account
            # is a row of unreadable text and the uploads stop with a
            # confusing error weeks later.
            self._emit("\nproving an account password still decrypts…")
            code, text = self._run(client, (
                f"cd {REHEARSAL_DIR} && docker compose exec -T web python - "
                "<<'PY'\n"
                "from app.db import SessionLocal\n"
                "from app.models import UploadAccount\n"
                "from app import pipeline as P\n"
                "db=SessionLocal()\n"
                "a=db.query(UploadAccount).first()\n"
                "if a is None: print('NO ACCOUNTS'); raise SystemExit(0)\n"
                # decrypt_secret is the real name, LOOKED UP rather than
                # guessed. An earlier draft invented two other names, neither
                # of which exists — so the one check whose entire job is
                # proving passwords survive would have failed for the wrong
                # reason and been believed. See check_tool_calls_exist in
                # preflight.py, which now catches exactly this.
                "try:\n"
                "    pw=P.decrypt_secret(a.password_enc)\n"
                "    print('OK %s -> %d characters'%(a.name,len(pw or '')))\n"
                "except Exception as e:\n"
                "    print('FAILED %s: %s'%(a.name,e))\n"
                "db.close()\n"
                "PY"))
            self._emit(text.strip(), "ok" if "OK " in text else "err")
            if "OK " not in text:
                self._emit(
                    "If that failed, PIPELINE_SECRET does not match the one "
                    "that encrypted these passwords. Fix it before the real "
                    "migration — afterwards means re-typing all of them.",
                    "err")

            self._emit("\nDisabling every account in the rehearsal, so this "
                       "throwaway can never touch a marketplace.", "dim")
            self._run(client, (
                f"cd {REHEARSAL_DIR} && docker compose exec -T web python -c "
                "\"from app.db import SessionLocal;from app.models import "
                "UploadAccount;db=SessionLocal();"
                "n=db.query(UploadAccount).update({UploadAccount.is_enabled:0});"
                "db.commit();print('disabled %d account(s)'%n)\""))
            self._emit(
                f"\nOpen http://{parse_ssh_target(self.test_var.get())[1]}:"
                f"{REHEARSAL_PORT}/ and click around. That is the last thing "
                f"this tool cannot do for you.", "ok")
        finally:
            client.close()

    # ════════════════════════════════════════════════════════════════════
    #  STEP 7 — make the rehearsal your everyday test system
    # ════════════════════════════════════════════════════════════════════

    def _step_promote(self) -> None:
        """
        Swap the rehearsed data into the test stack you already use.

        ════════════════════════════════════════════════════════════════════
        WHY SWAP RATHER THAN RUN BOTH
        ════════════════════════════════════════════════════════════════════
        Two stacks on one box means one worker machine having to be pointed
        at one of them, two deploy targets, and accounts that are live in one
        and disabled in the other. An hour spent testing the wrong one is the
        obvious outcome.

        After this there is still exactly one test system, at the same
        address, with the same DEPLOY button — it simply now contains
        101,605 real titles instead of nineteen rows.

        ════════════════════════════════════════════════════════════════════
        NOTHING IS DELETED
        ════════════════════════════════════════════════════════════════════
        The current database and workspace are RENAMED, not removed. A move
        within one filesystem is instant and costs no space, and it means
        this is reversible while you are still deciding whether you like it.
        """
        self._header("7. PROMOTING THE REHEARSAL TO YOUR TEST BOX")
        live = self.state.get("test_dir") or TEST_DIR
        if not self.state.get("after"):
            raise RuntimeError("Run step 5 first — I will not promote a copy "
                               "whose upgrade has not been checked.")
        rows = self.state.get("compare") or []
        ok, sentence = verdict_of(rows)
        if not ok:
            raise RuntimeError(
                f"Refusing: the last comparison said — {sentence}")

        if not messagebox.askyesno(
                "Promote", (
                    f"Make the rehearsed copy your everyday test system?\n\n"
                    f"{live} keeps its current database and posters under a "
                    f"'.before-promote' name, so this can be undone.\n\n"
                    f"Production is not touched.")):
            self._emit("cancelled", "dim")
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        client = self._connect("test")
        try:
            self._emit("stopping the test stack…")
            self._run(client, f"cd {live} && docker compose down 2>&1 | tail -3")

            self._emit("setting the current data aside (renamed, not "
                       "deleted)…")
            self._must(client, (
                f"cd {live}/data && "
                f"[ -f poster.db ] && mv poster.db poster.db.before-promote-{stamp} "
                f"|| true; "
                f"[ -d workspace ] && mv workspace workspace.before-promote-{stamp} "
                f"|| true; ls -1"), "could not set the old data aside")

            self._emit("moving the rehearsed data into place…")
            self._must(client, (
                f"mv {REHEARSAL_DIR}/data/poster.db {live}/data/poster.db && "
                f"if [ -d {REHEARSAL_DIR}/data/workspace ]; then "
                f"  mv {REHEARSAL_DIR}/data/workspace {live}/data/workspace; "
                f"else mkdir -p {live}/data/workspace; fi && echo moved"),
                "could not move the rehearsed data")

            self._emit("starting it again…")
            self._must(client, f"cd {live} && docker compose up -d 2>&1 | tail -5",
                       "the test stack would not start")

            health = self._run(client, (
                "for i in $(seq 1 30); do "
                "V=$(curl -fsS http://127.0.0.1/healthz 2>/dev/null) && break; "
                "sleep 2; done; echo \"$V\""), quiet=True)[1].strip()
            if not health:
                raise RuntimeError(
                    "it did not come back up. Put the old data back with:\n"
                    f"  cd {live}/data && mv poster.db.before-promote-{stamp} "
                    f"poster.db")
            self._emit(f"it answered: {health}", "ok")

            counts = self._counts(client, f"{live}/data/poster.db")
            self._emit("\nyour test box now holds:", "ok")
            for table in WATCH_TABLES:
                if counts.get(table) is not None:
                    self._emit(f"   {table:<16} {counts[table]:,}")

            self._emit(
                f"\nDone. Same address, same DEPLOY button, real data.\n"
                f"The old contents are at {live}/data/*.before-promote-{stamp} "
                f"— delete them once you are happy.", "ok")
            self._emit(
                "Every marketplace account is DISABLED if step 6 ran. Turn "
                "the ones you want back on before testing uploads.", "dim")
        finally:
            client.close()

    # ════════════════════════════════════════════════════════════════════
    #  STEP 8 — start again
    # ════════════════════════════════════════════════════════════════════

    def _step_reset(self) -> None:
        self._header("8. DELETING THE REHEARSAL STACK")
        live = self.state.get("test_dir") or TEST_DIR
        # ── THE CALLER WOULD NEVER SEND THAT, AND IT CHECKS ANYWAY ──────
        # A destructive instruction is checked by the thing carrying it out.
        # "rm -rf" with a path computed somewhere else is exactly the shape
        # that deletes a working system.
        if REHEARSAL_DIR.rstrip("/") in ("", "/", "/opt", "/root",
                                         live.rstrip("/")):
            raise RuntimeError(f"Refusing to delete {REHEARSAL_DIR}.")
        if not messagebox.askyesno(
                "Start again",
                f"Delete {REHEARSAL_DIR} on the test box?\n\n"
                f"Your working system at {live} is not touched. The next run "
                f"starts from a fresh copy of production."):
            self._emit("cancelled", "dim")
            return

        client = self._connect("test")
        try:
            self._run(client, f"cd {REHEARSAL_DIR} && docker compose down "
                              "-v 2>&1 | tail -5")
            self._must(client, f"rm -rf {REHEARSAL_DIR}",
                       "could not delete the folder")
            self._emit("gone. Step 2 to begin again.", "ok")
        finally:
            client.close()
        self.state.pop("before", None)
        self.state.pop("after", None)


def main() -> int:
    try:
        import paramiko  # noqa: F401
    except ImportError:
        print("paramiko is not installed. Run:  pip install paramiko")
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
