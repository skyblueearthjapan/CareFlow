"""Smoke tests for /api/v1/diff/compute (Phase 4-6/4-7/4-16, W1-E)."""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User

CSV_HEADER = (
    "職員名1,職種1,職員名2,職種2,同行2,職員名3,職種3,同行3,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,"
    "開始時間,終了時間,提供時間,備考\n"
)


def _kaipoke_row(
    *,
    date: str = "1",
    user: str = "P1",
    svc: str = "A",
    start: str = "09:00",
    end: str = "10:00",
) -> str:
    return f"A田,看護師,,,,,,,事業所A,{date},月,{user},医療保険,{svc},{start},{end},60,\n"


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_diff_compute_admin_returns_200_with_edit(client, db) -> None:
    admin = await _make_user(db, "diff-admin@example.com", "admin")
    cur = CSV_HEADER + _kaipoke_row(start="09:00", end="10:00")
    opt = CSV_HEADER + _kaipoke_row(start="11:00", end="12:00")

    res = await client.post(
        "/api/v1/diff/compute",
        headers=_bearer(admin),
        json={
            "current_csv": cur,
            "optimized_csv": opt,
            "target_week_start": 1,
            "target_week_end": 7,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "summary" in body and "edits" in body
    assert any(e["action"] == "edit" for e in body["edits"])
    assert body["summary"]["total"] == len(body["edits"])


@pytest.mark.asyncio
async def test_diff_compute_staff_returns_403(client, db) -> None:
    staff_user = await _make_user(db, "diff-staff@example.com", "staff")
    cur = CSV_HEADER + _kaipoke_row()
    opt = CSV_HEADER + _kaipoke_row(start="11:00", end="12:00")

    res = await client.post(
        "/api/v1/diff/compute",
        headers=_bearer(staff_user),
        json={
            "current_csv": cur,
            "optimized_csv": opt,
            "target_week_start": 1,
            "target_week_end": 7,
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_diff_compute_no_token_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/diff/compute",
        json={
            "current_csv": "x",
            "optimized_csv": "y",
            "target_week_start": 1,
            "target_week_end": 7,
        },
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_diff_compute_malformed_payload_returns_422(client, db) -> None:
    """Missing required fields (week range) → pydantic 422."""
    admin = await _make_user(db, "diff-admin2@example.com", "admin")
    res = await client.post(
        "/api/v1/diff/compute",
        headers=_bearer(admin),
        json={
            "current_csv": "x",
            "optimized_csv": "y",
            # missing target_week_start / target_week_end
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_diff_compute_out_of_range_week_returns_422(client, db) -> None:
    """target_week_* must be 1..31 — boundary check."""
    admin = await _make_user(db, "diff-admin3@example.com", "admin")
    res = await client.post(
        "/api/v1/diff/compute",
        headers=_bearer(admin),
        json={
            "current_csv": "x",
            "optimized_csv": "y",
            "target_week_start": 0,
            "target_week_end": 7,
        },
    )
    assert res.status_code == 422, res.text
