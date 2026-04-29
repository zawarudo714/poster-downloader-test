#!/usr/bin/env python3
"""
Create the first admin (or any user). Run once after installing.

    python scripts/create_admin.py

You'll be prompted for username + password.
"""

import getpass
import os
import re
import sys

# Make `app` importable when running from project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password
from app.db import SessionLocal, init_db
from app.models import User


def main():
    init_db()
    print("─" * 60)
    print("Poster Downloader — Create user")
    print("─" * 60)

    role = input("Role [admin/worker] (default: admin): ").strip().lower() or "admin"
    if role not in ("admin", "worker"):
        print("Role must be 'admin' or 'worker'.")
        return 1

    username = input("Username: ").strip()
    if not re.match(r"^[A-Za-z0-9_.-]{2,64}$", username):
        print("Username must be 2–64 chars: letters, digits, _ . -")
        return 1

    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.")
        return 1
    if len(pw1) < 6:
        print("Password must be at least 6 characters.")
        return 1

    db = SessionLocal()
    try:
        if db.query(User).filter_by(username=username).first():
            print(f"User '{username}' already exists.")
            return 1
        u = User(username=username, password_hash=hash_password(pw1), role=role)
        db.add(u)
        db.commit()
        print(f"\n✓ Created {role} user: {username}")
        print("\nNow run:  uvicorn app.main:app --reload")
        print("Then open: http://localhost:8000/login")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
