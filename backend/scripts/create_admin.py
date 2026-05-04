"""Create or upsert an admin user.

Usage:
    docker compose exec backend python scripts/create_admin.py

Reads email/password from stdin (interactive prompt). Performs a
**select-then-update upsert** (not a SQL `INSERT ... ON CONFLICT`):
SELECT by email; if a row exists, overwrite `password_hash`, force
`role='admin'`, and clear lockout fields; otherwise INSERT a new row.
Intended for the very first deploy bootstrap and "rescue overwrite" of
a forgotten password; subsequent users should be created via the UI/API.
"""

from __future__ import annotations

import asyncio
import getpass
import re
import sys
from pathlib import Path

# Ensure backend/ is importable when invoked as `python scripts/create_admin.py`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.user import User  # noqa: E402

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 12


def _read_email() -> str:
    while True:
        email = input("admin email: ").strip().lower()
        if _EMAIL_RE.match(email):
            return email
        print("  -> invalid email, try again", file=sys.stderr)


def _read_password() -> str:
    while True:
        pw1 = getpass.getpass(f"admin password (min {_MIN_PASSWORD_LEN} chars): ")
        if len(pw1) < _MIN_PASSWORD_LEN:
            print(f"  -> too short (min {_MIN_PASSWORD_LEN})", file=sys.stderr)
            continue
        pw2 = getpass.getpass("confirm password: ")
        if pw1 != pw2:
            print("  -> passwords do not match", file=sys.stderr)
            continue
        return pw1


async def upsert_admin(email: str, password: str) -> str:
    """Insert or update a user with role=admin. Returns 'created' or 'updated'."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        password_hash = hash_password(password)

        if existing is None:
            user = User(
                email=email,
                password_hash=password_hash,
                role="admin",
            )
            session.add(user)
            await session.commit()
            return "created"

        existing.password_hash = password_hash
        existing.role = "admin"
        existing.failed_login_count = 0
        existing.locked_until = None
        await session.commit()
        return "updated"


async def _main() -> int:
    email = _read_email()
    password = _read_password()
    try:
        action = await upsert_admin(email, password)
    finally:
        await dispose_engine()
    print(f"admin user {action}: {email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
