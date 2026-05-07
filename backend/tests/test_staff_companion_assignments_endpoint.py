"""Tests for /api/v1/staff-companion-assignments (W15-FE Phase 5 F-1).

Endpoint:
  GET    /api/v1/staff-companion-assignments?course_template_id=&iso_year=&iso_week=
  PATCH  /api/v1/staff-companion-assignments/{assignment_id}

Coverage:
  1. GET: admin can call (200 + empty list — schema does not yet associate
         assignments with a course template / week).
  2. GET: missing required query params -> 422.
  3. GET: staff role can call (no 403).
  4. PATCH: admin can update pair_role primary -> support -> null.
  5. PATCH: 404 when assignment id not found.
  6. PATCH: 403 when caller is staff role.
  7. PATCH: 422 on extra unknown field (extra='forbid').
  8. PATCH: 422 on invalid pair_role value.
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, StaffCompanionAssignment, User

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id: UUID | None = None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(
    db,
    name: str,
    *,
    role: str = "staff",
    status_: str = "active",
    is_trainee: bool = False,
) -> Staff:
    s = Staff(name=name, role=role, status=status_, is_trainee=is_trainee)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_assignment(
    db,
    *,
    trainee_id: UUID,
    companion_id: UUID,
    weekday: int = 0,
    part: str = "am",
    pair_role: str | None = None,
) -> StaffCompanionAssignment:
    row = StaffCompanionAssignment(
        id=uuid.uuid4(),
        trainee_staff_id=trainee_id,
        weekday=weekday,
        part=part,
        companion_staff_id=companion_id,
        pair_role=pair_role,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. GET: admin returns 200 + empty list (current schema limitation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_staff_companion_assignments_returns_empty(client, db) -> None:
    """GET returns empty list (schema limitation; assignments have no course link)."""
    admin = await _make_user(db, "sca-get1@example.com", "admin")
    course_template_id = uuid.uuid4()

    res = await client.get(
        "/api/v1/staff-companion-assignments",
        params={
            "course_template_id": str(course_template_id),
            "iso_year": 2026,
            "iso_week": 19,
        },
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 2. GET: missing query params -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_staff_companion_assignments_missing_params_422(client, db) -> None:
    """GET without required query params returns 422."""
    admin = await _make_user(db, "sca-get2@example.com", "admin")
    res = await client.get(
        "/api/v1/staff-companion-assignments",
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 3. GET: staff role can call (RBAC allows admin/manager/staff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_staff_companion_assignments_staff_role_ok(client, db) -> None:
    """staff role is permitted (no 403)."""
    staff_user = await _make_user(db, "sca-get3@example.com", "staff")
    course_template_id = uuid.uuid4()

    res = await client.get(
        "/api/v1/staff-companion-assignments",
        params={
            "course_template_id": str(course_template_id),
            "iso_year": 2026,
            "iso_week": 19,
        },
        headers=_bearer(staff_user),
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# 4. PATCH: admin updates pair_role primary -> support -> null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pair_role_admin_round_trip(client, db) -> None:
    """admin can set pair_role to primary, support, then back to null."""
    admin = await _make_user(db, "sca-patch1@example.com", "admin")
    trainee = await _make_staff(db, "新人A", is_trainee=True)
    companion = await _make_staff(db, "先輩B")
    row = await _make_assignment(db, trainee_id=trainee.id, companion_id=companion.id)

    # primary
    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": "primary"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["pair_role"] == "primary"

    # support
    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": "support"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["pair_role"] == "support"

    # null
    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": None},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["pair_role"] is None


# ---------------------------------------------------------------------------
# 5. PATCH: not found -> 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pair_role_not_found_404(client, db) -> None:
    admin = await _make_user(db, "sca-patch2@example.com", "admin")
    missing_id = uuid.uuid4()
    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{missing_id}",
        json={"pair_role": "primary"},
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 6. PATCH: staff role -> 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pair_role_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, "sca-patch3@example.com", "staff")
    trainee = await _make_staff(db, "新人C", is_trainee=True)
    companion = await _make_staff(db, "先輩D")
    row = await _make_assignment(db, trainee_id=trainee.id, companion_id=companion.id)

    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": "primary"},
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7. PATCH: extra='forbid' rejects unknown field -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pair_role_extra_field_422(client, db) -> None:
    admin = await _make_user(db, "sca-patch4@example.com", "admin")
    trainee = await _make_staff(db, "新人E", is_trainee=True)
    companion = await _make_staff(db, "先輩F")
    row = await _make_assignment(db, trainee_id=trainee.id, companion_id=companion.id)

    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": "primary", "weekday": 3},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 8. PATCH: invalid pair_role literal -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_pair_role_invalid_value_422(client, db) -> None:
    admin = await _make_user(db, "sca-patch5@example.com", "admin")
    trainee = await _make_staff(db, "新人G", is_trainee=True)
    companion = await _make_staff(db, "先輩H")
    row = await _make_assignment(db, trainee_id=trainee.id, companion_id=companion.id)

    res = await client.patch(
        f"/api/v1/staff-companion-assignments/{row.id}",
        json={"pair_role": "lead"},  # not in {'primary','support',null}
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
