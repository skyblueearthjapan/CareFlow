"""W3-BE-FIX: POST /api/v1/schedule/fix tests.

設計仕様書 v0.9 §3.6.2 / API 契約 §6.1 / 実装手順書 §4 W3-BE-FIX に対応。

検証観点:
  1. 単一患者の weekly_pattern.entries 更新
  2. 複数患者の一括更新 (10 名分)
  3. staff_count=2 を含むレイアウト
  4. 既存パターンとの差分のみ書込み (no-op の場合は patients_updated=0)
  5. RBAC: staff は 403, no token は 401
  6. トランザクションロールバック: 1 患者でも失敗すれば全 rollback
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _make_patient(
    db,
    *,
    code: str,
    weekly_pattern: dict[str, Any] | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者 {code}",
        status="active",
        weekly_pattern=weekly_pattern,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _fixed_visit(
    *,
    patient_id: UUID,
    weekday: int = 0,
    start_time: str = "09:00",
    end_time: str = "10:00",
    staff_count: int = 1,
) -> dict[str, Any]:
    return {
        "patient_id": str(patient_id),
        "weekday": weekday,
        "start_time": start_time,
        "end_time": end_time,
        "staff_count": staff_count,
    }


def _fix_payload(*visits: dict[str, Any], iso_year: int = 2026, iso_week: int = 20) -> dict:
    return {
        "iso_year": iso_year,
        "iso_week": iso_week,
        "layout": list(visits),
    }


# ---------------------------------------------------------------------------
# 1) 単一患者の weekly_pattern 更新
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_single_patient_persists_weekly_pattern(client, db) -> None:
    admin = await _make_user(db, "sf-1-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-001")

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id, weekday=0, start_time="09:00", end_time="09:45"),
        ),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patients_updated"] == 1
    assert len(body["weekly_pattern_changes"]) == 1
    change = body["weekly_pattern_changes"][0]
    assert change["patient_id"] == str(patient.id)
    assert change["entries_before"] == []
    after = change["entries_after"]
    assert len(after) == 1
    assert after[0]["weekday"] == "Mon"
    assert after[0]["preferred_start"] == "09:00"
    assert after[0]["preferred_end"] == "09:45"
    assert after[0]["service_minutes"] == 45
    assert after[0]["staff_count"] == 1

    # ORM round-trip: weekly_pattern.entries が DB に残っていること
    await db.refresh(patient)
    assert patient.weekly_pattern is not None
    entries = patient.weekly_pattern.get("entries", [])
    assert len(entries) == 1
    assert entries[0]["weekday"] == "Mon"
    assert entries[0]["preferred_start"] == "09:00"
    assert entries[0]["staff_count"] == 1


# ---------------------------------------------------------------------------
# 2) 複数患者の一括更新 (10 名分)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_bulk_updates_ten_patients(client, db) -> None:
    admin = await _make_user(db, "sf-2-admin@example.com", "admin")
    patients: list[Patient] = []
    for i in range(10):
        patients.append(await _make_patient(db, code=f"SF-BULK-{i:03d}"))

    visits = [
        _fixed_visit(
            patient_id=p.id,
            weekday=i % 5,  # Mon..Fri を巡回
            start_time=f"{9 + (i % 4):02d}:00",
            end_time=f"{9 + (i % 4):02d}:45",
            staff_count=1,
        )
        for i, p in enumerate(patients)
    ]

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(*visits),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patients_updated"] == 10
    assert len(body["weekly_pattern_changes"]) == 10

    # 全患者の weekly_pattern が更新されていること
    for p in patients:
        await db.refresh(p)
        assert p.weekly_pattern is not None
        assert len(p.weekly_pattern.get("entries", [])) == 1


# ---------------------------------------------------------------------------
# 3) staff_count=2 を含むレイアウト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_staff_count_two_persists(client, db) -> None:
    admin = await _make_user(db, "sf-3-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-002")

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(
                patient_id=patient.id,
                weekday=2,  # Wed
                start_time="14:00",
                end_time="15:00",
                staff_count=2,
            ),
        ),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patients_updated"] == 1
    after = body["weekly_pattern_changes"][0]["entries_after"]
    assert after[0]["weekday"] == "Wed"
    assert after[0]["staff_count"] == 2
    assert after[0]["service_minutes"] == 60

    await db.refresh(patient)
    assert patient.weekly_pattern["entries"][0]["staff_count"] == 2


# ---------------------------------------------------------------------------
# 4) 既存パターンとの差分のみ書込み
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_no_op_when_pattern_unchanged(client, db) -> None:
    """同じ entries を 2 回送ると 2 回目は patients_updated=0."""
    admin = await _make_user(db, "sf-4-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-003")

    payload = _fix_payload(
        _fixed_visit(
            patient_id=patient.id,
            weekday=0,
            start_time="09:00",
            end_time="09:45",
            staff_count=1,
        ),
    )

    # 1 回目: 新規保存
    res1 = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=payload,
    )
    assert res1.status_code == 200, res1.text
    assert res1.json()["patients_updated"] == 1

    # 2 回目: 同一レイアウト → 差分無し → 0 件
    res2 = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=payload,
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["patients_updated"] == 0
    assert body2["weekly_pattern_changes"] == []


@pytest.mark.asyncio
async def test_fix_partial_diff_only_changed_patient_counted(client, db) -> None:
    """2 患者中 1 患者のみ変化 → patients_updated=1."""
    admin = await _make_user(db, "sf-4b-admin@example.com", "admin")

    # patient_a は事前に Mon 09:00 entries で初期化
    initial_pattern = {
        "frequency_per_week": 1,
        "entries": [
            {
                "weekday": "Mon",
                "time_type": "固定",
                "preferred_start": "09:00",
                "preferred_end": "09:45",
                "service_minutes": 45,
                "staff_count": 1,
            }
        ],
    }
    patient_a = await _make_patient(db, code="SF-004A", weekly_pattern=initial_pattern)
    patient_b = await _make_patient(db, code="SF-004B")

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            # patient_a は同じ内容 → no-op
            _fixed_visit(
                patient_id=patient_a.id,
                weekday=0,
                start_time="09:00",
                end_time="09:45",
                staff_count=1,
            ),
            # patient_b は新規 → 変化あり
            _fixed_visit(
                patient_id=patient_b.id,
                weekday=1,
                start_time="10:00",
                end_time="10:45",
                staff_count=1,
            ),
        ),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patients_updated"] == 1
    changes = body["weekly_pattern_changes"]
    assert len(changes) == 1
    assert changes[0]["patient_id"] == str(patient_b.id)

    # frequency_per_week などのトップレベルキーは患者 A で維持されていること
    await db.refresh(patient_a)
    assert patient_a.weekly_pattern is not None
    assert patient_a.weekly_pattern.get("frequency_per_week") == 1


# ---------------------------------------------------------------------------
# 5) RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_no_token_returns_401(client) -> None:
    res = await client.post(
        "/api/v1/schedule/fix",
        json=_fix_payload(),
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_fix_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "sf-5-staff@example.com", "staff")
    patient = await _make_patient(db, code="SF-005")
    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(staff),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id),
        ),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_fix_manager_allowed(client, db) -> None:
    manager = await _make_user(db, "sf-5b-mgr@example.com", "manager")
    patient = await _make_patient(db, code="SF-006")
    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(manager),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id),
        ),
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# 6) トランザクションロールバック (失敗時)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_unknown_patient_rolls_back_all(client, db) -> None:
    """未知の patient_id が混じれば 404 + 既存変更を全 rollback."""
    admin = await _make_user(db, "sf-6-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-007")
    bogus_id = uuid4()

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id, weekday=0),
            _fixed_visit(patient_id=bogus_id, weekday=1),
        ),
    )
    assert res.status_code == 404, res.text

    # rollback されたので weekly_pattern は触られていないこと
    fresh = await db.scalar(select(Patient).where(Patient.id == patient.id))
    assert fresh is not None
    assert fresh.weekly_pattern is None


@pytest.mark.asyncio
async def test_fix_invalid_time_window_returns_422_and_rolls_back(client, db) -> None:
    """end_time <= start_time → 422 + 全 rollback."""
    admin = await _make_user(db, "sf-6b-admin@example.com", "admin")
    patient_a = await _make_patient(db, code="SF-008A")
    patient_b = await _make_patient(db, code="SF-008B")

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(patient_id=patient_a.id, weekday=0),
            # patient_b は start == end の不正データ -> service 層で 422.
            _fixed_visit(
                patient_id=patient_b.id,
                weekday=1,
                start_time="10:00",
                end_time="10:00",
            ),
        ),
    )
    assert res.status_code == 422, res.text

    # 両患者とも weekly_pattern は触られていないこと
    fresh_a = await db.scalar(select(Patient).where(Patient.id == patient_a.id))
    fresh_b = await db.scalar(select(Patient).where(Patient.id == patient_b.id))
    assert fresh_a is not None and fresh_a.weekly_pattern is None
    assert fresh_b is not None and fresh_b.weekly_pattern is None


@pytest.mark.asyncio
async def test_fix_duplicate_slot_for_same_patient_returns_422(client, db) -> None:
    """同一患者×同一曜日×同一時刻が 2 行 → 422 (整合性違反)."""
    admin = await _make_user(db, "sf-6c-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-009")

    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(
                patient_id=patient.id,
                weekday=2,
                start_time="09:00",
                end_time="10:00",
            ),
            _fixed_visit(
                patient_id=patient.id,
                weekday=2,
                start_time="09:00",
                end_time="11:00",
                staff_count=2,
            ),
        ),
    )
    assert res.status_code == 422, res.text

    fresh = await db.scalar(select(Patient).where(Patient.id == patient.id))
    assert fresh is not None and fresh.weekly_pattern is None


# ---------------------------------------------------------------------------
# 7) Validation: weekday range / staff_count range / time format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_invalid_weekday_returns_422(client, db) -> None:
    admin = await _make_user(db, "sf-7-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-010")
    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id, weekday=7),  # 6 を超える
        ),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_fix_invalid_staff_count_returns_422(client, db) -> None:
    admin = await _make_user(db, "sf-7b-admin@example.com", "admin")
    patient = await _make_patient(db, code="SF-011")
    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(
            _fixed_visit(patient_id=patient.id, staff_count=3),
        ),
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_fix_empty_layout_returns_zero(client, db) -> None:
    """layout=[] の場合は 200 + patients_updated=0."""
    admin = await _make_user(db, "sf-7c-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/schedule/fix",
        headers=_bearer(admin),
        json=_fix_payload(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patients_updated"] == 0
    assert body["weekly_pattern_changes"] == []
