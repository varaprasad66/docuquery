"""Operator-only account provisioning. Run from backend; passwords use hidden input."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.api.schemas import UserRegister
from app.config import get_settings
from app.db.models import User
from app.services.auth import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "promote", "disable"])
    parser.add_argument("email")
    args = parser.parse_args()
    email = args.email.strip().lower()
    engine = create_engine(get_settings().sync_database_url)
    try:
        with Session(engine) as db:
            user = db.scalar(select(User).where(User.email == email))
            if args.action == "create":
                if user:
                    raise SystemExit("Account already exists; no changes made.")
                password = getpass("New account password: ")
                if password != getpass("Repeat password: "):
                    raise SystemExit("Passwords do not match.")
                validated = UserRegister(email=email, password=password)
                db.add(User(email=validated.email, password_hash=hash_password(password), role="user"))
            else:
                if user is None:
                    raise SystemExit("Account not found.")
                if args.action == "promote":
                    user.role = "admin"
                else:
                    user.is_active = False
            db.commit()
        print("Account operation completed. No credentials printed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
