"""
Sabotage test for check_sheet_columns_all_or_nothing.

WHY THIS EXISTS AS A SEPARATE FILE
    A check that cannot go red is worse than no check, because it reports
    all-clear for ever while guarding nothing. Two of the first eight
    preflight checks were exactly that. So every new check gets broken on
    purpose and has to notice.

    This one needs a real database, and the machine the code is written on
    has no sqlalchemy. So it runs where sqlalchemy already lives — inside
    the container — against a THROWAWAY database it makes and deletes. It
    never touches poster.db.

    docker compose exec web python tools/test_sheet_columns_check.py

WHAT IT PROVES AND WHAT IT DOES NOT
    It proves the check's QUERY finds a half-filled project and stays quiet
    on a full or empty one. It says nothing about whether the columns are
    the right idea — that is a judgement, and no test settles a judgement.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point the app at a scratch database BEFORE anything imports app.db, which
# reads the path once at import time.
#
# DATABASE_URL, not DB_PATH. DB_PATH is DERIVED from the URL — see
# config._sqlite_file_from_url — so setting DB_PATH does nothing at all and
# this would have run against the live catalogue. The first version of this
# file did exactly that; the guard below is what caught it, which is the
# whole argument for having a guard rather than trusting the intention.
_scratch = Path(tempfile.gettempdir()) / "sheet_columns_check_test.db"
_scratch.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_scratch}"

from app.config import DB_PATH                                   # noqa: E402

if Path(DB_PATH).resolve() != _scratch.resolve():
    sys.exit(
        f"REFUSING TO RUN. The app is pointed at\n"
        f"    {DB_PATH}\n"
        f"and not at the scratch database\n"
        f"    {_scratch}\n"
        f"so this test would create and delete rows in REAL data. Something\n"
        f"about how the database path is chosen has changed. Fix that before\n"
        f"trusting anything this file says."
    )

from app.db import SessionLocal, engine                           # noqa: E402
from app.models import Base, MasterTitle, Project                 # noqa: E402
from app.diagnostics import (                                     # noqa: E402
    Scope, check_sheet_columns_all_or_nothing as check,
)


def load(db, total: int, with_search: int, with_listing: int) -> None:
    db.query(MasterTitle).delete()
    db.commit()
    for i in range(total):
        db.add(MasterTitle(
            external_id=i + 1,
            title=f"Place {i}",
            year="N/A",
            project_id=1,
            status="pending",
            search_query=(f"Place {i} Kenya" if i < with_search else None),
            marketplace_title=(f"Place {i}" if i < with_listing else None),
        ))
    db.commit()


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if not db.query(Project).filter_by(id=1).first():
        db.add(Project(id=1, slug="scratch", name="Scratch"))
        db.commit()

    cases = [
        ("HEALTHY  no sheet columns at all",      100,   0,   0, 0),
        ("HEALTHY  every title has both",         100, 100, 100, 0),
        ("SABOTAGE marketplace column lost",      100, 100,  63, 1),
        ("SABOTAGE only 5 got a search query",    100,   5, 100, 1),
        ("SABOTAGE both half filled",             100,  40,  70, 2),
    ]

    failures = 0
    for label, total, s, m, want in cases:
        load(db, total, s, m)
        result = check(db, Scope(db))
        got = result.count
        ok = got == want
        failures += not ok
        print(f"  {'ok ' if ok else 'BAD'}  {label:<36} "
              f"findings={got} (want {want})")
        if not ok:
            for f in result.findings:
                print(f"          {f.title}")

    db.close()
    engine.dispose()
    _scratch.unlink(missing_ok=True)

    print()
    if failures:
        print(f"!! {failures} case(s) wrong. The check does NOT behave as "
              f"documented — do not trust it.")
        sys.exit(1)
    print("All cases correct: silent when a project is whole or empty, "
          "red when it is half filled.")


if __name__ == "__main__":
    main()
