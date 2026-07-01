"""Provision staff login accounts from the staff master (P3 / WS-3).

Design source of truth: ``docs/plans/staff-account-linking-design.md`` §7.1.

For every alive staff member that has a ``code`` (S001…) but is **not yet
linked** to a user account, this creates a ``staff`` role ``User`` whose
``username`` is the (normalised) staff code. No email is required — staff log
in with their code. ``staff_id`` is auto-linked and ``must_change_password`` is
set so the operator hands over a one-time temp password.

Skip rules (idempotent, safe to re-run):
  * staff already has an alive account (``users.staff_id = staff.id``) → skip.
  * the target username (= ``code`` normalised) is already taken → skip + warn.
  * the normalised code contains invalid characters → skip + warn.
  * ``IntegrityError`` on flush (race with the partial unique indexes) →
    rolled back for that row and counted as skipped.

Temp-password hand-off mirrors ``import_users.py``: ``--out PATH`` writes a
0600 CSV (``staff_code,username,role,temp_password``); without it the
credentials are printed to stdout once and never persisted.

Usage:
    python scripts/provision_staff_accounts.py [--dry-run] [--out PATH]
                                               [--only-code S002,S003] [--all-roles]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import print_summary  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.staff import Staff  # noqa: E402
from app.models.user import User  # noqa: E402

logger = logging.getLogger(__name__)

# Staff whose master role gets an account by default. --all-roles widens this
# to every role (e.g. manager S001), but the created *account* role is always
# "staff" — it is a field-worker login regardless of the staff master role.
_DEFAULT_STAFF_ROLES = ("staff",)
_ACCOUNT_ROLE = "staff"

# Mirrors the schema-layer _normalize_username pattern in P1a so only
# ASCII-safe, lowercase codes pass through (guards against full-width chars,
# embedded spaces, etc. that would create unusable login IDs).
_USERNAME_RE = re.compile(r"^[a-z0-9_.\-]+$")


@dataclass
class ProvisionResult:
    created: list[tuple[str, str, str, str]] = field(default_factory=list)
    """(staff_code, username, role, temp_password) for each newly created row."""
    skipped: int = 0
    failed: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created)

    def summary(self) -> dict[str, int]:
        return {
            "created": self.created_count,
            "updated": 0,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def normalize_code(code: str) -> str | None:
    """Normalise a staff code into its username form (strip + lowercase).

    Returns ``None`` when the result contains characters outside
    ``[a-z0-9_.-]`` — callers must treat ``None`` as skip + warn.

    The schema layer normalises usernames the same way (P1a
    ``_normalize_username``), but this script does not go through the schema,
    so normalisation is applied explicitly here.
    """
    normalised = code.strip().lower()
    if not normalised or not _USERNAME_RE.match(normalised):
        return None
    return normalised


def _generate_temp_password() -> str:
    # token_urlsafe(12) → 16 URL-safe chars, same policy as import_users.py.
    return secrets.token_urlsafe(12)


def _parse_only_code(value: str | None) -> set[str] | None:
    if not value:
        return None
    # Normalise to uppercase so --only-code s002 matches staff.code == "S002".
    codes = {c.strip().upper() for c in value.split(",") if c.strip()}
    return codes or None


async def provision_accounts(
    *,
    dry_run: bool,
    only_codes: set[str] | None = None,
    all_roles: bool = False,
) -> ProvisionResult:
    """Create staff accounts for unlinked, code-bearing staff.

    Runs entirely inside a single session obtained from
    ``get_session_factory()`` so it binds to whatever engine the caller (CLI or
    test fixture) has configured. Returns a :class:`ProvisionResult`; the temp
    passwords it carries are the only place plaintext ever exists.
    """
    result = ProvisionResult()
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(Staff).where(
            Staff.deleted_at.is_(None),
            Staff.code.is_not(None),
        )
        if not all_roles:
            stmt = stmt.where(Staff.role.in_(_DEFAULT_STAFF_ROLES))
        staff_rows = list((await session.scalars(stmt.order_by(Staff.code))).all())

        if only_codes is not None:
            # Compare against upper-cased codes (staff.code is canonical S001…).
            staff_rows = [s for s in staff_rows if (s.code or "").upper() in only_codes]

        # Alive users already linked to a staff → those staff are done.
        linked_ids = set(
            (
                await session.scalars(
                    select(User.staff_id).where(
                        User.staff_id.is_not(None),
                        User.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        # Alive usernames already in use — loaded as lowercase so the check is
        # case-insensitive, symmetric with the code-based login path.
        taken_usernames: set[str] = set(
            filter(
                None,
                (
                    await session.scalars(
                        select(func.lower(User.username)).where(
                            User.username.is_not(None),
                            User.deleted_at.is_(None),
                        )
                    )
                ).all(),
            )
        )

        for staff in staff_rows:
            code = staff.code
            assert code is not None  # guarded by the query above (mypy narrowing)

            username = normalize_code(code)
            if username is None:
                result.skipped += 1
                result.warnings.append(
                    f"{code}: normalised code contains invalid characters -- skipped"
                )
                continue

            if staff.id in linked_ids:
                result.skipped += 1
                continue
            if username in taken_usernames:
                result.skipped += 1
                result.warnings.append(f"{code}: username {username!r} already in use -- skipped")
                continue

            if dry_run:
                result.created.append((code, username, _ACCOUNT_ROLE, ""))
                # Reserve the name so two staff sharing a normalised code do not
                # both appear as creatable in the same dry run.
                taken_usernames.add(username)
                continue

            temp_pw = _generate_temp_password()
            try:
                async with session.begin_nested():
                    session.add(
                        User(
                            username=username,
                            email=None,
                            role=_ACCOUNT_ROLE,
                            staff_id=staff.id,
                            must_change_password=True,
                            password_hash=hash_password(temp_pw),
                        )
                    )
                    await session.flush()
            except IntegrityError as exc:
                # Partial unique index (username / staff_id) tripped — treat as an
                # already-provisioned row rather than aborting the whole batch.
                # Log the raw DB detail at DEBUG only; the public warning omits
                # internal index names to avoid leaking schema information.
                logger.debug(
                    "[staff-accounts] %s: integrity conflict detail: %s",
                    code,
                    exc.orig,
                )
                result.skipped += 1
                result.warnings.append(
                    f"{code}: integrity conflict -- skipped (likely duplicate username or staff_id)"
                )
                continue

            taken_usernames.add(username)
            linked_ids.add(staff.id)
            result.created.append((code, username, _ACCOUNT_ROLE, temp_pw))

        if not dry_run:
            await session.commit()

    return result


def _emit_credentials(result: ProvisionResult, output_csv: Path | None) -> None:
    rows = result.created
    if output_csv is None:
        if rows:
            print(
                "[staff-accounts] temp-password CSV output skipped (no --out PATH); "
                "new credentials below -- copy them now, they will not be "
                "recoverable later."
            )
            for staff_code, username, role, temp_pw in rows:
                print(f"  - {staff_code}\t{username}\t{role}\t{temp_pw}")
        else:
            print("[staff-accounts] no new accounts created -- no CSV to write.")
        return

    output_csv.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Set a restrictive umask before opening so the file is created with
    # 0600 permissions from the start, closing the TOCTOU window between
    # file creation and the subsequent chmod call.
    old_umask = os.umask(0o077)
    try:
        with output_csv.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(["staff_code", "username", "role", "temp_password"])
            writer.writerows(rows)
    finally:
        os.umask(old_umask)
    # Belt-and-suspenders: explicit chmod in case umask had no effect (e.g.
    # the file already existed with looser permissions).
    try:
        os.chmod(output_csv, 0o600)
    except (OSError, NotImplementedError):
        # Windows / non-POSIX FS: chmod is a no-op. Warn so the operator knows
        # to apply ACLs manually.
        logger.warning(
            "[staff-accounts] could not chmod 0600 %s -- apply ACLs manually",
            output_csv,
        )
    print(f"[staff-accounts] temp-password CSV -> {output_csv} (mode 0600)")
    print(
        "[staff-accounts] distribute credentials then delete the file securely "
        f"(e.g. `shred -u {output_csv}` on Linux, or `sdelete` on Windows)."
    )


async def _run(
    *,
    dry_run: bool,
    output_csv: Path | None,
    only_codes: set[str] | None,
    all_roles: bool,
) -> ProvisionResult:
    result = await provision_accounts(dry_run=dry_run, only_codes=only_codes, all_roles=all_roles)

    if dry_run:
        print(f"[staff-accounts] would create {result.created_count} account(s):")
        for staff_code, username, role, _ in result.created:
            print(f"  - {staff_code} -> username={username} role={role}")
    else:
        _emit_credentials(result, output_csv)

    print_summary("staff-accounts", **result.summary(), errors=result.warnings)
    return result


async def _main(
    *,
    dry_run: bool,
    output_csv: Path | None,
    only_codes: set[str] | None,
    all_roles: bool,
) -> ProvisionResult:
    try:
        return await _run(
            dry_run=dry_run,
            output_csv=output_csv,
            only_codes=only_codes,
            all_roles=all_roles,
        )
    finally:
        # Dispose inside the same loop that owns asyncpg connections to avoid
        # "RuntimeError: Event loop is closed" on shutdown.
        # Always dispose, even for dry-run, because provision_accounts still
        # opens a session (read queries) and must release pool resources cleanly.
        await dispose_engine()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Provision staff login accounts from the staff master (P3)"
    )
    p.add_argument("--dry-run", action="store_true", help="print planned accounts only")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "destination CSV for newly-generated temp passwords "
            "(staff_code,username,role,temp_password). If omitted the "
            "credentials are printed to stdout once and not persisted."
        ),
    )
    p.add_argument(
        "--only-code",
        default=None,
        help="comma-separated staff codes to restrict to (e.g. 'S002,S003' or 's002,s003')",
    )
    p.add_argument(
        "--all-roles",
        action="store_true",
        help="include staff of any master role (default: only role=staff)",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    asyncio.run(
        _main(
            dry_run=args.dry_run,
            output_csv=args.out,
            only_codes=_parse_only_code(args.only_code),
            all_roles=args.all_roles,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
