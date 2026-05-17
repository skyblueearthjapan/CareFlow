"""Tests for /api/v1/schedule/v2/* endpoints (Wave 41 v2.0).

設計仕様書: ``docs/plans/auto-schedule-v2.md`` (v0.2)
"""

from __future__ import annotations

from datetime import time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
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


async def _seed_office_with_staff(db) -> tuple[Office, Staff]:
    office = Office(name="v2-api-office")
    db.add(office)
    await db.flush()
    s = Staff(
        name="v2-api-staff",
        role="staff",
        is_trainee=False,
        primary_office_id=office.id,
    )
    db.add(s)
    await db.flush()
    # Mon-Fri 稼働
    for wd in range(5):
        db.add(StaffShift(staff_id=s.id, weekday=wd, is_on=True))
    await db.commit()
    return office, s


async def _seed_patient(
    db,
    *,
    office: Office,
    code: str,
    lat: float = 35.65,
    lng: float = 140.10,
    weekly_pattern: dict[str, Any] | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern
        or {
            "preferred_weekdays": ["Mon"],
            "preferred_start": "10:00",
            "service_minutes": 30,
            "time_type": "固定",
        },
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# /v2/diff-add
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_add_returns_pool_proposals(client, db) -> None:
    admin = await _make_user(db, email="v2-da-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    # 固定枠ありの患者 (pool 対象外)
    p_fixed = await _seed_patient(db, office=office, code="DA-F1")
    db.add(
        PatientFixedVisit(
            patient_id=p_fixed.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    # 固定枠なしの患者 (pool 対象)
    await _seed_patient(db, office=office, code="DA-P1", lat=35.66, lng=140.11)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/diff-add",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "proposal_batch_id" in body
    assert "proposals" in body
    pool_codes = {p["patient_code"] for p in body["proposals"]}
    assert "DA-P1" in pool_codes
    assert "DA-F1" not in pool_codes


@pytest.mark.asyncio
async def test_diff_add_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="v2-da-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/diff-add",
        headers=_bearer(staff_user),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": []},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_diff_add_rejects_no_auth(client, db) -> None:
    res = await client.post(
        "/api/v1/schedule/v2/diff-add",
        json={"iso_year": 2026, "iso_week": 20, "office_ids": []},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_diff_add_rejects_bad_iso_year(client, db) -> None:
    admin = await _make_user(db, email="v2-da-bad@example.com", role="admin")
    res = await client.post(
        "/api/v1/schedule/v2/diff-add",
        headers=_bearer(admin),
        json={"iso_year": 1990, "iso_week": 20, "office_ids": []},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# /v2/full-optimize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_optimize_returns_week_proposals(client, db) -> None:
    admin = await _make_user(db, email="v2-fo-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p1 = await _seed_patient(db, office=office, code="FO-1", lat=35.65, lng=140.10)
    await _seed_patient(db, office=office, code="FO-2", lat=35.66, lng=140.11)
    db.add(
        PatientFixedVisit(
            patient_id=p1.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "week_proposals" in body
    # week_proposals は 7 曜日分
    assert len(body["week_proposals"]) == 7
    assert "kpi_overall" in body
    # H10 違反件数キー
    assert "H10" in body["kpi_overall"]["h_violations"]


# ---------------------------------------------------------------------------
# /v2/apply-individual
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_individual_creates_pfv(client, db) -> None:
    admin = await _make_user(db, email="v2-ap-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="AP-1")
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "duration_min": 30,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "am_pm": "am",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    assert body["idempotent"] is False
    assert len(body["fixed_visit_ids"]) == 1

    # DB 状態を確認
    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert len(pfv_rows) == 1


@pytest.mark.asyncio
async def test_apply_individual_is_idempotent(client, db) -> None:
    admin = await _make_user(db, email="v2-id-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="ID-1")
    await db.commit()

    plans = [
        {
            "weekday": 1,
            "start_time": "09:30",
            "end_time": "10:00",
            "duration_min": 30,
            "course_code": "A",
            "office_id": str(office.id),
            "am_pm": "am",
        }
    ]
    res1 = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={"patient_id": str(p.id), "confirm": True, "visit_plans": plans},
    )
    assert res1.status_code == 200
    res2 = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={"patient_id": str(p.id), "confirm": True, "visit_plans": plans},
    )
    assert res2.status_code == 200
    assert res2.json()["idempotent"] is True


@pytest.mark.asyncio
async def test_apply_individual_rejects_no_confirm(client, db) -> None:
    admin = await _make_user(db, email="v2-nc-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="NC-1")
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": False,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "duration_min": 30,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "am_pm": "am",
                }
            ],
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_apply_individual_rejects_empty_plans(client, db) -> None:
    admin = await _make_user(db, email="v2-ep-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="EP-1")
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [],
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_apply_individual_rejects_lunch_break_visit(client, db) -> None:
    """H-Codex-2 regression: 昼休憩 12:00-13:00 と重なる visit_plan は 422."""
    admin = await _make_user(db, email="v2-h10-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="H10-1")
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [
                {
                    "weekday": 0,
                    "start_time": "12:15",  # 昼休憩 12:00-13:00 と重なる
                    "end_time": "12:45",
                    "duration_min": 30,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "am_pm": "pm",
                }
            ],
        },
    )
    assert res.status_code == 422, res.text
    assert "H10" in res.text


# ---------------------------------------------------------------------------
# /v2/reset-to-fixed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_to_fixed_regenerates_visits(client, db) -> None:
    """対象週の visits が patient_fixed_visits から再生成される."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-rs-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="RS-1")
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    # 既存 visit (この週の月曜の関係無い枠) — reset で soft-delete される.
    # W41 v2 final cross-review (C-Codex-2): source="manual" は保護対象なので、
    # 削除を確認するため自動生成 source="auto_alloc" を使う.
    existing = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(14, 0),
        end_time=time(15, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add(existing)
    await db.commit()
    existing_id = existing.id

    res = await client.post(
        "/api/v1/schedule/v2/reset-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visits_regenerated"] >= 1
    assert body["visits_soft_deleted"] >= 1

    # 既存 visit は soft-delete
    refreshed = await db.scalar(select(Visit).where(Visit.id == existing_id))
    assert refreshed is not None
    assert refreshed.deleted_at is not None


@pytest.mark.asyncio
async def test_reset_to_fixed_rejects_staff(client, db) -> None:
    staff_user = await _make_user(db, email="v2-rs-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/reset-to-fixed",
        headers=_bearer(staff_user),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": []},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# /v2/apply-week-only (この週だけ反映)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_week_only_creates_visits_without_touching_pfv(client, db) -> None:
    """apply-week-only は visits を作成し、patient_fixed_visits は変更しない."""
    from datetime import date

    from app.models.visit import Visit

    admin = await _make_user(db, email="v2-wo-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="WO-1")
    # 既存 PFV (apply-week-only でも変更されないことを後で検証)
    existing_pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(existing_pfv)
    await db.commit()
    existing_pfv_start_before = existing_pfv.start_time
    existing_pfv_duration_before = existing_pfv.duration_min

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 1,  # 火曜 (PFV と違う曜日にして visit を作る)
                            "start_time": "14:30",
                            "end_time": "15:00",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "pm",
                        }
                    ],
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["iso_year"] == 2026
    assert body["iso_week"] == 20
    assert body["visits_created"] >= 1

    # PFV は変更されていない
    refreshed_pfv = await db.scalar(
        select(PatientFixedVisit).where(PatientFixedVisit.id == existing_pfv.id)
    )
    assert refreshed_pfv is not None
    assert refreshed_pfv.start_time == existing_pfv_start_before
    assert refreshed_pfv.duration_min == existing_pfv_duration_before

    # 新規 visit が source="auto_alloc_v2w" で作成されている
    week_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 12),  # Tue W20
                Visit.source == "auto_alloc_v2w",
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(week_visits) == 1
    assert week_visits[0].start_time == time(14, 30)


@pytest.mark.asyncio
async def test_apply_week_only_rejects_no_confirm(client, db) -> None:
    """confirm=False は 400 (Literal[True] schema 検証 → 422 のところを endpoint で 400)."""
    admin = await _make_user(db, email="v2-wonc-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="WONC-1")
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "10:00",
                            "end_time": "10:30",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "am",
                        }
                    ],
                }
            ],
            "confirm": False,
        },
    )
    # Pydantic Literal[True] が False を弾くので 422 (FastAPI validation).
    assert res.status_code in (400, 422), res.text


@pytest.mark.asyncio
async def test_apply_week_only_soft_deletes_existing_visits(client, db) -> None:
    """対象週の既存 active visits は soft-delete されてから INSERT される."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-wosd-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="WOSD-1")
    existing = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(9, 30),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add(existing)
    await db.commit()
    existing_id = existing.id

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "11:00",
                            "end_time": "11:30",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "am",
                        }
                    ],
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visits_soft_deleted"] >= 1
    assert body["visits_created"] >= 1

    refreshed = await db.scalar(select(Visit).where(Visit.id == existing_id))
    assert refreshed is not None
    assert refreshed.deleted_at is not None


@pytest.mark.asyncio
async def test_apply_week_only_rejects_staff(client, db) -> None:
    """staff ロールは 403."""
    staff_user = await _make_user(db, email="v2-wo-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(staff_user),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [],
            "visit_plans_per_patient": [],
            "confirm": True,
        },
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# P1: apply_week_only DELETE 限定 (本質バグ修正)
# - unassigned 患者の旧 visit を保護する
# - visit_plans に含まれる patient のみ DELETE 対象
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_week_only_preserves_unassigned_patient_old_visits(client, db) -> None:
    """P1: visit_plans に含まれない unassigned 患者の旧 visit は保護される (本質バグ修正)."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-p1-pres-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    # 2 名の active 患者: 1 名は visit_plans に含まれる, もう 1 名は unassigned.
    p_assigned = await _seed_patient(db, office=office, code="P1-AS")
    p_unassigned = await _seed_patient(db, office=office, code="P1-UN")

    # 両者に旧 visit を仕込む (auto_alloc 由来 = DELETE 対象 source).
    old_assigned = Visit(
        patient_id=p_assigned.id,
        visit_date=date(2026, 5, 11),  # Mon W20
        start_time=time(9, 30),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    old_unassigned = Visit(
        patient_id=p_unassigned.id,
        visit_date=date(2026, 5, 11),
        start_time=time(11, 0),
        end_time=time(11, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add_all([old_assigned, old_unassigned])
    await db.commit()
    old_unassigned_id = old_unassigned.id

    # apply: assigned のみ visit_plans に含める. unassigned は含めない.
    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p_assigned.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "14:30",
                            "end_time": "15:00",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "pm",
                        }
                    ],
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text

    # P1 検証: unassigned 患者の旧 visit が保護されている (deleted_at is None).
    preserved = await db.scalar(select(Visit).where(Visit.id == old_unassigned_id))
    assert preserved is not None
    assert preserved.deleted_at is None, (
        "P1 本質バグ: unassigned 患者の旧 visit が誤って soft-delete されている"
    )


@pytest.mark.asyncio
async def test_apply_week_only_replaces_only_assigned_patient_visits(client, db) -> None:
    """P1: visit_plans に含まれる patient のみ旧 visit が削除され、新 visit が INSERT される."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-p1-repl-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p_assigned = await _seed_patient(db, office=office, code="P1-REPL-AS")
    p_unassigned = await _seed_patient(db, office=office, code="P1-REPL-UN")
    old_assigned = Visit(
        patient_id=p_assigned.id,
        visit_date=date(2026, 5, 11),
        start_time=time(9, 30),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    old_unassigned = Visit(
        patient_id=p_unassigned.id,
        visit_date=date(2026, 5, 11),
        start_time=time(11, 0),
        end_time=time(11, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
    )
    db.add_all([old_assigned, old_unassigned])
    await db.commit()
    old_assigned_id = old_assigned.id

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p_assigned.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "14:30",
                            "end_time": "15:00",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "pm",
                        }
                    ],
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # assigned 患者の旧 visit のみ soft-delete されている (1 件).
    assert body["visits_soft_deleted"] == 1
    assert body["visits_created"] >= 1

    # 検証: assigned 旧 visit は deleted_at セット済み, unassigned 旧 visit は active のまま.
    refreshed_assigned = await db.scalar(select(Visit).where(Visit.id == old_assigned_id))
    assert refreshed_assigned is not None
    assert refreshed_assigned.deleted_at is not None

    # 新規 visit は assigned 患者のみに INSERT される (unassigned には新規無し).
    assigned_new = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p_assigned.id,
                Visit.source == "auto_alloc_v2w",
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(assigned_new) == 1

    unassigned_new = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p_unassigned.id,
                Visit.source == "auto_alloc_v2w",
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(unassigned_new) == 0


@pytest.mark.asyncio
async def test_apply_week_only_emits_warning_when_unassigned_old_visits_preserved(
    client, db
) -> None:
    """P1: unassigned 患者の旧 visit を保護した旨を warning に出す."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-p1-warn-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p_assigned = await _seed_patient(db, office=office, code="P1-WARN-AS")
    p_unassigned = await _seed_patient(db, office=office, code="P1-WARN-UN")
    db.add(
        Visit(
            patient_id=p_unassigned.id,
            visit_date=date(2026, 5, 11),
            start_time=time(11, 0),
            end_time=time(11, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto_alloc",
            required_staff_count=1,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p_assigned.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "14:30",
                            "end_time": "15:00",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "pm",
                        }
                    ],
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # P1: 「未割当 ... 旧 visit ... 件を保持しました」warning が出ていること.
    warnings = body["warnings"]
    assert any("未割当" in w and "保持" in w for w in warnings), (
        f"P1 unassigned 旧 visit 保護 warning が見つからない: {warnings}"
    )


# ---------------------------------------------------------------------------
# H-Codex-3: v1 endpoints return 410 Gone
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v1_auto_allocate_returns_410(client, db) -> None:
    """H-Codex-3 regression: /api/v1/schedule/auto-allocate は 410 Gone."""
    admin = await _make_user(db, email="v1-aa-admin@example.com", role="admin")
    res = await client.post(
        "/api/v1/schedule/auto-allocate",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [], "mode": "mode_1"},
    )
    assert res.status_code == 410, res.text


@pytest.mark.asyncio
async def test_v1_proposal_apply_returns_410(client, db) -> None:
    """H-Codex-3 regression: /api/v1/schedule/proposal/{id}/apply は 410 Gone."""
    import uuid as _uuid

    admin = await _make_user(db, email="v1-pa-admin@example.com", role="admin")
    res = await client.post(
        f"/api/v1/schedule/proposal/{_uuid.uuid4()}/apply",
        headers=_bearer(admin),
    )
    assert res.status_code == 410, res.text


@pytest.mark.asyncio
async def test_v1_proposal_discard_returns_410(client, db) -> None:
    """H-Codex-3 regression: /api/v1/schedule/proposal/{id}/discard は 410 Gone."""
    import uuid as _uuid

    admin = await _make_user(db, email="v1-pd-admin@example.com", role="admin")
    res = await client.post(
        f"/api/v1/schedule/proposal/{_uuid.uuid4()}/discard",
        headers=_bearer(admin),
    )
    assert res.status_code == 410, res.text


# ---------------------------------------------------------------------------
# W41 v2 (Mode 2 Before/After 表示拡張) — _group_visits_into_courses の挙動
# ---------------------------------------------------------------------------


def test_group_visits_into_courses_sorts_abc_by_office() -> None:
    """同曜日内で (office_name, code) ABC 順にソートされる."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _group_visits_into_courses
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_a = _uuid.uuid4()
    office_b = _uuid.uuid4()
    office_names = {office_a: "本店(稲毛)", office_b: "都賀支店"}

    def _mk(office_id, code, start_h):
        return V2Visit(
            patient_id=_uuid.uuid4(),
            patient_name=f"p-{code}-{start_h}",
            patient_code=None,
            weekday=0,
            start_time=_time(start_h, 0),
            end_time=_time(start_h + 1, 0),
            service_minutes=30,
            lat=35.65,
            lng=140.10,
            office_id=office_id,
            am_pm="am",
            source_kind="pool",
            course_code=code,
        )

    visits = [
        _mk(office_b, "A", 9),
        _mk(office_a, "C", 9),
        _mk(office_a, "A", 9),
        _mk(office_a, "B", 9),
    ]
    courses = _group_visits_into_courses(visits, office_name_by_id=office_names)
    # 本店(稲毛) A → B → C, then 都賀支店 A
    assert [(c.office_name, c.code) for c in courses] == [
        ("本店(稲毛)", "A"),
        ("本店(稲毛)", "B"),
        ("本店(稲毛)", "C"),
        ("都賀支店", "A"),
    ]


def test_group_visits_into_courses_sets_office_name() -> None:
    """office_name_by_id から V2CourseSummary.office_name がセットされる."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _group_visits_into_courses
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_id = _uuid.uuid4()
    v = V2Visit(
        patient_id=_uuid.uuid4(),
        patient_name="x",
        patient_code=None,
        weekday=0,
        start_time=_time(9, 0),
        end_time=_time(10, 0),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
        course_code="A",
        time_type="午前",
        sex_restriction="female_only",
    )
    courses = _group_visits_into_courses([v], office_name_by_id={office_id: "本店(稲毛)"})
    assert len(courses) == 1
    assert courses[0].office_name == "本店(稲毛)"
    # visit にも time_type / sex_restriction が流れる
    assert courses[0].visits[0].time_type == "午前"
    assert courses[0].visits[0].sex_restriction == "female_only"


@pytest.mark.asyncio
async def test_full_optimize_response_includes_office_name(client, db) -> None:
    """/full-optimize レスポンスの course に office_name が含まれる."""
    admin = await _make_user(db, email="v2-foon-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    await _seed_patient(db, office=office, code="FOON-1", lat=35.65, lng=140.10)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    seen_office_name = False
    for wp in body["week_proposals"]:
        for c in wp["after"]["courses"]:
            assert c["office_name"] == office.name
            seen_office_name = True
    assert seen_office_name, "少なくとも 1 つの after course に office_name が含まれるべき"


# ---------------------------------------------------------------------------
# W41 v2 — H2 視覚化: same_address_group_id 割当
# ---------------------------------------------------------------------------


def test_assign_same_address_groups_two_visits_get_id() -> None:
    """同 (office, weekday, start_time, address_bucket) で 2 名 → 共通 group_id."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _assign_same_address_groups
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_id = _uuid.uuid4()
    common: dict[str, Any] = {
        "patient_code": None,
        "weekday": 0,
        "start_time": _time(9, 30),
        "end_time": _time(10, 0),
        "service_minutes": 30,
        "office_id": office_id,
        "am_pm": "am",
        "source_kind": "pool",
    }
    v1 = V2Visit(patient_id=_uuid.uuid4(), patient_name="A", lat=35.65, lng=140.10, **common)
    v2 = V2Visit(patient_id=_uuid.uuid4(), patient_name="B", lat=35.65, lng=140.10, **common)
    mapping = _assign_same_address_groups([v1, v2])
    # 2 件とも同じ group_id が振られる
    key1 = (v1.patient_id, v1.weekday, v1.start_time)
    key2 = (v2.patient_id, v2.weekday, v2.start_time)
    assert key1 in mapping
    assert key2 in mapping
    assert mapping[key1] == mapping[key2]
    assert mapping[key1].startswith("sa_")


def test_assign_same_address_groups_solo_visit_no_id() -> None:
    """単独 visit には group_id は付かない (None 扱い → mapping に key なし)."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _assign_same_address_groups
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    v = V2Visit(
        patient_id=_uuid.uuid4(),
        patient_name="solo",
        patient_code=None,
        weekday=0,
        start_time=_time(9, 30),
        end_time=_time(10, 0),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=_uuid.uuid4(),
        am_pm="am",
        source_kind="pool",
    )
    mapping = _assign_same_address_groups([v])
    assert mapping == {}


def test_assign_same_address_groups_different_start_time_still_grouped() -> None:
    """W41 v2.8: 同住所同 weekday なら start_time が違っても group_id 付与.

    旧仕様では key に start_time が含まれていたため、実動時間や移動時間で
    09:00 / 09:30 のように連番にズレるとペア囲みが消えていた。本テストで
    時刻ズレでも group_id が付与されることを担保する。FE 側で「sort 順の
    隣接判定」により連番のときだけ実際に囲みが描画される。
    """
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _assign_same_address_groups
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_id = _uuid.uuid4()
    common: dict[str, Any] = {
        "patient_code": None,
        "weekday": 0,
        "service_minutes": 30,
        "office_id": office_id,
        "am_pm": "am",
        "source_kind": "pool",
    }
    # 09:00-09:30 患者 A → 09:30-10:00 患者 B (同住所、実動時間で連番にズレ)
    v1 = V2Visit(
        patient_id=_uuid.uuid4(),
        patient_name="A",
        lat=35.65,
        lng=140.10,
        start_time=_time(9, 0),
        end_time=_time(9, 30),
        **common,
    )
    v2 = V2Visit(
        patient_id=_uuid.uuid4(),
        patient_name="B",
        lat=35.65,
        lng=140.10,
        start_time=_time(9, 30),
        end_time=_time(10, 0),
        **common,
    )
    mapping = _assign_same_address_groups([v1, v2])
    key1 = (v1.patient_id, v1.weekday, v1.start_time)
    key2 = (v2.patient_id, v2.weekday, v2.start_time)
    assert key1 in mapping, "同住所同 weekday なら start_time 違っても group_id 付与"
    assert key2 in mapping
    assert mapping[key1] == mapping[key2], "同じ住所バケットなら同 group_id"


def test_assign_same_address_groups_different_address_no_group() -> None:
    """異住所同 start_time なら group_id 付与なし (既存仕様維持)."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _assign_same_address_groups
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_id = _uuid.uuid4()
    common: dict[str, Any] = {
        "patient_code": None,
        "weekday": 0,
        "start_time": _time(9, 30),
        "end_time": _time(10, 0),
        "service_minutes": 30,
        "office_id": office_id,
        "am_pm": "am",
        "source_kind": "pool",
    }
    # 0.005 ≒ 500 m 離れた別住所 (tolerance 0.001 を超過)
    v1 = V2Visit(patient_id=_uuid.uuid4(), patient_name="A", lat=35.650, lng=140.100, **common)
    v2 = V2Visit(patient_id=_uuid.uuid4(), patient_name="B", lat=35.655, lng=140.105, **common)
    mapping = _assign_same_address_groups([v1, v2])
    assert mapping == {}, "異住所なら group_id 付与なし"


def test_group_visits_into_courses_sets_same_address_group_id() -> None:
    """_group_visits_into_courses が同住所 visit に same_address_group_id を埋める."""
    import uuid as _uuid
    from datetime import time as _time

    from app.api.v1.schedule_v2 import _group_visits_into_courses
    from app.services.scheduling.auto_allocator_v2 import V2Visit

    office_id = _uuid.uuid4()
    common: dict[str, Any] = {
        "patient_code": None,
        "weekday": 0,
        "start_time": _time(9, 30),
        "end_time": _time(10, 0),
        "service_minutes": 30,
        "office_id": office_id,
        "am_pm": "am",
        "source_kind": "pool",
        "course_code": "A",
    }
    v1 = V2Visit(patient_id=_uuid.uuid4(), patient_name="A", lat=35.65, lng=140.10, **common)
    v2 = V2Visit(patient_id=_uuid.uuid4(), patient_name="B", lat=35.65, lng=140.10, **common)
    courses = _group_visits_into_courses([v1, v2])
    assert len(courses) == 1
    visits_ui = courses[0].visits
    assert all(vu.same_address_group_id is not None for vu in visits_ui)
    assert visits_ui[0].same_address_group_id == visits_ui[1].same_address_group_id


# ---------------------------------------------------------------------------
# W41 v2 拡張 (構造化警告 + distance_to_next_km + 固定時間変更 API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_optimize_returns_structured_warnings(client, db) -> None:
    """V2Warning は type / actionable / patient_id 等の構造化フィールドを持つ.

    same_address_consolidation 警告が actionable=True で出ること等を検証.
    """
    admin = await _make_user(db, email="v2-sw-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)

    # 同住所 (同 lat/lng) で異なる固定時刻を持つ 2 患者 → 集約不可 → 警告.
    p1 = await _seed_patient(
        db,
        office=office,
        code="SW-A",
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "11:00",
                    "preferred_end": "11:30",
                    "time_type": "固定",
                }
            ]
        },
    )
    await _seed_patient(
        db,
        office=office,
        code="SW-B",
        lat=35.65,
        lng=140.10,
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "10:00",
                    "preferred_end": "10:30",
                    "time_type": "固定",
                }
            ]
        },
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    warnings = body["warnings"]
    assert isinstance(warnings, list)
    assert len(warnings) > 0, "同住所異時刻でも警告が出ない"
    # 各 warning は構造化されている (type / message / actionable などのキー).
    types = {w["type"] for w in warnings}
    assert "same_address_consolidation" in types, f"types: {types}"
    # actionable=True の warning に patient_id / current_time / suggested_time が埋まる
    actionable = [
        w for w in warnings if w["actionable"] and w["type"] == "same_address_consolidation"
    ]
    assert actionable, "actionable=True の同住所警告が出ない"
    sample = actionable[0]
    assert sample["patient_id"] is not None
    assert sample["current_time"]
    assert sample["suggested_time"]
    # 関連患者の id が p1 か別の集約不可患者
    assert sample["patient_id"] in (str(p1.id),) or sample["patient_id"]


@pytest.mark.asyncio
async def test_full_optimize_visit_has_distance_to_next_km(client, db) -> None:
    """同コース内 3 visits 並んだとき distance_to_next_km が計算されている.

    最後の visit は None, それ以外は数値.
    """
    admin = await _make_user(db, email="v2-dn-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    # 3 つの近接した patient を月曜 09:00 / 10:00 / 11:00 で固定.
    for i, (h, lat, lng) in enumerate(
        [(9, 35.65, 140.10), (10, 35.66, 140.11), (11, 35.67, 140.12)]
    ):
        await _seed_patient(
            db,
            office=office,
            code=f"DN-{i}",
            lat=lat,
            lng=lng,
            weekly_pattern={
                "entries": [
                    {
                        "weekday": "Mon",
                        "preferred_start": f"{h:02d}:00",
                        "preferred_end": f"{h:02d}:30",
                        "time_type": "固定",
                    }
                ]
            },
        )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # 月曜の after で 1 コースに 3 件入るはず.
    monday = next(wp for wp in body["week_proposals"] if wp["weekday"] == 0)
    courses = monday["after"]["courses"]
    assert courses, "月曜 after にコースが無い"
    # 3 件入っているコースを探す.
    target_course = next((c for c in courses if c["visits_count"] >= 3), None)
    assert target_course is not None, "3 件入ったコースが無い"
    visits = target_course["visits"]
    # 並びは start_time 昇順
    assert visits[0]["start_time"] <= visits[1]["start_time"] <= visits[2]["start_time"]
    # 1 件目 / 2 件目には distance_to_next_km が入っている (> 0)
    assert visits[0]["distance_to_next_km"] is not None
    assert visits[0]["distance_to_next_km"] > 0
    assert visits[1]["distance_to_next_km"] is not None
    # 3 件目 (最後) は None
    assert visits[2]["distance_to_next_km"] is None


@pytest.mark.asyncio
async def test_update_fixed_time_master_updates_pfv_and_weekly_pattern(client, db) -> None:
    """update-fixed-time-master が PFV と patient.weekly_pattern を更新する."""
    admin = await _make_user(db, email="v2-ufm-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(
        db,
        office=office,
        code="UFM-1",
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "10:00",
                    "preferred_end": "10:30",
                    "time_type": "固定",
                }
            ]
        },
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "weekday": 0,
            "new_start": "16:00",
            "new_end": "17:00",
            "new_time_type": "固定",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["updated"] is True
    assert body["patient_id"] == str(p.id)
    assert body["weekday"] == 0

    # PFV が更新されている (async session は refresh で fresh fetch)
    pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.mode == "normal",
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv is not None
    await db.refresh(pfv)
    assert pfv.start_time == time(16, 0)
    assert pfv.duration_min == 60  # 17:00 - 16:00 = 60min

    # weekly_pattern.entries が更新されている
    await db.refresh(p)
    entries = p.weekly_pattern.get("entries") or []
    mon_entry = next((e for e in entries if e.get("weekday") in (0, "Mon")), None)
    assert mon_entry is not None
    assert mon_entry["preferred_start"] == "16:00"
    assert mon_entry["preferred_end"] == "17:00"


@pytest.mark.asyncio
async def test_update_fixed_time_master_rejects_lunch_break(client, db) -> None:
    """H10: 12:00-13:00 と重なる時刻は 422."""
    admin = await _make_user(db, email="v2-ufm-lb@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="UFM-LB")
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "weekday": 0,
            "new_start": "12:15",
            "new_end": "12:45",
            "new_time_type": "固定",
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_update_fixed_time_master_404_for_unknown_patient(client, db) -> None:
    import uuid as _uuid

    admin = await _make_user(db, email="v2-ufm-404@example.com", role="admin")
    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(_uuid.uuid4()),
            "weekday": 0,
            "new_start": "09:30",
            "new_end": "10:00",
        },
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_update_fixed_time_week_only_updates_visit_only(client, db) -> None:
    """update-fixed-time-week-only が visit のみ更新し PFV は触らない."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-ufw-admin@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(
        db,
        office=office,
        code="UFW-1",
        weekly_pattern={
            "entries": [
                {
                    "weekday": "Mon",
                    "preferred_start": "10:00",
                    "preferred_end": "10:30",
                    "time_type": "固定",
                }
            ]
        },
    )
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc_v2w",  # 自動算出由来 → 許可
        required_staff_count=1,
    )
    db.add(visit)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-week-only",
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "new_start": "16:20",
            "new_end": "17:00",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["updated"] is True

    # visit が更新されている (Identity Map をクリアして fresh fetch).
    # async session は ``await db.refresh(obj)`` で fresh fetch する.
    await db.refresh(visit)
    assert visit.start_time == time(16, 20)
    assert visit.end_time == time(17, 0)
    # PFV は変わっていない (マスター保護)
    await db.refresh(pfv)
    refreshed_pfv = pfv
    assert refreshed_pfv is not None
    assert refreshed_pfv.start_time == time(10, 0)
    assert refreshed_pfv.duration_min == 30


@pytest.mark.asyncio
async def test_update_fixed_time_week_only_rejects_non_auto_source(client, db) -> None:
    """source='manual' などの非自動算出由来 visit は 409 で拒否."""
    from datetime import date

    from app.models.visit import VISIT_STATUS_PLANNED, Visit

    admin = await _make_user(db, email="v2-ufw-src@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="UFW-SRC")
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",  # 手動作成 → 保護対象
        required_staff_count=1,
    )
    db.add(visit)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-week-only",
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "new_start": "11:00",
            "new_end": "11:30",
        },
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_update_fixed_time_endpoints_reject_staff(client, db) -> None:
    """staff ロールは両エンドポイントとも 403."""
    import uuid as _uuid

    staff = await _make_user(db, email="v2-ufm-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(staff),
        json={
            "patient_id": str(_uuid.uuid4()),
            "weekday": 0,
            "new_start": "09:30",
            "new_end": "10:00",
        },
    )
    assert res.status_code == 403

    res2 = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-week-only",
        headers=_bearer(staff),
        json={
            "visit_id": str(_uuid.uuid4()),
            "new_start": "09:30",
            "new_end": "10:00",
        },
    )
    assert res2.status_code == 403


# ---------------------------------------------------------------------------
# W41 v2 拡張 (V2Warning unit test) — _consolidate_same_address_time から V2Warning が返る
# ---------------------------------------------------------------------------


def test_consolidate_same_address_time_emits_v2warning() -> None:
    """同住所 異時刻 集約不可ケースで V2Warning (type=same_address_consolidation,
    actionable=True) が返ること."""
    import uuid as _uuid
    from datetime import time as _time

    from app.services.scheduling.auto_allocator_v2 import (
        V2Visit,
        V2Warning,
        _consolidate_same_address_time,
    )

    office_id = _uuid.uuid4()
    pid_a = _uuid.uuid4()
    pid_b = _uuid.uuid4()
    a = V2Visit(
        patient_id=pid_a,
        patient_name="A",
        patient_code="A",
        weekday=1,
        start_time=_time(11, 0),
        end_time=_time(11, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
        time_type="固定",
        preferred_start="11:00",
    )
    b = V2Visit(
        patient_id=pid_b,
        patient_name="B",
        patient_code="B",
        weekday=1,
        start_time=_time(10, 0),  # 異なる時刻
        end_time=_time(10, 30),
        service_minutes=30,
        lat=35.65,
        lng=140.10,
        office_id=office_id,
        am_pm="am",
        source_kind="pool",
        time_type="固定",
        preferred_start="10:00",
    )
    warnings: list[V2Warning] = []
    _consolidate_same_address_time([a, b], warnings)
    consol = [w for w in warnings if w.type == "same_address_consolidation"]
    assert consol, f"same_address_consolidation 警告が出ていない: {warnings}"
    w = consol[0]
    assert w.actionable is True
    assert w.patient_id in (pid_a, pid_b)
    assert w.weekday == 1
    assert w.current_time
    assert w.suggested_time
    assert w.time_type == "固定"


# ---------------------------------------------------------------------------
# W41 v2 拡張 (post-review regression tests)
#   - update-fixed-time-week-only が status='in_progress'/'completed' を拒否
#   - update-fixed-time-master の H10 が new_end 省略時にも duration_min から判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_status", ["in_progress", "completed", "cancelled"])
@pytest.mark.asyncio
async def test_update_fixed_time_week_only_rejects_non_planned_status(
    client, db, bad_status: str
) -> None:
    """status が planned 以外の visit は 409 で拒否 (進行中/完了/キャンセル保護)."""
    from datetime import date

    from app.models.visit import Visit

    admin = await _make_user(db, email=f"v2-ufw-st-{bad_status}@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code=f"UFW-ST-{bad_status[:3].upper()}")
    visit = Visit(
        patient_id=p.id,
        visit_date=date(2026, 5, 11),
        start_time=time(10, 0),
        end_time=time(10, 30),
        type="regular",
        status=bad_status,
        source="auto_alloc_v2w",
        required_staff_count=1,
    )
    db.add(visit)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-week-only",
        headers=_bearer(admin),
        json={
            "visit_id": str(visit.id),
            "new_start": "11:00",
            "new_end": "11:30",
        },
    )
    assert res.status_code == 409, res.text
    assert "計画状態ではない" in res.text


@pytest.mark.asyncio
async def test_update_fixed_time_master_h10_skipped_for_range_type(client, db) -> None:
    """time_type='時間帯' の場合、希望レンジが昼休憩を跨いでも H10 はスキップ.

    new_start=09:30, new_end=17:30, time_type='時間帯' は希望範囲指定であって
    実訪問時間ではないため、H10 (12:00-13:00 重複) を適用しない. 200 OK.
    """
    from app.models.patient_fixed_visit import PatientFixedVisit

    admin = await _make_user(db, email="v2-ufm-rng@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="UFM-RNG")
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=2,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "weekday": 2,
            "new_start": "09:30",
            "new_end": "17:30",
            "new_time_type": "時間帯",
        },
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_update_fixed_time_master_h10_lunch_overlap_via_duration(client, db) -> None:
    """new_end 省略時でも duration_min から計算した end が昼休憩に重なれば 422.

    既存 PFV の duration=60min, new_start=11:30 → end=12:30 (12:00-13:00 重複) → 422.
    """
    from app.models.patient_fixed_visit import PatientFixedVisit

    admin = await _make_user(db, email="v2-ufm-h10dur@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="UFM-H10DUR")
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=60,  # ← 既存 duration
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/update-fixed-time-master",
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "weekday": 0,
            "new_start": "11:30",  # 11:30 + 60min = 12:30 が昼休憩に重複
            # new_end は故意に省略
        },
    )
    assert res.status_code == 422, res.text
    assert "H10" in res.text or "昼休憩" in res.text


# ---------------------------------------------------------------------------
# W41 v2 拡張 (今週限定オーバーレイ / pending_edits)
# /full-optimize と /apply-week-only が pending_edits を受けて PFV を上書きせず
# 一時的に反映する.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_optimize_empty_pending_edits_is_backward_compatible(client, db) -> None:
    """pending_edits が空でも従来通り動く (後方互換)."""
    admin = await _make_user(db, email="v2-pe-empty@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p1 = await _seed_patient(db, office=office, code="PE-EMP1", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p1.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    # pending_edits 未指定でも 200
    res1 = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res1.status_code == 200, res1.text

    # 空配列を明示しても 200
    res2 = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [],
        },
    )
    assert res2.status_code == 200, res2.text


@pytest.mark.asyncio
async def test_full_optimize_pending_edits_overlay_reflected_in_before_after(client, db) -> None:
    """pending_edits で固定時間を上書きすると Before/After に反映される."""
    admin = await _make_user(db, email="v2-pe-overlay@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-OV1", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()
    # W41 v2 cross-review (M-Codex-3): API 呼び出し前に weekly_pattern を保持して
    # マスター不変を後で assert する.
    original_weekly_pattern = dict(p.weekly_pattern) if p.weekly_pattern else None

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "11:00",
                    "new_end": "11:30",
                    "new_time_type": "固定",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # week_proposals[weekday=0].before.courses[*].visits に対象 visit が出てくる.
    mon = next((w for w in body["week_proposals"] if w["weekday"] == 0), None)
    assert mon is not None
    found = False
    for course in mon["before"]["courses"]:
        for v in course["visits"]:
            if v["patient_id"] == str(p.id):
                # オーバーレイの 11:00 になっている (PFV 元値 10:00 ではない)
                assert v["start_time"].startswith("11:00")
                found = True
    assert found, "対象患者の visit が Before に見つからない"

    # PFV (マスター) は変更されていない
    refreshed_pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id, PatientFixedVisit.weekday == 0
        )
    )
    assert refreshed_pfv is not None
    assert refreshed_pfv.start_time == time(10, 0)
    assert refreshed_pfv.duration_min == 30

    # W41 v2 cross-review (M-Codex-3): patients.weekly_pattern (JSON) も
    # pending_edits によって変更されないこと (マスター不変原則).
    await db.refresh(p)
    assert p.weekly_pattern == original_weekly_pattern


@pytest.mark.asyncio
async def test_full_optimize_pending_edits_range_type_preserves_duration(client, db) -> None:
    """pending_edits の time_type='時間帯' は duration_min を保持する."""
    admin = await _make_user(db, email="v2-pe-range@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-RNG1", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=45,  # ← 既存 duration を保持することを検証
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "13:30",
                    "new_end": "15:00",  # range なので 90 分にはならない
                    "new_time_type": "時間帯",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    mon = next((w for w in body["week_proposals"] if w["weekday"] == 0), None)
    assert mon is not None
    found_dur: int | None = None
    for course in mon["before"]["courses"]:
        for v in course["visits"]:
            if v["patient_id"] == str(p.id):
                found_dur = v["duration_min"]
    assert found_dur == 45, f"time_type=時間帯 のとき duration は保持されるべき (実際: {found_dur})"


@pytest.mark.asyncio
async def test_full_optimize_pending_edits_fixed_type_recomputes_duration(client, db) -> None:
    """pending_edits の time_type='固定' は new_end-new_start で duration_min を再計算."""
    admin = await _make_user(db, email="v2-pe-fixed@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-FIX1", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,  # ← 元値
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "14:00",
                    "new_end": "14:45",  # 45 分
                    "new_time_type": "固定",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    mon = next((w for w in body["week_proposals"] if w["weekday"] == 0), None)
    assert mon is not None
    found_dur: int | None = None
    for course in mon["before"]["courses"]:
        for v in course["visits"]:
            if v["patient_id"] == str(p.id):
                found_dur = v["duration_min"]
                assert v["start_time"].startswith("14:00")
    assert found_dur == 45, f"time_type=固定 のとき new_end-new_start で再計算 (実際: {found_dur})"


@pytest.mark.asyncio
async def test_apply_week_only_with_pending_edits_overrides_visit_start(client, db) -> None:
    """apply-week-only で pending_edits 反映 → visits.start_time が新値, PFV は元値のまま."""
    from datetime import date

    from app.models.visit import Visit

    admin = await _make_user(db, email="v2-pe-aw@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-AW1", lat=35.65, lng=140.10)
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()

    # 元 visit_plan は 10:00 (PFV) のままだが pending_edits で 11:00 に上書き
    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "10:00",
                            "end_time": "10:30",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "am",
                        }
                    ],
                }
            ],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "11:00",
                    "new_end": "11:30",
                    "new_time_type": "固定",
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visits_created"] >= 1

    # 作られた visit は 11:00 開始
    week_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),  # Mon W20
                Visit.source == "auto_alloc_v2w",
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(week_visits) == 1
    assert week_visits[0].start_time == time(11, 0)

    # PFV は元値のまま
    refreshed_pfv = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.id == pfv.id))
    assert refreshed_pfv is not None
    assert refreshed_pfv.start_time == time(10, 0)
    assert refreshed_pfv.duration_min == 30


@pytest.mark.asyncio
async def test_apply_week_only_pending_edits_invalid_end_before_start_skips(client, db) -> None:
    """W41 v2 cross-review (M-Codex-3): pending_edits の new_end <= new_start で

    visit が作成されず warning が出ること. クライアントが不正な range を送っても
    end_time < start_time な不正 visit が DB に挿入されないことを担保する.
    """
    from datetime import date

    from app.models.visit import Visit

    admin = await _make_user(db, email="v2-pe-aw-invalid@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-AW-INV", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "10:00",
                            "end_time": "10:30",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "am",
                        }
                    ],
                }
            ],
            # new_end (13:00) <= new_start (14:00) な不正 range を送る
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "14:00",
                    "new_end": "13:00",
                    "new_time_type": "固定",
                }
            ],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # overlay 適用後 et<=st のためスキップ -> visits_created=0
    assert body["visits_created"] == 0, (
        f"overlay 適用後 et<=st の visit は作られるべきでない (visits_created={body['visits_created']})"
    )
    # warning に「overlay 適用後」または「end_time」「start_time」を含む文言があること
    msgs = " | ".join(body.get("warnings", []))
    assert "overlay" in msgs or "end_time" in msgs, f"想定 warning が見つからない: {msgs}"
    # DB にも該当週 visit が無いこと
    week_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.visit_date == date(2026, 5, 11),  # Mon W20
                Visit.source == "auto_alloc_v2w",
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert len(week_visits) == 0


@pytest.mark.asyncio
async def test_full_optimize_pending_edits_unknown_pfv_emits_warning(client, db) -> None:
    """存在しない PFV (patient_id+weekday の組合せが無い) の pending_edit は warning + 無視."""
    admin = await _make_user(db, email="v2-pe-noexist@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-NX1", lat=35.65, lng=140.10)
    # PFV を **作らない** (新規 patient と同じ状態)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "11:00",
                    "new_end": "11:30",
                    "new_time_type": "固定",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # warning に「固定枠が存在しないため」または「pending」「今週限定」っぽい文言が含まれる
    msgs = " | ".join(w.get("message", "") for w in body.get("warnings", []))
    assert (
        "固定枠が存在しない" in msgs
        or "今週限定" in msgs
        or "pending" in msgs.lower()
        or "PFV" in msgs
    ), f"想定 warning が見つからない: {msgs}"


@pytest.mark.asyncio
async def test_full_optimize_pending_edits_duplicate_key_uses_last(client, db) -> None:
    """pending_edits が同じ (patient_id, weekday) を複数持つ場合は **最後のもの** を採用."""
    admin = await _make_user(db, email="v2-pe-dup@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-DUP1", lat=35.65, lng=140.10)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "11:00",
                    "new_end": "11:30",
                    "new_time_type": "固定",
                },
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "13:30",
                    "new_end": "14:00",
                    "new_time_type": "固定",
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    mon = next((w for w in body["week_proposals"] if w["weekday"] == 0), None)
    assert mon is not None
    found_start: str | None = None
    for course in mon["before"]["courses"]:
        for v in course["visits"]:
            if v["patient_id"] == str(p.id):
                found_start = v["start_time"]
    assert found_start is not None
    # 最後のもの 13:30 が採用される
    assert found_start.startswith("13:30"), (
        f"最後の pending_edit を採用するべき (実際: {found_start})"
    )


@pytest.mark.asyncio
async def test_full_optimize_idempotent_after_apply_week_only(client, db) -> None:
    """apply-week-only 後に /full-optimize を呼ぶと PFV は元のままで提案も元に戻る."""
    admin = await _make_user(db, email="v2-pe-idem@example.com", role="admin")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(db, office=office, code="PE-IDEM1", lat=35.65, lng=140.10)
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(10, 0),
        duration_min=30,
        slot_index=0,
    )
    db.add(pfv)
    await db.commit()

    # 1) apply-week-only で pending_edits を反映 (visits に 11:00 を作る)
    res_apply = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=_bearer(admin),
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [
                {
                    "patient_id": str(p.id),
                    "visit_plans": [
                        {
                            "weekday": 0,
                            "start_time": "11:00",
                            "end_time": "11:30",
                            "duration_min": 30,
                            "course_code": "A",
                            "office_id": str(office.id),
                            "am_pm": "am",
                        }
                    ],
                }
            ],
            "pending_edits": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "new_start": "11:00",
                    "new_end": "11:30",
                    "new_time_type": "固定",
                }
            ],
            "confirm": True,
        },
    )
    assert res_apply.status_code == 200, res_apply.text

    # 2) 再度 /full-optimize を pending_edits なしで呼ぶ → PFV の 10:00 が反映される
    res_fo = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=_bearer(admin),
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert res_fo.status_code == 200, res_fo.text
    body = res_fo.json()
    mon = next((w for w in body["week_proposals"] if w["weekday"] == 0), None)
    assert mon is not None
    found_start: str | None = None
    for course in mon["before"]["courses"]:
        for v in course["visits"]:
            if v["patient_id"] == str(p.id):
                found_start = v["start_time"]
    assert found_start is not None
    # pending_edits なしで再算出すれば、PFV 元値の 10:00 に戻る
    assert found_start.startswith("10:00"), (
        f"再算出時 pending_edits なしなら PFV 元値 10:00 に戻るべき (実際: {found_start})"
    )

    # PFV (マスター) も変更されていない
    refreshed_pfv = await db.scalar(select(PatientFixedVisit).where(PatientFixedVisit.id == pfv.id))
    assert refreshed_pfv is not None
    assert refreshed_pfv.start_time == time(10, 0)
