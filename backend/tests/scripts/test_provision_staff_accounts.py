"""Tests for scripts/provision_staff_accounts.py (P3 / WS-3).

Coverage:
  1. normalize_code: strip + lowercase; invalid chars → None.
  2. two code-bearing staff → two staff accounts (username=code lowercased,
     must_change_password=True, email None, role=staff, staff_id linked).
  3. idempotent: re-run creates 0.
  4. already-linked staff is skipped (no duplicate account).
  5. username collision is skipped + warned.
  6. --dry-run writes nothing to the DB.
  7. role / --all-roles filtering and --only-code filtering.
  8. two staff whose codes normalise to the same username → created=1, skipped=1.
  9. staff with an invalid-charset code is skipped with a warning.
"""

# ruff: noqa: I001
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

# Add scripts/ + backend/ to sys.path (mirrors test_cleanup_visits_duplicate.py).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from provision_staff_accounts import (  # noqa: E402
    normalize_code,
    provision_accounts,
)

from app.core.security import hash_password  # noqa: E402
from app.models.staff import Staff  # noqa: E402
from app.models.user import User  # noqa: E402


def test_normalize_code_strips_and_lowercases() -> None:
    assert normalize_code("  S002 ") == "s002"
    assert normalize_code("S003") == "s003"


def test_normalize_code_rejects_invalid_chars() -> None:
    # Full-width digits / letters
    assert normalize_code("Ｓ０２") is None
    # Embedded space after strip leaves no content
    assert normalize_code("S 02") is None
    # Empty string (or whitespace-only)
    assert normalize_code("   ") is None


async def _alive_users(db) -> list[User]:
    return list((await db.scalars(select(User).where(User.deleted_at.is_(None)))).all())


@pytest.mark.asyncio
async def test_creates_accounts_for_unlinked_staff(_engine, db) -> None:
    """Two code-bearing staff → two staff accounts with normalised usernames."""
    db.add_all(
        [
            Staff(code="S002", name="佐藤 花子", role="staff"),
            Staff(code="S003", name="鈴木 太郎", role="staff"),
        ]
    )
    await db.commit()

    result = await provision_accounts(dry_run=False)

    assert result.created_count == 2
    assert result.skipped == 0
    assert result.failed == 0

    await db.rollback()  # provision committed in its own session
    users = {u.username: u for u in await _alive_users(db)}
    assert set(users) == {"s002", "s003"}
    for u in users.values():
        assert u.role == "staff"
        assert u.email is None
        assert u.must_change_password is True
        assert u.staff_id is not None
        assert u.password_hash  # a real hash was stored

    # temp passwords are surfaced only via the result rows
    temp_by_code = {code: temp for code, _u, _r, temp in result.created}
    assert temp_by_code["S002"] and temp_by_code["S003"]


@pytest.mark.asyncio
async def test_idempotent_rerun_creates_zero(_engine, db) -> None:
    db.add(Staff(code="S002", name="佐藤 花子", role="staff"))
    await db.commit()

    first = await provision_accounts(dry_run=False)
    assert first.created_count == 1

    second = await provision_accounts(dry_run=False)
    assert second.created_count == 0
    assert second.skipped == 1

    await db.rollback()
    assert len(await _alive_users(db)) == 1


@pytest.mark.asyncio
async def test_already_linked_staff_is_skipped(_engine, db) -> None:
    """A staff already linked to an account is skipped (川名 S001 scenario)."""
    staff = Staff(code="S001", name="川名 千恵", role="staff")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    db.add(
        User(
            email="chie.kawana@thousands.jp",
            password_hash=hash_password("existing-pw-01"),
            role="manager",
            staff_id=staff.id,
        )
    )
    await db.commit()

    result = await provision_accounts(dry_run=False, all_roles=True)
    assert result.created_count == 0
    assert result.skipped == 1

    await db.rollback()
    users = await _alive_users(db)
    assert len(users) == 1
    assert users[0].email == "chie.kawana@thousands.jp"


@pytest.mark.asyncio
async def test_username_collision_is_skipped_with_warning(_engine, db) -> None:
    """A pre-existing username equal to the staff code blocks creation."""
    db.add_all(
        [
            Staff(code="S002", name="佐藤 花子", role="staff"),
            User(
                username="s002",
                password_hash=hash_password("someone-else-01"),
                role="staff",
            ),
        ]
    )
    await db.commit()

    result = await provision_accounts(dry_run=False)
    assert result.created_count == 0
    assert result.skipped == 1
    assert any("s002" in w for w in result.warnings)

    await db.rollback()
    # still exactly one s002 user, and it has no staff link
    users = [u for u in await _alive_users(db) if u.username == "s002"]
    assert len(users) == 1
    assert users[0].staff_id is None


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(_engine, db) -> None:
    db.add(Staff(code="S002", name="佐藤 花子", role="staff"))
    await db.commit()

    result = await provision_accounts(dry_run=True)
    assert result.created_count == 1  # would create one

    await db.rollback()
    assert len(await _alive_users(db)) == 0


@pytest.mark.asyncio
async def test_role_and_only_code_filters(_engine, db) -> None:
    """Default excludes manager staff; --all-roles + --only-code narrow scope."""
    db.add_all(
        [
            Staff(code="S001", name="川名 千恵", role="manager"),
            Staff(code="S002", name="佐藤 花子", role="staff"),
            Staff(code="S003", name="鈴木 太郎", role="staff"),
        ]
    )
    await db.commit()

    # default: manager S001 excluded → only S002, S003
    default = await provision_accounts(dry_run=True)
    assert {row[0] for row in default.created} == {"S002", "S003"}

    # --all-roles: manager S001 included
    all_roles = await provision_accounts(dry_run=True, all_roles=True)
    assert {row[0] for row in all_roles.created} == {"S001", "S002", "S003"}

    # --only-code restricts the set
    only = await provision_accounts(dry_run=True, only_codes={"S003"})
    assert {row[0] for row in only.created} == {"S003"}


@pytest.mark.asyncio
async def test_normalised_code_collision_creates_one_skips_one(_engine, db) -> None:
    """Two staff whose codes normalise to the same username: first wins, second skips."""
    # "S002" and " s002 " both normalise to "s002".
    db.add_all(
        [
            Staff(code="S002", name="佐藤 花子", role="staff"),
            Staff(code=" s002 ", name="重複コード", role="staff"),
        ]
    )
    await db.commit()

    result = await provision_accounts(dry_run=False)
    assert result.created_count == 1
    assert result.skipped == 1
    # The skipped one should appear in warnings (username collision path).
    assert any("s002" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_invalid_charset_code_is_skipped_with_warning(_engine, db) -> None:
    """A staff whose code contains invalid characters is skipped + warned."""
    db.add_all(
        [
            # Valid code alongside an invalid one.
            Staff(code="S002", name="有効コード", role="staff"),
            Staff(code="Ｓ０２", name="全角コード", role="staff"),  # full-width
        ]
    )
    await db.commit()

    result = await provision_accounts(dry_run=False)
    assert result.created_count == 1
    assert result.skipped == 1
    assert any("invalid characters" in w for w in result.warnings)
