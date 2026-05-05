"""Import admin users from Sample 2 (sheet '管理者').

Sheet has columns: email / enabled / name / role. Password is **not** in the
sheet, so each new user gets a 16-char ``secrets.token_urlsafe(12)``
temp-password (bcrypt-hashed for storage). Plaintext temp passwords are
written to ``backend/data/initial_users.csv`` for hand-off; existing rows on
that file are overwritten on re-run.

Rows after the admin block (e.g. 'パスワード', '展開制限月') are config noise:
filtered out by requiring a syntactically valid email.

Usage:
    python scripts/import_users.py /path/to/sample2.xlsx [--dry-run]
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    build_parser,
    cell,
    clean_str,
    iter_rows,
    parse_bool,
    print_summary,
)

from sqlalchemy import select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.user import USER_ROLES, User  # noqa: E402

SHEET_NAME = "管理者"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

logger = logging.getLogger(__name__)


def _norm_role(value: Any) -> str:
    s = (clean_str(value) or "admin").lower()
    return s if s in USER_ROLES else "admin"


def build_payload(row: tuple, idx: dict[str, int]) -> dict[str, Any] | None:
    email = clean_str(cell(row, idx, "email"))
    if not email or not EMAIL_RE.match(email):
        return None
    enabled = parse_bool(cell(row, idx, "enabled"), default=True)
    if not enabled:
        return None
    return {
        "email": email.lower(),
        "name": clean_str(cell(row, idx, "name")),
        "role": _norm_role(cell(row, idx, "role")),
    }


def _generate_temp_password() -> str:
    # token_urlsafe(12) → 16 chars (URL-safe base64), satisfies "ランダム 16文字".
    return secrets.token_urlsafe(12)


async def import_users(xlsx: Path, dry_run: bool,
                       output_csv: Path | None = None) -> dict[str, int]:
    idx_map, rows = iter_rows(xlsx, SHEET_NAME)
    if not idx_map:
        raise RuntimeError(f"empty header row in sheet '{SHEET_NAME}'")

    payloads: list[dict[str, Any]] = []
    failures: list[str] = []
    for row_no, row in enumerate(rows, start=2):
        try:
            payload = build_payload(row, idx_map)
            if payload is None:
                continue
            payloads.append(payload)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"row {row_no}: {exc}")

    summary = {"created": 0, "updated": 0, "skipped": 0, "failed": len(failures)}

    if dry_run:
        print(f"[users] parsed={len(payloads)} admin row(s) from sheet '{SHEET_NAME}'")
        for p in payloads:
            print(f"  - {p['email']} role={p['role']} name={p['name']}")
        print_summary("users", **summary, errors=failures)
        return summary

    # Wave 4-G: only NEW users with a freshly generated temp password are
    # written out. "(existing - …)" rows contain no actionable secret and
    # are dropped to minimise the blast radius of the CSV.
    csv_rows: list[tuple[str, str, str]] = []
    factory = get_session_factory()
    async with factory() as session:
        existing = await session.execute(
            select(User).where(User.email.in_([p["email"] for p in payloads]))
        )
        by_email = {u.email: u for u in existing.scalars()}

        for payload in payloads:
            email = payload["email"]
            role = payload["role"]
            obj = by_email.get(email)
            if obj is None:
                temp_pw = _generate_temp_password()
                session.add(User(
                    email=email,
                    password_hash=hash_password(temp_pw),
                    role=role,
                ))
                csv_rows.append((email, role, temp_pw))
                summary["created"] += 1
            else:
                # Existing user: leave password alone, only sync role.
                # No CSV row — nothing secret to hand off.
                if obj.role != role:
                    obj.role = role
                summary["updated"] += 1

        await session.commit()

    if output_csv is None:
        # Wave 4-G: never write temp passwords to disk implicitly. Surface
        # them via stdout so the operator sees them but they don't linger
        # on the container filesystem after the import process exits.
        if csv_rows:
            print(
                "[users] temp-password CSV output skipped (no --out PATH); "
                "new credentials below — copy them now, they will not be "
                "recoverable later."
            )
            for email, role, temp_pw in csv_rows:
                print(f"  - {email}\t{role}\t{temp_pw}")
        else:
            print("[users] no new users created — no CSV to write.")
    else:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        with output_csv.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["email", "role", "temp_password"])
            writer.writerows(csv_rows)
        # Lock to owner-only read/write — this file holds plaintext
        # bootstrap passwords until the operator distributes them.
        try:
            os.chmod(output_csv, 0o600)
        except (OSError, NotImplementedError):
            # Windows / non-POSIX FS: chmod is a no-op. Warn so the
            # operator knows to apply ACLs manually.
            logger.warning(
                "[users] could not chmod 0600 %s — apply ACLs manually",
                output_csv,
            )
        print(f"[users] temp-password CSV → {output_csv} (mode 0600)")

    print_summary("users", **summary, errors=failures)
    return summary


async def _main(xlsx: Path, dry_run: bool, output_csv: Path | None) -> dict[str, int]:
    try:
        return await import_users(xlsx, dry_run, output_csv=output_csv)
    finally:
        # Dispose inside the same loop that owns asyncpg connections to
        # avoid "RuntimeError: Event loop is closed" on shutdown.
        if not dry_run:
            await dispose_engine()


def main() -> int:
    parser = build_parser("Import admin users (Sample 2 / 管理者)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "destination CSV for newly-generated temp passwords. If omitted "
            "the credentials are printed to stdout once and not persisted."
        ),
    )
    args = parser.parse_args()
    asyncio.run(_main(args.xlsx, args.dry_run, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
