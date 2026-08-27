#!/usr/bin/env python3
"""
Local development setup — one click wipes everything and rebuilds from scratch.

    python scripts/dev_setup.py            # GUI
    python scripts/dev_setup.py --cli      # headless, same work
    python scripts/dev_setup.py --cli --help

════════════════════════════════════════════════════════════════════════════
THIS IS DESTRUCTIVE AND THAT IS THE POINT
════════════════════════════════════════════════════════════════════════════
Every run deletes poster.db, the workspace tree and the backups folder, then
recreates a known-good state. There is no partial/merge mode — the whole value
is that "click setup" always lands you somewhere identical, so a broken
experiment is never something you have to unpick.

It refuses to run against a database that looks like production (see
`_looks_like_production`). That check is the only thing standing between a
misplaced double-click and 101,605 real rows, so do not weaken it.

════════════════════════════════════════════════════════════════════════════
WHY IT USES THE APP'S OWN CODE
════════════════════════════════════════════════════════════════════════════
Passwords go through app.auth.hash_password, tables through app.db.init_db,
folder names through app.parsing, pipeline state through app.pipeline. Nothing
is reimplemented here. If the app changes how it hashes or where it writes,
this tool follows automatically instead of quietly seeding data the app can't
read.

════════════════════════════════════════════════════════════════════════════
WHAT YOU GET
════════════════════════════════════════════════════════════════════════════
  admin   / 123456     (role: admin)
  worker1 / 123456     (role: worker)

Plus, optionally: master titles to claim, finished work with real image files
on disk, a registered pipeline worker node, a demo marketplace account, and
that finished work already greenlit so the Pipeline tab has something in it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

# Make `app` importable however this script is invoked.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ═════════════════════════════════════════════════════════════════════════
#  DEFAULTS
# ═════════════════════════════════════════════════════════════════════════

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "123456"
WORKER_USERNAME = "worker1"
WORKER_PASSWORD = "123456"

# A production database has ~100k titles. Anything near that is not a dev box.
PRODUCTION_TITLE_THRESHOLD = 5_000

DEFAULT_OPTIONS = {
    "master_titles":    300,   # rows in the work queue
    "completed_titles": 12,    # finished titles with real files on disk
    "posters_per_title": 3,
    "seed_worker_node": True,
    "seed_upload_account": True,
    "greenlight_seeded": True,
    "seed_payment_run": True,
}


# ═════════════════════════════════════════════════════════════════════════
#  PLACEHOLDER IMAGE GENERATION
# ═════════════════════════════════════════════════════════════════════════
#
# Real image files matter: the admin gallery reads dimensions from the file
# header to flag sub-800px posters, and the pipeline's Photoshop stage needs
# something openable. So we write genuine PNGs rather than empty files.
#
# Hand-rolled with zlib + struct to avoid adding Pillow as a dependency for a
# dev-only tool.

# Distinct hues so posters are visually tellable apart in the gallery.
_PALETTE = [
    (198, 122, 40), (72, 118, 168), (150, 72, 130), (86, 148, 96),
    (176, 92, 74), (108, 104, 176), (168, 152, 60), (92, 140, 150),
]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def make_placeholder_png(width: int, height: int, seed: int) -> bytes:
    """
    A vertical-gradient PNG at the given size.

    Poster aspect (2:3) and >=800px wide on purpose, so seeded data doesn't
    trip the admin gallery's low-resolution warning and muddy what you're
    actually testing.
    """
    base = _PALETTE[seed % len(_PALETTE)]
    rows = []
    for y in range(height):
        # Darken toward the bottom for an obvious, cheap gradient.
        factor = 1.0 - (y / max(height - 1, 1)) * 0.55
        pixel = bytes(max(0, min(255, int(channel * factor))) for channel in base)
        rows.append(b"\x00" + pixel * width)   # \x00 = no per-row filter

    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + _png_chunk(b"IEND", b"")
    )


# ═════════════════════════════════════════════════════════════════════════
#  DEMO TITLE DATA
# ═════════════════════════════════════════════════════════════════════════

# Enough real-looking titles to exercise the UI, including deliberately awkward
# ones — colons, accents, ampersands, apostrophes — because those are exactly
# what break marketplace title cleaning.
_DEMO_TITLES = [
    ("The Shawshank Redemption", "1994", "movie", "Two convicts form a friendship over several years, finding consolation and eventual redemption."),
    ("The Dark Knight", "2008", "movie", "Batman faces the Joker, a criminal mastermind who plunges Gotham into anarchy."),
    ("Inception", "2010", "movie", "A thief who steals corporate secrets through dream-sharing technology takes on one last job."),
    ("Léon: The Professional", "1994", "movie", "A hitman reluctantly takes in a young girl after her family is murdered."),
    ("Star Wars: Episode V - The Empire Strikes Back", "1980", "movie", "The Rebels scatter after an Imperial attack while Luke seeks training with Yoda."),
    ("Schindler's List", "1993", "movie", "An industrialist gradually becomes concerned for his Jewish workforce during the war."),
    ("Amélie", "2001", "movie", "A shy Parisian waitress decides to change the lives of those around her for the better."),
    ("Pirates of the Caribbean: The Curse of the Black Pearl", "2003", "movie", "A blacksmith allies with a roguish pirate to rescue his love from a cursed crew."),
    ("Breaking Bad", "2008", "tvSeries", "A chemistry teacher diagnosed with cancer turns to manufacturing drugs to secure his family's future."),
    ("Game of Thrones", "2011", "tvSeries", "Noble families vie for control of the Iron Throne as an ancient enemy returns."),
    ("The Sopranos", "1999", "tvSeries", "A New Jersey mob boss balances the demands of his crime family and his own."),
    ("Spirited Away", "2001", "movie", "A young girl wanders into a world of spirits and must work to free her parents."),
    ("Parasite", "2019", "movie", "A poor family schemes to infiltrate the household of a wealthy one."),
    ("Mad Max: Fury Road", "2015", "movie", "In a desert wasteland, a drifter and a rebel flee a tyrant across the sands."),
    ("The Grand Budapest Hotel", "2014", "movie", "A concierge and his protégé become entangled in the theft of a priceless painting."),
    ("Chernobyl", "2019", "tvSeries", "The 1986 nuclear disaster and the people who responded to it."),
    ("Whiplash", "2014", "movie", "A young drummer is pushed to his limits by an abusive instructor."),
    ("Blade Runner 2049", "2017", "movie", "A replicant blade runner uncovers a secret that could upend what is left of society."),
    ("Fight Club", "1999", "movie", "An insomniac office worker forms an underground fight club with a soap salesman."),
    ("Arrival", "2016", "movie", "A linguist is recruited to communicate with visitors whose language bends time."),
    ("Tokyo Story", "1953", "movie", "An elderly couple travel to Tokyo to visit their grown children."),
    ("The Good, the Bad and the Ugly", "1966", "movie", "Three gunslingers compete to find a fortune in buried Confederate gold."),
    ("Cowboy Bebop", "1998", "tvSeries", "A crew of bounty hunters chase criminals across the solar system."),
    ("Portrait of a Lady on Fire", "2019", "movie", "A painter is commissioned to produce a wedding portrait in secret."),
]


def _demo_title(index: int) -> tuple[str, str, str, str]:
    """
    A title for row `index`.

    The curated list is used first; beyond it, entries are suffixed so a large
    seed still produces unique, searchable rows rather than duplicates.
    """
    if index < len(_DEMO_TITLES):
        return _DEMO_TITLES[index]
    base_title, year, content_type, description = _DEMO_TITLES[index % len(_DEMO_TITLES)]
    volume = index // len(_DEMO_TITLES) + 1
    return (f"{base_title} Vol {volume}", year, content_type, description)


# ═════════════════════════════════════════════════════════════════════════
#  SETUP
# ═════════════════════════════════════════════════════════════════════════

class SetupAborted(RuntimeError):
    """Raised when a safety check refuses to continue."""


# ═════════════════════════════════════════════════════════════════════════
#  PREFLIGHT
# ═════════════════════════════════════════════════════════════════════════
#
# Every third-party module this tool needs, with the distribution that
# provides it. Checked BEFORE anything is deleted.
#
# This exists because of a real failure: `cryptography` was added to
# requirements.txt after a venv already existed, so the launcher's
# "first run only" install never re-ran. Setup then wiped the database,
# rebuilt the schema, and died two-thirds of the way through on the missing
# import — leaving tables with no users, i.e. an install you can't log into.
#
# A dependency problem must be caught while it is still harmless.
REQUIRED_MODULES = [
    ("sqlalchemy",   "SQLAlchemy"),
    ("bcrypt",       "bcrypt"),
    ("cryptography", "cryptography"),   # marketplace password encryption
    ("fastapi",      "fastapi"),
    ("itsdangerous", "itsdangerous"),
    ("jinja2",       "jinja2"),
]


def missing_modules() -> list[tuple[str, str]]:
    """Which required modules can't be imported. Uses find_spec so nothing
    is actually executed."""
    import importlib.util

    missing = []
    for module, distribution in REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            missing.append((module, distribution))
    return missing


def install_requirements(log: Callable[..., None]) -> bool:
    """
    Install requirements.txt into the running interpreter.

    Safe to do unprompted: this is a dev tool, it is normally launched from
    the project's own virtualenv, and installing the project's declared
    dependencies is exactly what the launcher would have done. Returns True
    if pip reported success.
    """
    import subprocess

    requirements = PROJECT_ROOT / "requirements.txt"
    if not requirements.is_file():
        log(f"  no requirements.txt at {requirements}", level="warn")
        return False

    log(f"  installing from {requirements.name} …")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as e:
        log(f"  pip could not be run: {e}", level="error")
        return False

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        for line in tail:
            log(f"    {line}", level="error")
        return False

    log("  dependencies installed")
    return True


def preflight(log: Callable[..., None], *, auto_install: bool = True) -> None:
    """
    Verify dependencies before touching the filesystem.

    Attempts a one-shot install of requirements.txt when something is
    missing, then re-checks. Raises SetupAborted rather than proceeding into
    a wipe it can't finish.
    """
    missing = missing_modules()
    if not missing:
        return

    log("Dependencies", level="head")
    for module, distribution in missing:
        log(f"  missing: {module}  (provides: {distribution})", level="warn")

    if auto_install and install_requirements(log):
        # find_spec caches negative results per-interpreter in some cases;
        # invalidate so the re-check sees freshly installed packages.
        import importlib
        importlib.invalidate_caches()
        missing = missing_modules()
        if not missing:
            log("  all dependencies present", level="ok")
            return

    names = ", ".join(distribution for _module, distribution in missing)
    raise SetupAborted(
        "Missing dependencies — nothing was changed.\n\n"
        f"  Not installed: {names}\n\n"
        "Install them and run setup again:\n\n"
        f'  "{sys.executable}" -m pip install -r requirements.txt\n\n'
        "If you are using the project's virtualenv, deleting the .venv folder "
        "and re-running DEV_SETUP.bat will rebuild it from scratch."
    )


class DevSetup:
    """
    Performs the wipe-and-rebuild.

    `log` is injected so the same code drives the GUI's console and the CLI's
    stdout — there is no separate CLI path that could behave differently.
    """

    def __init__(self, options: dict, log: Callable[..., None], *,
                 force: bool = False, auto_install: bool = True):
        self.options = {**DEFAULT_OPTIONS, **options}
        self.log = log
        self.force = force
        self.auto_install = auto_install
        self.results: dict = {}

    # ── Safety ─────────────────────────────────────────────────────────────

    def _looks_like_production(self) -> Optional[str]:
        """
        Return a reason string if this database looks real, else None.

        Deliberately conservative and based on stdlib sqlite3 so it works even
        if the app's imports are broken. Checks the master-title count and
        whether any payment runs exist — a dev box has neither.
        """
        from app.config import DB_PATH

        if not Path(DB_PATH).is_file():
            return None

        import sqlite3
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        except sqlite3.Error:
            return None

        try:
            cursor = conn.cursor()

            def table_exists(name: str) -> bool:
                cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
                return cursor.fetchone() is not None

            if table_exists("master_titles"):
                cursor.execute("SELECT COUNT(*) FROM master_titles")
                count = cursor.fetchone()[0]
                if count > PRODUCTION_TITLE_THRESHOLD:
                    return (f"{count:,} master titles — a dev database seeded by this "
                            f"tool has at most a few hundred.")

            if table_exists("payment_runs"):
                cursor.execute("SELECT COUNT(*) FROM payment_runs")
                runs = cursor.fetchone()[0]
                if runs > 3:
                    return f"{runs} payment runs on record — real money has been paid from this database."
        except sqlite3.Error:
            return None
        finally:
            conn.close()

        return None

    # ── Wipe ───────────────────────────────────────────────────────────────

    def wipe(self) -> None:
        """
        Delete the database, workspace tree and backups.

        The engine is disposed first: on Windows an open SQLite handle makes
        the file undeletable, which would otherwise surface as a confusing
        PermissionError halfway through.
        """
        from app.config import BACKUPS_DIR, DB_PATH, WORKSPACE_DIR
        from app.db import engine

        self.log("Wiping existing state", level="head")
        engine.dispose()

        db_path = Path(DB_PATH)
        for path in (db_path,
                     db_path.with_suffix(db_path.suffix + "-wal"),
                     db_path.with_suffix(db_path.suffix + "-shm")):
            if path.exists():
                try:
                    path.unlink()
                    self.log(f"  removed {path.name}")
                except OSError as e:
                    raise SetupAborted(
                        f"Could not delete {path.name}: {e}\n"
                        f"Stop the running server (uvicorn) and try again."
                    )

        for directory, label in ((WORKSPACE_DIR, "workspace"), (BACKUPS_DIR, "backups")):
            path = Path(directory)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                self.log(f"  cleared {label}/")
            path.mkdir(parents=True, exist_ok=True)

        stale = PROJECT_ROOT / ".first_admin.txt"
        if stale.exists():
            stale.unlink()
            self.log("  removed .first_admin.txt")

    # ── Build ──────────────────────────────────────────────────────────────

    def create_schema(self) -> None:
        """
        Create every table, then run the pipeline migration.

        create_all() covers a fresh database entirely, so the migration is a
        no-op here — it is run anyway because exercising it on every setup is
        what stops it from rotting between deployments.
        """
        from app.db import init_db

        self.log("Creating schema", level="head")
        init_db()
        self.log("  all tables created")

        try:
            from scripts.migrate_pipeline import migrate_schema
            result = migrate_schema()
            added = len(result.get("added", []))
            self.log(f"  pipeline migration verified ({added} columns added, "
                     f"{len(result.get('skipped', []))} already present)")
        except Exception as e:
            self.log(f"  migration check skipped: {e}", level="warn")

    def create_users(self, db) -> None:
        from app.auth import hash_password
        from app.models import User

        self.log("Creating users", level="head")
        for username, password, role in (
            (ADMIN_USERNAME, ADMIN_PASSWORD, "admin"),
            (WORKER_USERNAME, WORKER_PASSWORD, "worker"),
        ):
            db.add(User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                is_active=1,
                is_deleted=0,
            ))
            self.log(f"  {role:<6} {username} / {password}")
        db.flush()

    def create_project(self, db):
        """
        The primary project, plus an empty SECOND one.

        The second project exists purely so the master/project split is
        exercised locally. With one project the nav never replaces itself, the
        dashboard shows a single card, and the worker switcher stays hidden —
        which means every bug in that machinery would go unseen until the
        celebrity niche is added on the live server. An empty project costs
        nothing and makes the shape of the app visible.
        """
        from app import pipeline as P
        from app.models import Project

        project = P.ensure_default_project(db)
        db.flush()
        self.log("Pipeline projects", level="head")
        self.log(f"  {project.name} (slug: {project.slug}) — seeded with work")

        second = db.query(Project).filter_by(slug="celebrity").first()
        if second is None:
            second = Project(
                slug="celebrity",
                name="Celebrity Portraits",
                source_site="pinterest",
                target_site="fineartamerica",
                images_per_title=2,
                notes="Empty placeholder so the master/project split is testable locally.",
            )
            db.add(second)
            db.flush()
        self.log(f"  {second.name} (slug: {second.slug}) — empty, for testing the split")
        return project

    def seed_master_titles(self, db, project) -> int:
        """Fill the work queue so there is something to claim."""
        from app.models import MasterTitle

        count = int(self.options["master_titles"])
        if count <= 0:
            return 0

        self.log("Seeding master titles", level="head")
        for index in range(count):
            title, year, content_type, description = _demo_title(index)
            db.add(MasterTitle(
                external_id=index + 1,
                title=title,
                year=year,
                content_type=content_type,
                description=description,
                # Plausible values so the admin title list looks realistic.
                votes=250_000 - index * 137,
                rating=round(9.0 - (index % 40) * 0.07, 1),
                status="pending",
                project_id=project.id,
            ))
        db.flush()
        self.log(f"  {count} titles, status=pending, external_id 1..{count}")
        return count

    def seed_completed_work(self, db, project) -> dict:
        """
        Produce finished work with real files on disk.

        This is what makes the admin side worth looking at immediately: the
        gallery has posters to review, payments have something to pay for, and
        the pipeline has something to process. Titles are dated across recent
        days so the date-grouped views aren't a single row.
        """
        from app.models import MasterTitle, SavedPoster, User
        from app.parsing import filename_for, folder_name_for
        from app.utils import title_folder_for

        title_count = int(self.options["completed_titles"])
        per_title = int(self.options["posters_per_title"])
        if title_count <= 0 or per_title <= 0:
            return {"titles": 0, "posters": 0}

        self.log("Seeding completed work with real image files", level="head")

        worker = db.query(User).filter_by(username=WORKER_USERNAME).first()
        titles = (
            db.query(MasterTitle)
              .filter(MasterTitle.status == "pending")
              .order_by(MasterTitle.external_id.asc())
              .limit(title_count)
              .all()
        )
        if not titles:
            self.log("  no pending titles to complete", level="warn")
            return {"titles": 0, "posters": 0}

        today = date.today()
        poster_total = 0
        dates_used: set[str] = set()

        for offset, title in enumerate(titles):
            # Spread across the last few days, oldest first, so date-grouped
            # views and the greenlight queue have several buckets.
            save_date = today - timedelta(days=(title_count - offset - 1) % 5)
            dates_used.add(save_date.isoformat())

            title.claimed_by_id = worker.id
            title.claimed_by_name = worker.username
            title.claimed_at = datetime.utcnow()
            title.started_at = datetime.utcnow()
            title.original_save_date = save_date
            title.title_folder_path = folder_name_for(
                str(title.external_id), title.title, title.year)
            title.status = "complete"
            title.completed_at = datetime.utcnow()

            folder = title_folder_for(worker.username, save_date, title.title_folder_path)

            for n in range(1, per_title + 1):
                filename = filename_for(title.title, n, "poster.png")
                # Vary size a little so the gallery's dimension pills differ.
                width = 800 + (n - 1) * 100
                height = int(width * 1.5)
                payload = make_placeholder_png(width, height, seed=offset + n)

                (folder / filename).write_bytes(payload)

                db.add(SavedPoster(
                    master_title_id=title.id,
                    user_id=worker.id,
                    username=worker.username,
                    original_save_date=save_date,
                    title_folder_path=title.title_folder_path,
                    filename=filename,
                    source_url=f"https://image.tmdb.org/t/p/original/dev-seed-{title.external_id}-{n}.png",
                    file_size=len(payload),
                    image_width=width,
                    image_height=height,
                    created_at=datetime.utcnow(),
                ))
                poster_total += 1

        db.flush()
        self.log(f"  {len(titles)} titles marked complete for {worker.username}")
        self.log(f"  {poster_total} PNG files written across {len(dates_used)} date folder(s)")
        return {"titles": len(titles), "posters": poster_total,
                "dates": sorted(dates_used)}

    def seed_payment_run(self, db) -> Optional[dict]:
        """
        Record one payment covering the seeded work.

        Present so the payments UI isn't empty and — more usefully — so the
        greenlight queue can show dates as PAID, which is the state the
        auto-greenlight hook keys off.
        """
        if not self.options["seed_payment_run"]:
            return None

        import json
        from app.models import PaymentRun, SavedPoster, User
        from app.payments import get_rate_kes, parse_decimal

        worker = db.query(User).filter_by(username=WORKER_USERNAME).first()
        posters = (
            db.query(SavedPoster)
              .filter(SavedPoster.user_id == worker.id,
                      SavedPoster.deleted_at.is_(None))
              .all()
        )
        if not posters:
            return None

        self.log("Seeding a payment run", level="head")

        poster_ids = sorted(p.id for p in posters)
        by_day: dict[str, int] = {}
        for poster in posters:
            key = poster.original_save_date.isoformat()
            by_day[key] = by_day.get(key, 0) + 1

        rate = parse_decimal(get_rate_kes(db))
        amount = rate * len(poster_ids)
        dates = sorted(by_day)

        run = PaymentRun(
            worker_id=worker.id,
            worker_username=worker.username,
            period_start=date.fromisoformat(dates[0]),
            period_end=date.fromisoformat(dates[-1]),
            poster_count=len(poster_ids),
            rate_kes=str(rate),
            amount_kes=str(amount),
            reference="DEV-SEED",
            note="Created by dev_setup.py so the greenlight queue shows these dates as paid.",
            poster_ids_json=json.dumps(poster_ids),
            by_day_json=json.dumps(by_day),
            created_by=ADMIN_USERNAME,
        )
        db.add(run)
        db.flush()
        self.log(f"  {len(poster_ids)} posters, {amount} KES at {rate}/poster")
        return {"posters": len(poster_ids), "amount": str(amount)}

    def seed_worker_node(self, db) -> Optional[dict]:
        """
        Register a pipeline node and surface its token.

        The token is normally shown once and only hashed — for local work you
        want it printed where you can copy it into worker_service/config.json.
        """
        if not self.options["seed_worker_node"]:
            return None

        from app import pipeline as P

        self.log("Registering a pipeline worker node", level="head")
        node, token = P.create_node(db, name="local-dev", capabilities="process,upload")
        db.flush()
        self.log(f"  node '{node.name}' (process, upload)")
        self.log(f"  token: {token}")
        return {"name": node.name, "token": token}

    def seed_upload_account(self, db, project) -> Optional[dict]:
        """
        Create a disabled demo marketplace account.

        Disabled on purpose: it exists so the Upload tab and the per-account
        quota widgets have something to render, and it must never be able to
        push a real upload with placeholder credentials.
        """
        if not self.options["seed_upload_account"]:
            return None

        from app import pipeline as P
        from app.models import UploadAccount

        self.log("Creating a demo marketplace account", level="head")
        account = UploadAccount(
            project_id=project.id,
            name="DEV",
            # Canonical name. "faa" is the legacy value that MARKETPLACE_RENAMES
            # repairs at startup; writing it here keeps two names alive for one
            # marketplace and makes the dev database differ from a real one.
            target_site="fineartamerica",
            email="dev@example.com",
            password_enc=P.encrypt_secret("not-a-real-password"),
            profile_url="https://fineartamerica.com/profiles/0-dev",
            chrome_profile_dir="C:/faa/profiles/DEV",
            daily_limit=100,
            is_enabled=0,   # never let placeholder credentials reach a real site
            created_by="dev_setup",
        )
        db.add(account)
        db.flush()
        self.log("  account 'DEV' created, DISABLED")
        self.log("  enable it and set real credentials before any live upload")
        return {"name": account.name}

    def greenlight(self, db) -> Optional[dict]:
        """Push the seeded completed work into the pipeline."""
        if not self.options["greenlight_seeded"]:
            return None

        from app import pipeline as P
        from app.models import MasterTitle

        title_ids = [
            row[0] for row in
            db.query(MasterTitle.id).filter(MasterTitle.status == "complete").all()
        ]
        if not title_ids:
            return None

        self.log("Greenlighting the seeded work", level="head")
        result = P.greenlight_titles(db, title_ids, by=ADMIN_USERNAME, reason="dev_setup")
        db.flush()
        self.log(f"  {result['greenlit']} titles, {result['posters']} posters → greenlit")
        return result

    # ── Orchestration ──────────────────────────────────────────────────────

    def run(self) -> dict:
        started = datetime.now()

        # FIRST, before any import of app.* and before anything is deleted.
        # A missing dependency discovered mid-run would leave a wiped database
        # with a schema and no users — an install you cannot log into.
        preflight(self.log, auto_install=self.auto_install)

        from app.config import DB_PATH, WORKSPACE_DIR

        self.log(f"Project root: {PROJECT_ROOT}")
        self.log(f"Database:     {DB_PATH}")
        self.log(f"Workspace:    {WORKSPACE_DIR}")
        self.log("")

        reason = self._looks_like_production()
        if reason and not self.force:
            raise SetupAborted(
                "REFUSING TO RUN — this looks like a production database.\n\n"
                f"  {reason}\n\n"
                "This tool deletes the database, the workspace and the backups.\n"
                "If you really mean to wipe it, move it aside first, or re-run "
                "with --force from the CLI."
            )
        if reason and self.force:
            self.log(f"Production-looking database, --force given: {reason}", level="warn")

        self.wipe()
        self.log("")
        self.create_schema()
        self.log("")

        from app.db import SessionLocal
        db = SessionLocal()
        try:
            self.create_users(db)
            self.log("")
            project = self.create_project(db)
            self.log("")

            self.results["master_titles"] = self.seed_master_titles(db, project)
            self.log("")
            self.results["completed"] = self.seed_completed_work(db, project)
            self.log("")
            self.results["payment"] = self.seed_payment_run(db)
            if self.results["payment"]:
                self.log("")
            self.results["node"] = self.seed_worker_node(db)
            if self.results["node"]:
                self.log("")
            self.results["account"] = self.seed_upload_account(db, project)
            if self.results["account"]:
                self.log("")
            self.results["greenlit"] = self.greenlight(db)

            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        elapsed = (datetime.now() - started).total_seconds()
        self.log("")
        self.log(f"Done in {elapsed:.1f}s", level="ok")
        return self.results


# ═════════════════════════════════════════════════════════════════════════
#  GUI
# ═════════════════════════════════════════════════════════════════════════

def launch_gui() -> None:
    import queue
    import threading
    import tkinter as tk
    import webbrowser
    from tkinter import messagebox, ttk

    # Matches the app's own palette so the tool doesn't feel bolted on.
    BG, CARD, BORDER = "#0e0e14", "#1c1c28", "#2a2a3d"
    ACCENT, TEXT, SUBTEXT = "#e8b84b", "#f0ede6", "#8a8799"
    SUCCESS, ERROR, WARN = "#4ec98a", "#e8554b", "#e8a84b"

    root = tk.Tk()
    root.title("Poster Downloader — Local Dev Setup")
    root.geometry("1000x740")
    root.configure(bg=BG)
    root.minsize(860, 620)

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("TSpinbox", fieldbackground=CARD, foreground=TEXT, arrowcolor=ACCENT)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("Head.TLabel", background=BG, foreground=ACCENT,
                    font=("Segoe UI", 10, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=TEXT,
                    font=("Segoe UI", 17, "bold"))
    style.configure("Sub.TLabel", background=BG, foreground=SUBTEXT,
                    font=("Segoe UI", 9))

    outer = ttk.Frame(root, padding=16)
    outer.pack(fill="both", expand=True)

    ttk.Label(outer, text="Local Dev Setup", style="Title.TLabel").pack(anchor="w")
    ttk.Label(
        outer,
        text="Wipes the database, workspace and backups, then rebuilds everything from scratch.",
        style="Sub.TLabel",
    ).pack(anchor="w", pady=(2, 12))

    body = ttk.Frame(outer)
    body.pack(fill="both", expand=True)
    body.columnconfigure(0, minsize=310)
    body.columnconfigure(1, weight=1)
    body.rowconfigure(0, weight=1)

    # ── Left column: options ──────────────────────────────────────────────
    left = ttk.Frame(body)
    left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))

    creds = tk.Frame(left, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
    creds.pack(fill="x", pady=(0, 12))
    tk.Label(creds, text="ACCOUNTS CREATED", bg=CARD, fg=ACCENT,
             font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=11, pady=(9, 5))
    for role, username, password in (("admin", ADMIN_USERNAME, ADMIN_PASSWORD),
                                     ("worker", WORKER_USERNAME, WORKER_PASSWORD)):
        row = tk.Frame(creds, bg=CARD)
        row.pack(fill="x", padx=11, pady=1)
        tk.Label(row, text=f"{role:<7}", bg=CARD, fg=SUBTEXT,
                 font=("Consolas", 9)).pack(side="left")
        tk.Label(row, text=f"{username} / {password}", bg=CARD, fg=TEXT,
                 font=("Consolas", 10, "bold")).pack(side="left")
    tk.Frame(creds, bg=CARD, height=8).pack()

    ttk.Label(left, text="SEED DATA", style="Head.TLabel").pack(anchor="w", pady=(0, 6))

    master_var = tk.IntVar(value=DEFAULT_OPTIONS["master_titles"])
    completed_var = tk.IntVar(value=DEFAULT_OPTIONS["completed_titles"])
    per_title_var = tk.IntVar(value=DEFAULT_OPTIONS["posters_per_title"])

    for label, var, lo, hi, hint in (
        ("Master titles", master_var, 0, 5000, "rows in the work queue"),
        ("Completed titles", completed_var, 0, 200, "finished, with real files on disk"),
        ("Posters per title", per_title_var, 1, 8, "PNG files per completed title"),
    ):
        block = ttk.Frame(left)
        block.pack(fill="x", pady=(0, 7))
        row = ttk.Frame(block)
        row.pack(fill="x")
        ttk.Label(row, text=label).pack(side="left")
        ttk.Spinbox(row, from_=lo, to=hi, textvariable=var, width=7).pack(side="right")
        ttk.Label(block, text=hint, style="Sub.TLabel").pack(anchor="w")

    ttk.Label(left, text="PIPELINE", style="Head.TLabel").pack(anchor="w", pady=(8, 6))

    node_var = tk.BooleanVar(value=DEFAULT_OPTIONS["seed_worker_node"])
    account_var = tk.BooleanVar(value=DEFAULT_OPTIONS["seed_upload_account"])
    greenlight_var = tk.BooleanVar(value=DEFAULT_OPTIONS["greenlight_seeded"])
    payment_var = tk.BooleanVar(value=DEFAULT_OPTIONS["seed_payment_run"])

    for text, var, hint in (
        ("Register a worker node", node_var, "prints a token for worker_service/config.json"),
        ("Create a demo account", account_var, "disabled — placeholder credentials"),
        ("Record a payment run", payment_var, "makes greenlight dates show as PAID"),
        ("Greenlight seeded work", greenlight_var, "so the Pipeline tab has a queue"),
    ):
        block = ttk.Frame(left)
        block.pack(fill="x", pady=(0, 5))
        ttk.Checkbutton(block, text=text, variable=var).pack(anchor="w")
        ttk.Label(block, text=f"   {hint}", style="Sub.TLabel").pack(anchor="w")

    # ── Right column: log ─────────────────────────────────────────────────
    right = ttk.Frame(body)
    right.grid(row=0, column=1, sticky="nsew")
    right.rowconfigure(1, weight=1)
    right.columnconfigure(0, weight=1)

    ttk.Label(right, text="OUTPUT", style="Head.TLabel").grid(row=0, column=0, sticky="w",
                                                             pady=(0, 6))
    log_frame = tk.Frame(right, bg=BORDER, highlightthickness=0)
    log_frame.grid(row=1, column=0, sticky="nsew")
    log_frame.rowconfigure(0, weight=1)
    log_frame.columnconfigure(0, weight=1)

    log_text = tk.Text(log_frame, bg="#08080d", fg="#c9c6bd", insertbackground=TEXT,
                       font=("Consolas", 9), relief="flat", wrap="word",
                       padx=10, pady=8, state="disabled")
    log_text.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
    scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    log_text.configure(yscrollcommand=scrollbar.set)

    log_text.tag_configure("head", foreground=ACCENT, font=("Consolas", 9, "bold"))
    log_text.tag_configure("ok", foreground=SUCCESS, font=("Consolas", 9, "bold"))
    log_text.tag_configure("warn", foreground=WARN)
    log_text.tag_configure("error", foreground=ERROR, font=("Consolas", 9, "bold"))

    log_text.configure(state="normal")
    log_text.insert("end", "Ready.\n\nClick SETUP to wipe and rebuild.\n"
                           "Every run produces the same starting state.\n")
    log_text.configure(state="disabled")

    # ── Bottom bar ────────────────────────────────────────────────────────
    bottom = ttk.Frame(outer)
    bottom.pack(fill="x", pady=(14, 0))

    status_var = tk.StringVar(value="Idle")
    ttk.Label(bottom, textvariable=status_var, style="Sub.TLabel").pack(side="left")

    def styled_button(parent, text, command, *, primary=False):
        return tk.Button(
            parent, text=text, command=command,
            bg=ACCENT if primary else CARD,
            fg="#14131a" if primary else TEXT,
            activebackground="#f0c95f" if primary else BORDER,
            activeforeground="#14131a" if primary else TEXT,
            relief="flat", bd=0, cursor="hand2",
            font=("Segoe UI", 10, "bold" if primary else "normal"),
            padx=18 if primary else 13, pady=8,
        )

    server_process: dict = {"proc": None}

    def open_browser() -> None:
        webbrowser.open("http://localhost:8000/login")

    def toggle_server() -> None:
        """
        Start or stop uvicorn as a child process.

        Run from the project root so relative paths (workspace, poster.db)
        resolve the same way they do for a normal manual launch.
        """
        import subprocess

        proc = server_process["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            server_process["proc"] = None
            server_button.configure(text="START SERVER")
            status_var.set("Server stopped")
            return

        try:
            server_process["proc"] = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", "8000"],
                cwd=str(PROJECT_ROOT),
            )
        except Exception as e:
            messagebox.showerror("Could not start server", str(e))
            return

        server_button.configure(text="STOP SERVER")
        status_var.set("Server running on http://localhost:8000")
        root.after(1800, open_browser)

    browser_button = styled_button(bottom, "OPEN BROWSER", open_browser)
    browser_button.pack(side="right", padx=(8, 0))
    server_button = styled_button(bottom, "START SERVER", toggle_server)
    server_button.pack(side="right", padx=(8, 0))
    setup_button = styled_button(bottom, "SETUP", lambda: start_setup(), primary=True)
    setup_button.pack(side="right")

    # ── Threaded run ──────────────────────────────────────────────────────
    # The work touches the filesystem and SQLite, so it runs off the UI thread
    # and reports back through a queue — otherwise the window would freeze and
    # look hung during the wipe.
    messages: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def drain() -> None:
        try:
            while True:
                text, level = messages.get_nowait()
                log_text.configure(state="normal")
                log_text.insert("end", text + "\n", level or ())
                log_text.see("end")
                log_text.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(80, drain)

    root.after(80, drain)

    def start_setup() -> None:
        if server_process["proc"] is not None and server_process["proc"].poll() is None:
            messagebox.showwarning(
                "Server is running",
                "Stop the server first — it holds the database file open, "
                "so it cannot be deleted while running.",
            )
            return

        if not messagebox.askyesno(
            "Wipe and rebuild?",
            "This deletes:\n"
            "  • poster.db\n"
            "  • the workspace folder (all saved images)\n"
            "  • the backups folder\n\n"
            "Then recreates everything from scratch.\n\nContinue?",
            icon="warning",
        ):
            return

        log_text.configure(state="normal")
        log_text.delete("1.0", "end")
        log_text.configure(state="disabled")

        setup_button.configure(state="disabled", text="WORKING…")
        status_var.set("Running…")

        options = {
            "master_titles": master_var.get(),
            "completed_titles": completed_var.get(),
            "posters_per_title": per_title_var.get(),
            "seed_worker_node": node_var.get(),
            "seed_upload_account": account_var.get(),
            "greenlight_seeded": greenlight_var.get(),
            "seed_payment_run": payment_var.get(),
        }

        def log(text: str = "", *, level: str = "") -> None:
            messages.put((text, level))

        def work() -> None:
            try:
                DevSetup(options, log).run()
                log("")
                log("─" * 58)
                log("READY", level="ok")
                log(f"  {ADMIN_USERNAME} / {ADMIN_PASSWORD}   (admin)")
                log(f"  {WORKER_USERNAME} / {WORKER_PASSWORD}   (worker)")
                log("")
                log("  Click START SERVER, then log in at /login")
                log("─" * 58)
                root.after(0, lambda: status_var.set("Setup complete"))
            except SetupAborted as e:
                log("")
                log(str(e), level="error")
                root.after(0, lambda: status_var.set("Aborted"))
                root.after(0, lambda: messagebox.showerror("Setup aborted", str(e)))
            except Exception as e:
                import traceback
                log("")
                log(f"{type(e).__name__}: {e}", level="error")
                for line in traceback.format_exc().splitlines()[-12:]:
                    log("  " + line, level="error")
                root.after(0, lambda: status_var.set("Failed"))
            finally:
                root.after(0, lambda: setup_button.configure(state="normal", text="SETUP"))

        threading.Thread(target=work, daemon=True).start()

    def on_close() -> None:
        proc = server_process["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ═════════════════════════════════════════════════════════════════════════
#  CLI
# ═════════════════════════════════════════════════════════════════════════

def run_cli(args: argparse.Namespace) -> int:
    """
    Headless setup. Shares every code path with the GUI so the two can't drift.
    """
    def log(text: str = "", *, level: str = "") -> None:
        prefix = {"head": "\n== ", "ok": "** ", "warn": "!! ", "error": "XX "}.get(level, "")
        print(prefix + text, flush=True)

    options = {
        "master_titles": args.master_titles,
        "completed_titles": args.completed_titles,
        "posters_per_title": args.posters_per_title,
        "seed_worker_node": not args.no_node,
        "seed_upload_account": not args.no_account,
        "greenlight_seeded": not args.no_greenlight,
        "seed_payment_run": not args.no_payment,
    }

    print("=" * 62)
    print("  LOCAL DEV SETUP — wipes and rebuilds everything")
    print("=" * 62)

    try:
        DevSetup(options, log, force=args.force,
                 auto_install=not args.no_install).run()
    except SetupAborted as e:
        print(f"\nABORTED\n\n{e}\n")
        return 2
    except Exception as e:
        import traceback
        print(f"\nFAILED: {type(e).__name__}: {e}\n")
        traceback.print_exc()
        return 1

    print()
    print("─" * 62)
    print("  READY")
    print(f"    {ADMIN_USERNAME} / {ADMIN_PASSWORD}   (admin)")
    print(f"    {WORKER_USERNAME} / {WORKER_PASSWORD}   (worker)")
    print()
    print("    uvicorn app.main:app --reload")
    print("    http://localhost:8000/login")
    print("─" * 62)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wipe and rebuild the local development environment.",
        epilog="With no arguments, opens the GUI.",
    )
    parser.add_argument("--cli", action="store_true", help="Run headless instead of opening the GUI.")
    parser.add_argument("--master-titles", type=int, default=DEFAULT_OPTIONS["master_titles"])
    parser.add_argument("--completed-titles", type=int, default=DEFAULT_OPTIONS["completed_titles"])
    parser.add_argument("--posters-per-title", type=int, default=DEFAULT_OPTIONS["posters_per_title"])
    parser.add_argument("--no-node", action="store_true", help="Skip registering a worker node.")
    parser.add_argument("--no-account", action="store_true", help="Skip the demo marketplace account.")
    parser.add_argument("--no-payment", action="store_true", help="Skip the seeded payment run.")
    parser.add_argument("--no-greenlight", action="store_true", help="Leave seeded work un-greenlit.")
    parser.add_argument("--force", action="store_true",
                        help="Override the production-database safety check. Be certain.")
    parser.add_argument("--no-install", action="store_true",
                        help="Don't auto-install missing dependencies; just report them.")
    args = parser.parse_args()

    if args.cli:
        return run_cli(args)

    try:
        launch_gui()
    except Exception as e:
        # A headless machine has no Tk; fall back rather than dying.
        print(f"Could not open the GUI ({type(e).__name__}: {e}).")
        print("Re-run with --cli to do the same work headlessly.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
