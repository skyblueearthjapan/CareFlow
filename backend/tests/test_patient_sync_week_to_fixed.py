"""Tests for POST /api/v1/patients/{patient_id}/sync-week-visits-to-fixed.

検証観点:
  1. dry_run=True で diff 確認 (DB 変更なし; transaction_applied=False)
  2. dry_run=False で PFV upsert (insert + update が混在)
  3. 既存 PFV と完全一致なら "unchanged" 扱い
  4. 今週に visit が無い weekday は触らない (既存 PFV を削除しない)
  5. 別 patient の PFV は触らない (scope isolation)
  6. RBAC: staff role は 403
  7. patient 404 + ISO week invalid + 同 weekday 複数 visit は最早を採用

Backend で APP_ENV=test ガード済み (conftest.py L40-).
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User, Visit
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient_fixed_visit import PatientFixedVisit

# ---------------------------------------------------------------------------
# Helpers
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
# 1) dry_run=True で diff 確認 (DB 変更なし)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_dry_run_returns_diff_without_db_changes(client, db) -> None:
    admin = await _make_user(db, email="sync-dry-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-DRY-1")
    # 今週: Mon に visit (新規 → insert), Tue に visit (既存 PFV と差 → update)
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_visit(db, patient=p, visit_date=TUE, start=time(13, 30), end=time(14, 0))
    # 既存 PFV: Tue 10:00 (異なる) → update / Wed は触らない (今週 visit 無し)
    await _make_pfv(db, patient=p, weekday=1, start=time(10, 0))  # Tue
    await _make_pfv(db, patient=p, weekday=2, start=time(11, 0))  # Wed (touched-not-by-week)

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is False
    assert body["summary"]["pfv_inserted"] == 1
    assert body["summary"]["pfv_updated"] == 1
    assert body["summary"]["pfv_unchanged"] == 0
    # change 内容も確認 (Mon=insert / Tue=update). Wed は触らないので含まない.
    ops = {c["weekday"]: c["operation"] for c in body["changes"]}
    assert ops == {0: "insert", 1: "update"}

    # DB 変更なし: Tue の PFV start_time は 10:00 のまま, Mon の PFV は無いまま
    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    pfv_by_wd = {p_.weekday: p_ for p_ in pfv_rows}
    assert 0 not in pfv_by_wd  # Mon の PFV は作られていない
    assert pfv_by_wd[1].start_time == time(10, 0)  # Tue は変わらず
    assert pfv_by_wd[2].start_time == time(11, 0)  # Wed は変わらず


# ---------------------------------------------------------------------------
# 2) dry_run=False で PFV upsert (insert + update)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_apply_upserts_pfv(client, db) -> None:
    admin = await _make_user(db, email="sync-apply-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-APPLY-1")
    # 今週 Mon: 新規 visit (PFV 無し → insert) / Tue: 既存 PFV と差 (10:00 → 13:30) → update
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_visit(db, patient=p, visit_date=TUE, start=time(13, 30), end=time(14, 0))
    await _make_pfv(db, patient=p, weekday=1, start=time(10, 0), duration_min=30)

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_inserted"] == 1
    assert body["summary"]["pfv_updated"] == 1
    assert body["summary"]["pfv_unchanged"] == 0

    # DB 反映を確認.
    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    pfv_by_wd = {p_.weekday: p_ for p_ in pfv_rows}
    assert pfv_by_wd[0].start_time == time(9, 0)  # Mon が insert された
    assert pfv_by_wd[0].duration_min == 30
    assert pfv_by_wd[1].start_time == time(13, 30)  # Tue が update された
    assert pfv_by_wd[1].duration_min == 30


# ---------------------------------------------------------------------------
# 3) 既存 PFV と完全一致 → "unchanged" 扱い
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_unchanged_when_pfv_matches_visit(client, db) -> None:
    admin = await _make_user(db, email="sync-uc-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-UC-1")
    # 今週 Mon: visit 09:00-09:30 (= PFV と完全一致)
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0), duration_min=30)

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["pfv_unchanged"] == 1
    assert body["summary"]["pfv_inserted"] == 0
    assert body["summary"]["pfv_updated"] == 0
    assert body["changes"][0]["operation"] == "unchanged"


# ---------------------------------------------------------------------------
# 4) 今週に visit が無い weekday は触らない (既存 PFV 保持)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_does_not_touch_pfv_without_week_visit(client, db) -> None:
    admin = await _make_user(db, email="sync-keep-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-KEEP-1")
    # 今週は Mon のみ visit. Tue / Wed の既存 PFV は残るべき (削除されない)
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_pfv(db, patient=p, weekday=1, start=time(10, 0))  # Tue
    await _make_pfv(db, patient=p, weekday=2, start=time(11, 0))  # Wed

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 変更は Mon の insert のみ. Tue / Wed の delete は無い.
    assert body["summary"]["pfv_inserted"] == 1
    assert body["summary"]["pfv_updated"] == 0
    assert {c["weekday"] for c in body["changes"]} == {0}

    # Tue / Wed の PFV が残っていることを DB で確認
    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    weekdays = {p_.weekday for p_ in pfv_rows}
    assert weekdays == {0, 1, 2}  # Mon (新規) + Tue / Wed (既存)


# ---------------------------------------------------------------------------
# 5) 別 patient の PFV は触らない (scope isolation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_isolation_other_patient_pfv_untouched(client, db) -> None:
    admin = await _make_user(db, email="sync-iso-admin@example.com", role="admin")
    target = await _make_patient(db, code="SYNC-ISO-T")
    other = await _make_patient(db, code="SYNC-ISO-O")
    # target は Mon visit (新規)
    await _make_visit(db, patient=target, visit_date=MON, start=time(9, 0), end=time(9, 30))
    # other の既存 PFV (Mon 12:30) — sync 後も残るべき
    other_pfv = await _make_pfv(db, patient=other, weekday=0, start=time(12, 30))
    other_pfv_id = other_pfv.id

    res = await client.post(
        f"/api/v1/patients/{target.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text

    # other patient の PFV が無傷
    refreshed = await db.scalar(
        select(PatientFixedVisit).where(PatientFixedVisit.id == other_pfv_id)
    )
    assert refreshed is not None
    assert refreshed.start_time == time(12, 30)


# ---------------------------------------------------------------------------
# 6) RBAC: staff role → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="sync-staff@example.com", role="staff")
    p = await _make_patient(db, code="SYNC-STAFF-1")
    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(staff_user),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": True},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 7) 認証なし → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_rejects_no_auth(client, db) -> None:
    p = await _make_patient(db, code="SYNC-NOAUTH-1")
    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": True},
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# 8) 存在しない patient → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_404_when_patient_missing(client, db) -> None:
    admin = await _make_user(db, email="sync-404-admin@example.com", role="admin")
    missing_id = uuid4()
    res = await client.post(
        f"/api/v1/patients/{missing_id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": True},
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 9) 同 weekday に複数 visit がある場合は最早 start_time を採用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_picks_earliest_visit_per_weekday(client, db) -> None:
    admin = await _make_user(db, email="sync-multi-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-MULTI-1")
    # 同 Mon に visit が 2 件: 14:00 と 9:00. 9:00 が採用されるべき.
    await _make_visit(db, patient=p, visit_date=MON, start=time(14, 0), end=time(14, 30))
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["pfv_inserted"] == 1
    pfv_row = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv_row is not None
    assert pfv_row.start_time == time(9, 0)


# ---------------------------------------------------------------------------
# 10) soft-deleted visit は無視される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_ignores_soft_deleted_visits(client, db) -> None:
    from datetime import UTC, datetime

    admin = await _make_user(db, email="sync-soft-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-SOFT-1")
    # active visit を作り、その後 soft-delete する.
    v = await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    v.deleted_at = datetime.now(tz=UTC)
    db.add(v)
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": True},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # active visit は無いので変更 0 件
    assert body["summary"]["pfv_inserted"] == 0
    assert body["summary"]["pfv_updated"] == 0
    assert body["summary"]["pfv_unchanged"] == 0
    assert body["changes"] == []


# ---------------------------------------------------------------------------
# Wave Next 1 cross-review HIGH/MEDIUM fixes
# ---------------------------------------------------------------------------


async def _make_office(db, name: str = "WaveNext-Office") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_course_template(db, office_id, label: str = "A") -> CourseTemplate:
    tpl = CourseTemplate(
        office_id=office_id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def _make_course(
    db,
    *,
    office_id,
    template_id,
    iso_year: int,
    iso_week: int,
    weekday: int,
    code: str = "A",
) -> Course:
    course = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        office_id=office_id,
        template_id=template_id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


# ---------------------------------------------------------------------------
# 11) H4: manager role でも apply できる (admin と同等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_apply_allows_manager_role(client, db) -> None:
    """Wave Next 1 H4: manager role の happy-path. test 2 と同じシナリオで 200."""
    mgr = await _make_user(db, email="sync-mgr@example.com", role="manager")
    p = await _make_patient(db, code="SYNC-MGR-1")
    # 今週 Mon: 新規 visit (PFV 無し → insert) / Tue: 既存 PFV と差 → update
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_visit(db, patient=p, visit_date=TUE, start=time(13, 30), end=time(14, 0))
    await _make_pfv(db, patient=p, weekday=1, start=time(10, 0), duration_min=30)

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(mgr),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["transaction_applied"] is True
    assert body["summary"]["pfv_inserted"] == 1
    assert body["summary"]["pfv_updated"] == 1

    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    pfv_by_wd = {p_.weekday: p_ for p_ in pfv_rows}
    assert pfv_by_wd[0].start_time == time(9, 0)
    assert pfv_by_wd[1].start_time == time(13, 30)


# ---------------------------------------------------------------------------
# 12) H1: visit.course_id is None でも既存 PFV の course_template_id を保持
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_preserves_pfv_course_template_id_when_visit_has_no_course(client, db) -> None:
    """Wave Next 1 H1: visit.course_id=None でも既存 PFV の ct_id を NULL 上書きしない.

    シナリオ:
      - 既存 PFV (Mon 09:00, duration=30, course_template_id=tpl.id)
      - 今週 visit (Mon 09:00-09:30, course_id=None)
      - 期待: unchanged (比較キーの ct_id も既存値 fallback で一致するため).
    """
    admin = await _make_user(db, email="sync-h1-admin@example.com", role="admin")
    office = await _make_office(db, name="H1-Office")
    tpl = await _make_course_template(db, office.id, label="H1-A")
    p = await _make_patient(db, code="SYNC-H1-1")

    # 既存 PFV (ct_id 紐付き)
    await _make_pfv(
        db,
        patient=p,
        weekday=0,
        start=time(9, 0),
        duration_min=30,
        course_template_id=tpl.id,
    )
    # 今週 visit (course_id なし)
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 既存 ct_id が保持されるので "unchanged" のはず.
    assert body["summary"]["pfv_unchanged"] == 1
    assert body["summary"]["pfv_inserted"] == 0
    assert body["summary"]["pfv_updated"] == 0
    assert body["changes"][0]["operation"] == "unchanged"

    # DB 上の ct_id も NULL 上書きされていないこと.
    pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv is not None
    assert pfv.course_template_id == tpl.id


@pytest.mark.asyncio
async def test_sync_uses_visit_course_template_id_when_resolvable(client, db) -> None:
    """Wave Next 1 H1 補強: visit.course_id が active course を指していれば、
    その course.template_id を採用する (= 通常パス).
    """
    admin = await _make_user(db, email="sync-h1b-admin@example.com", role="admin")
    office = await _make_office(db, name="H1B-Office")
    tpl = await _make_course_template(db, office.id, label="H1B-A")
    course = await _make_course(
        db,
        office_id=office.id,
        template_id=tpl.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
    )
    p = await _make_patient(db, code="SYNC-H1B-1")

    v = Visit(
        patient_id=p.id,
        visit_date=MON,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
        course_id=course.id,
    )
    db.add(v)
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["pfv_inserted"] == 1
    pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv is not None
    assert pfv.course_template_id == tpl.id


# ---------------------------------------------------------------------------
# 13) M1: soft-deleted course は ct_id 逆引きに使わない → H1 fallback が効く
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_ignores_soft_deleted_course_for_ct_id_resolution(client, db) -> None:
    """Wave Next 1 M1: visit.course_id が soft-deleted course を指している場合、
    course.template_id は採用せず、既存 PFV の ct_id を保持する (H1 fallback).
    """
    from datetime import UTC, datetime

    admin = await _make_user(db, email="sync-m1-admin@example.com", role="admin")
    office = await _make_office(db, name="M1-Office")
    tpl_existing = await _make_course_template(db, office.id, label="M1-X")
    tpl_deleted_course = await _make_course_template(db, office.id, label="M1-D")
    deleted_course = await _make_course(
        db,
        office_id=office.id,
        template_id=tpl_deleted_course.id,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
    )
    # course を soft-delete.
    deleted_course.deleted_at = datetime.now(tz=UTC)
    await db.commit()

    p = await _make_patient(db, code="SYNC-M1-1")
    await _make_pfv(
        db,
        patient=p,
        weekday=0,
        start=time(9, 0),
        duration_min=30,
        course_template_id=tpl_existing.id,
    )
    v = Visit(
        patient_id=p.id,
        visit_date=MON,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=1,
        course_id=deleted_course.id,
    )
    db.add(v)
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 既存 ct_id を保持 → unchanged
    assert body["summary"]["pfv_unchanged"] == 1
    pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv is not None
    assert pfv.course_template_id == tpl_existing.id


# ---------------------------------------------------------------------------
# 14) H2: requires_multiple_staff=True で同 weekday に visit 2 件 → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_skips_multi_staff_weekday(client, db) -> None:
    """Wave Next 1 H2: 2 名体制患者 (requires_multiple_staff=True) で同 weekday に
    visit が 2 件ある場合は当該 weekday を ``operation="skipped"`` で返す.
    既存 PFV は触らない.
    """
    from uuid import uuid4

    admin = await _make_user(db, email="sync-h2-admin@example.com", role="admin")
    p = Patient(
        code="SYNC-H2-1",
        name="P-SYNC-H2-1",
        status="active",
        special_week_active=[],
        requires_multiple_staff=True,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)

    # 2 名体制ペア: 同 Mon に同 visit_group_id で 2 件
    grp = uuid4()
    v1 = Visit(
        patient_id=p.id,
        visit_date=MON,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=2,
        visit_group_id=grp,
    )
    v2 = Visit(
        patient_id=p.id,
        visit_date=MON,
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status="planned",
        source="manual",
        required_staff_count=2,
        visit_group_id=grp,
    )
    db.add_all([v1, v2])
    await db.commit()

    # 既存 PFV (slot 0) を Mon に置いておく → skipped でも触らない (apply 後も残存).
    await _make_pfv(db, patient=p, weekday=0, start=time(8, 0), duration_min=30)

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["pfv_skipped"] == 1
    assert body["summary"]["pfv_inserted"] == 0
    assert body["summary"]["pfv_updated"] == 0
    assert body["summary"]["pfv_unchanged"] == 0

    skipped_entries = [c for c in body["changes"] if c["operation"] == "skipped"]
    assert len(skipped_entries) == 1
    entry = skipped_entries[0]
    assert entry["weekday"] == 0
    assert entry["new"] is None
    assert entry["reason"] == "multi_staff_not_supported"

    # 既存 PFV (Mon 08:00) は触れていない.
    pfv = await db.scalar(
        select(PatientFixedVisit).where(
            PatientFixedVisit.patient_id == p.id,
            PatientFixedVisit.weekday == 0,
        )
    )
    assert pfv is not None
    assert pfv.start_time == time(8, 0)


# ---------------------------------------------------------------------------
# 15) M3: 今週 visit が無い既存 PFV は untouched_existing に列挙される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_lists_untouched_existing_pfv(client, db) -> None:
    """Wave Next 1 M3: 今週に visit が無い既存 PFV を ``untouched_existing`` で返す.
    apply 後もその PFV は残存する.
    """
    admin = await _make_user(db, email="sync-m3-admin@example.com", role="admin")
    p = await _make_patient(db, code="SYNC-M3-1")
    # 今週 Mon に visit (insert) のみ. Tue/Wed の既存 PFV は zombie 候補.
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))
    await _make_pfv(db, patient=p, weekday=1, start=time(10, 0))  # Tue
    await _make_pfv(db, patient=p, weekday=2, start=time(11, 0))  # Wed

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # untouched_existing に Tue / Wed が含まれる (weekday 順).
    untouched = body["untouched_existing"]
    assert isinstance(untouched, list)
    untouched_wd = sorted(u["weekday"] for u in untouched)
    assert untouched_wd == [1, 2]
    # changes 側には Tue/Wed が含まれない (= 触らない).
    changes_wd = {c["weekday"] for c in body["changes"]}
    assert 1 not in changes_wd and 2 not in changes_wd

    # apply 後も DB に Tue/Wed の PFV が残存している.
    pfv_rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    weekdays = sorted(p_.weekday for p_ in pfv_rows)
    # Mon (insert) + Tue/Wed (既存) = [0, 1, 2]
    assert weekdays == [0, 1, 2]
