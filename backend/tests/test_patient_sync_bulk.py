"""Tests for bulk patient_sync endpoints.

POST /api/v1/patients/bulk-sync-week-to-fixed
    複数患者の今週 visits を patient_fixed_visits に 1 TX で反映 (永続モード).

POST /api/v1/patients/bulk-apply-week-only-visit-changes
    今週の visits のみを (patient, weekday) 単位で update (週限定; PFV 不変).

検証観点:
  1. 複数 patient bulk sync (3 名 → 3 件成功 + total count)
  2. partial failure rollback (1 件存在しない patient → results に "not_found",
     成功 patient は commit される; ただし DB error 系は全 rollback)
  3. bulk week-only visit changes (PFV 不変 + visit update)
  4. RBAC (admin / manager 許可, staff 403, no-auth 401)

Backend で APP_ENV=test ガード済み (conftest.py L40-).
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit

# ---------------------------------------------------------------------------
# Helpers (copy from test_patient_sync_week_to_fixed.py)
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw-does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, code: str) -> Patient:
    p = Patient(code=code, name=f"P-{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    *,
    patient: Patient,
    visit_date: date,
    start: time,
    end: time,
    source: str = "manual",
    status_v: str = "planned",
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=status_v,
        source=source,
        required_staff_count=1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _make_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start: time,
    duration_min: int = 30,
    course_template_id=None,
    slot_index: int = 0,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=duration_min,
        slot_index=slot_index,
        course_template_id=course_template_id,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date(2026, 5, 11)
TUE = date(2026, 5, 12)
WED = date(2026, 5, 13)


# ---------------------------------------------------------------------------
# 1) bulk sync: 3 名 → 3 件成功 (PFV insert/update mix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_week_to_fixed_multiple_patients(client, db) -> None:
    """3 名同時 bulk sync → 全成功 + total count が正しい."""
    admin = await _make_user(db, email="bulk-sync-admin@example.com", role="admin")
    p1 = await _make_patient(db, code="BULK-1")
    p2 = await _make_patient(db, code="BULK-2")
    p3 = await _make_patient(db, code="BULK-3")
    # p1: Mon visit (insert), Tue PFV mismatch (update)
    await _make_visit(db, patient=p1, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_visit(db, patient=p1, visit_date=TUE, start=time(13, 30), end=time(14, 0))
    await _make_pfv(db, patient=p1, weekday=1, start=time(10, 0))  # Tue mismatch
    # p2: Mon visit (insert only)
    await _make_visit(db, patient=p2, visit_date=MON, start=time(10, 0), end=time(10, 30))
    # p3: Wed visit + matching PFV (unchanged)
    await _make_visit(db, patient=p3, visit_date=WED, start=time(11, 0), end=time(11, 30))
    await _make_pfv(db, patient=p3, weekday=2, start=time(11, 0), duration_min=30)

    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        headers=_bearer(admin),
        json={
            "patient_ids": [str(p1.id), str(p2.id), str(p3.id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert len(body["results"]) == 3
    # totals: p1 (1 insert + 1 update), p2 (1 insert), p3 (1 unchanged)
    assert body["total_inserted"] == 2
    assert body["total_updated"] == 1
    assert body["total_unchanged"] == 1
    assert body["total_skipped"] == 0

    # DB 確認
    p1_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p1.id))
    ).all()
    p1_by_wd = {p_.weekday: p_ for p_ in p1_rows}
    assert p1_by_wd[0].start_time == time(9, 0)  # Mon inserted
    assert p1_by_wd[1].start_time == time(13, 30)  # Tue updated

    p2_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p2.id))
    ).all()
    assert len(p2_rows) == 1
    assert p2_rows[0].weekday == 0

    # p3: unchanged
    p3_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p3.id))
    ).all()
    assert len(p3_rows) == 1
    assert p3_rows[0].start_time == time(11, 0)


# ---------------------------------------------------------------------------
# 2) partial failure: 1 件 not_found → results に "not_found",
#    成功 patient は commit される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_not_found_patient_marked_in_results(client, db) -> None:
    """存在しない patient_id は results[].error="not_found".
    他の patient は通常通り commit される.
    """
    admin = await _make_user(db, email="bulk-sync-pf-admin@example.com", role="admin")
    p_ok = await _make_patient(db, code="BULK-PF-OK")
    await _make_visit(db, patient=p_ok, visit_date=MON, start=time(9, 0), end=time(9, 30))
    missing_id = uuid4()

    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        headers=_bearer(admin),
        json={
            "patient_ids": [str(p_ok.id), str(missing_id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert len(body["results"]) == 2

    # 結果を patient_id で検索
    by_pid = {r["patient_id"]: r for r in body["results"]}
    assert by_pid[str(p_ok.id)]["error"] is None
    assert by_pid[str(p_ok.id)]["summary"]["pfv_inserted"] == 1
    assert by_pid[str(missing_id)]["error"] == "not_found"
    assert by_pid[str(missing_id)]["summary"]["pfv_inserted"] == 0

    # 成功 patient は DB に反映されている
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p_ok.id))
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 3) dry_run=True → DB 変更なし
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_dry_run_no_db_changes(client, db) -> None:
    """dry_run=True で diff のみ返し DB は触らない."""
    admin = await _make_user(db, email="bulk-sync-dry-admin@example.com", role="admin")
    p1 = await _make_patient(db, code="BULK-DRY-1")
    await _make_visit(db, patient=p1, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        headers=_bearer(admin),
        json={
            "patient_ids": [str(p1.id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is False
    assert body["total_inserted"] == 1

    # DB 変更なし
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p1.id))
    ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# 4) bulk apply week-only: PFV 不変 + visit update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_apply_week_only_visit_changes(client, db) -> None:
    """今週限定モード: visit の start_time / duration_min を update.
    PFV は一切変更しない.
    """
    admin = await _make_user(db, email="bulk-wo-admin@example.com", role="admin")
    p1 = await _make_patient(db, code="BULK-WO-1")
    p2 = await _make_patient(db, code="BULK-WO-2")
    # p1: Mon visit 09:00-09:30 → 11:00-11:30 (update)
    v1 = await _make_visit(db, patient=p1, visit_date=MON, start=time(9, 0), end=time(9, 30))
    # p1: Tue visit 14:00-14:30 → no change requested (PFV も無傷)
    await _make_visit(db, patient=p1, visit_date=TUE, start=time(14, 0), end=time(14, 30))
    # p2: Mon visit 10:00-10:30 → 13:00-13:45 (update + duration change)
    v2 = await _make_visit(db, patient=p2, visit_date=MON, start=time(10, 0), end=time(10, 30))
    # p1 / p2 とも PFV を持つ (これらが触られないことを確認)
    pfv_p1 = await _make_pfv(db, patient=p1, weekday=0, start=time(9, 0), duration_min=30)
    pfv_p2 = await _make_pfv(db, patient=p2, weekday=0, start=time(10, 0), duration_min=30)
    pfv_p1_id = pfv_p1.id
    pfv_p2_id = pfv_p2.id

    res = await client.post(
        "/api/v1/patients/bulk-apply-week-only-visit-changes",
        headers=_bearer(admin),
        json={
            "patient_visit_changes": [
                {
                    "patient_id": str(p1.id),
                    "weekday": 0,
                    "start_time": "11:00",
                    "duration_min": 30,
                },
                {
                    "patient_id": str(p2.id),
                    "weekday": 0,
                    "start_time": "13:00",
                    "duration_min": 45,
                },
            ],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["total_updated"] == 2
    assert body["total_unchanged"] == 0
    assert body["total_skipped"] == 0

    # outcomes に visit_id が含まれる
    by_pid = {o["patient_id"]: o for o in body["outcomes"]}
    assert by_pid[str(p1.id)]["operation"] == "update"
    assert by_pid[str(p1.id)]["visit_id"] == str(v1.id)
    assert by_pid[str(p2.id)]["operation"] == "update"
    assert by_pid[str(p2.id)]["visit_id"] == str(v2.id)

    # DB 反映: visit が update されている
    await db.refresh(v1)
    await db.refresh(v2)
    assert v1.start_time == time(11, 0)
    assert v1.end_time == time(11, 30)
    assert v2.start_time == time(13, 0)
    assert v2.end_time == time(13, 45)

    # PFV は一切変更されていない (= 「イレギュラー対応」の本質)
    pfv_p1_refreshed = await db.scalar(
        select(PatientFixedVisit).where(PatientFixedVisit.id == pfv_p1_id)
    )
    pfv_p2_refreshed = await db.scalar(
        select(PatientFixedVisit).where(PatientFixedVisit.id == pfv_p2_id)
    )
    assert pfv_p1_refreshed is not None
    assert pfv_p1_refreshed.start_time == time(9, 0)
    assert pfv_p1_refreshed.duration_min == 30
    assert pfv_p2_refreshed is not None
    assert pfv_p2_refreshed.start_time == time(10, 0)
    assert pfv_p2_refreshed.duration_min == 30


# ---------------------------------------------------------------------------
# 5) bulk week-only: 当該 weekday に visit が無い → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_apply_week_only_skips_when_no_visit(client, db) -> None:
    """当該 weekday に active visit が無い場合は operation='skipped'."""
    admin = await _make_user(db, email="bulk-wo-skip-admin@example.com", role="admin")
    p1 = await _make_patient(db, code="BULK-WO-SKIP")
    # 今週は何の visit も無い

    res = await client.post(
        "/api/v1/patients/bulk-apply-week-only-visit-changes",
        headers=_bearer(admin),
        json={
            "patient_visit_changes": [
                {
                    "patient_id": str(p1.id),
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                },
            ],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_skipped"] == 1
    assert body["total_updated"] == 0
    assert body["outcomes"][0]["operation"] == "skipped"
    assert body["outcomes"][0]["reason"] == "no_active_visit"


# ---------------------------------------------------------------------------
# 6) RBAC: staff → 403 (bulk sync)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="bulk-sync-staff@example.com", role="staff")
    p = await _make_patient(db, code="BULK-RBAC")
    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        headers=_bearer(staff_user),
        json={
            "patient_ids": [str(p.id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": True,
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7) RBAC: staff → 403 (bulk week-only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_week_only_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="bulk-wo-staff@example.com", role="staff")
    p = await _make_patient(db, code="BULK-WO-RBAC")
    res = await client.post(
        "/api/v1/patients/bulk-apply-week-only-visit-changes",
        headers=_bearer(staff_user),
        json={
            "patient_visit_changes": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                },
            ],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": True,
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 8) 認証なし → 401 (両 endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_rejects_no_auth(client, db) -> None:
    p = await _make_patient(db, code="BULK-NOAUTH")
    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        json={
            "patient_ids": [str(p.id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": True,
        },
    )
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_bulk_week_only_rejects_no_auth(client, db) -> None:
    p = await _make_patient(db, code="BULK-WO-NOAUTH")
    res = await client.post(
        "/api/v1/patients/bulk-apply-week-only-visit-changes",
        json={
            "patient_visit_changes": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                },
            ],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": True,
        },
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# 9) manager role でも bulk sync 実行可能
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_sync_allows_manager_role(client, db) -> None:
    mgr = await _make_user(db, email="bulk-sync-mgr@example.com", role="manager")
    p = await _make_patient(db, code="BULK-MGR")
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        "/api/v1/patients/bulk-sync-week-to-fixed",
        headers=_bearer(mgr),
        json={
            "patient_ids": [str(p.id)],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_inserted"] == 1
    assert body["results"][0]["error"] is None


# ---------------------------------------------------------------------------
# 10) bulk week-only: unchanged 検出 (visit が既に新値と一致)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_week_only_unchanged(client, db) -> None:
    """visit が既に new_start/new_duration と一致なら operation='unchanged'."""
    admin = await _make_user(db, email="bulk-wo-uc-admin@example.com", role="admin")
    p = await _make_patient(db, code="BULK-WO-UC")
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        "/api/v1/patients/bulk-apply-week-only-visit-changes",
        headers=_bearer(admin),
        json={
            "patient_visit_changes": [
                {
                    "patient_id": str(p.id),
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                },
            ],
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "dry_run": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total_unchanged"] == 1
    assert body["total_updated"] == 0
    assert body["outcomes"][0]["operation"] == "unchanged"
