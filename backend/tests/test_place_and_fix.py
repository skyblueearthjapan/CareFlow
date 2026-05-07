"""W15-BE-FIXPATTERN (Phase 2 C-1): POST /api/v1/schedule/place-and-fix tests.

Wave 15 で新設された「ドロップ即固定枠化」フロー専用 endpoint の挙動を検証。

検証観点:
  1. 正常系: place-and-fix で visit + fixed_visit が両方作成される
  2. fix_pattern=False: visit のみ作成、fixed_visit は null
  3. RBAC: staff は 403
  4. RBAC: viewer は 403
  5. patient 不存在 → 404
  6. 同一 patient × weekday × normal を連続 call → fixed_visit が upsert される
  7. ISO 週越境 (例: iso_week=53) も正常動作
  8. special_week_active 該当週は mode='special'
  9. start_time + duration_min が 24:00 を越えると 422
  10. weekday の範囲外 (-1 / 7) は 422
  11. patient_id 空文字列など型不正は 422
  12. 不存在 ISO 週 (例: 2025 / week=53) は 422
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit

# 共通テスト週: ISO 2026-W19 (月曜 = 2026-05-04)
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
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


async def _make_patient(
    db,
    code: str,
    *,
    special_week_active: list | None = None,
    status: str = "active",
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status=status,
        special_week_active=special_week_active or [],
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _payload(
    patient_id,
    *,
    iso_year: int = TEST_ISO_YEAR,
    iso_week: int = TEST_ISO_WEEK,
    weekday: int = 0,
    start_time: str = "09:00:00",
    duration_min: int = 60,
    staff_count: int = 1,
    fix_pattern: bool = True,
) -> dict:
    return {
        "patient_id": str(patient_id),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "weekday": weekday,
        "start_time": start_time,
        "duration_min": duration_min,
        "staff_count": staff_count,
        "fix_pattern": fix_pattern,
    }


# ---------------------------------------------------------------------------
# 1. 正常系: visit + fixed_visit が両方作成される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_creates_visit_and_fixed_visit(client, db) -> None:
    """fix_pattern=True (default): visit + fixed_visit が両方作成される."""
    admin = await _make_user(db, "paf-1-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-001")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=0, start_time="10:00:00", duration_min=45),
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # visit
    assert data["visit"] is not None
    assert data["visit"]["patient_id"] == str(patient.id)
    assert data["visit"]["visit_date"] == TEST_WEEK_MONDAY.isoformat()
    assert data["visit"]["start_time"] == "10:00:00"
    assert data["visit"]["end_time"] == "10:45:00"
    assert data["visit"]["status"] == "planned"
    assert data["visit"]["source"] == "manual"
    assert data["visit"]["required_staff_count"] == 1

    # fixed_visit
    assert data["fixed_visit"] is not None
    assert data["fixed_visit"]["mode"] == "normal"
    assert data["fixed_visit"]["weekday"] == 0
    assert data["fixed_visit"]["duration_min"] == 45

    # DB 確認
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 1

    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].mode == "normal"
    assert fvs[0].weekday == 0


# ---------------------------------------------------------------------------
# 2. fix_pattern=False: visit のみ作成、fixed_visit は null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_without_pattern_creates_visit_only(client, db) -> None:
    """fix_pattern=False: visit のみ作成、patient_fixed_visits は触らない."""
    admin = await _make_user(db, "paf-2-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-002")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=1, start_time="11:00:00", fix_pattern=False),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["visit"] is not None
    assert data["fixed_visit"] is None

    # DB: fixed_visits は 0 件
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 0


# ---------------------------------------------------------------------------
# 3. RBAC: staff → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_staff_forbidden(client, db) -> None:
    """staff ロールは place-and-fix を呼べない (403)."""
    staff = await _make_staff(db, "スタッフPAF3")
    staff_user = await _make_user(db, "paf-3-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(staff_user),
        json=_payload(uuid4()),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 4. RBAC: viewer → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_viewer_forbidden(client, db) -> None:
    viewer = await _make_user(db, "paf-4-viewer@example.com", "viewer")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(viewer),
        json=_payload(uuid4()),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 5. patient 不存在 → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_unknown_patient_returns_404(client, db) -> None:
    admin = await _make_user(db, "paf-5-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(uuid4()),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 6. 同一 patient × weekday の連続 call → fixed_visit が upsert される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_upserts_existing_fixed_visit(client, db) -> None:
    """同一 (patient_id, mode='normal', weekday) を 2 回 call すると fixed_visit
    が DELETE→INSERT で 1 行のまま、最新の値で更新される."""
    admin = await _make_user(db, "paf-6-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-006")

    # 1 回目
    r1 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=2, start_time="09:00:00", duration_min=30),
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["fixed_visit"]["duration_min"] == 30

    # 2 回目: 同じ weekday=2 を別時刻 / 別 duration で再投入
    r2 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=2, start_time="14:00:00", duration_min=60),
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["fixed_visit"]["duration_min"] == 60
    assert body2["fixed_visit"]["weekday"] == 2

    # DB: weekday=2 / mode='normal' は 1 行 (= 上書きされた)
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.weekday == 2,
            )
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].start_time == time(14, 0)
    assert fvs[0].duration_min == 60

    # visits は 2 行作成される (place-and-fix は visit を毎回作る)
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 2


# ---------------------------------------------------------------------------
# 7. ISO 週越境: 2026-W53 (= 2026-12-28) でも正常動作
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_iso_week_53(client, db) -> None:
    """2026 は 53 週まで存在する → iso_week=53 でも 200."""
    admin = await _make_user(db, "paf-7-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-007")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            iso_year=2026,
            iso_week=53,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # 2026-W53 月曜 = 2026-12-28
    assert data["visit"]["visit_date"] == "2026-12-28"


# ---------------------------------------------------------------------------
# 8. special_week_active 該当週は mode='special'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_special_mode(client, db) -> None:
    admin = await _make_user(db, "paf-8-admin@example.com", "admin")
    patient = await _make_patient(
        db,
        "PAF-008",
        special_week_active=[{"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK}],
    )

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=3, start_time="13:00:00", duration_min=30),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["fixed_visit"]["mode"] == "special"

    # DB 確認: normal 側は 0 件
    normal_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
            )
        )
    ).all()
    assert len(normal_rows) == 0


# ---------------------------------------------------------------------------
# 9. start_time + duration_min > 24:00 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_overflow_24h_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-9-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-009")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=0, start_time="23:30:00", duration_min=60),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 10. weekday 範囲外 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_weekday_out_of_range(client, db) -> None:
    admin = await _make_user(db, "paf-10-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-010")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=7),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 11. patient_id 空文字列 → 422 (UUID バリデーション)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_empty_patient_id_returns_422(client, db) -> None:
    """patient_id='' は UUID バリデーションで 422."""
    admin = await _make_user(db, "paf-11-admin@example.com", "admin")
    payload = _payload(uuid4())
    payload["patient_id"] = ""
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=payload,
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 12. 不存在 ISO 週 (2025-W53 は存在しない) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_invalid_iso_week_returns_422(client, db) -> None:
    """2025 は 52 週までなので W53 は ValueError → 422."""
    admin = await _make_user(db, "paf-12-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-012")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, iso_year=2025, iso_week=53),
    )
    assert res.status_code == 422, res.text
