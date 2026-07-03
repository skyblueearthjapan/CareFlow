"""Wave U-2 (変更反映先の統一 §2.2 / PO 決定 D-1/D-2) の BE 契約テスト.

対象 (U-1 の残り操作への展開):
  1. apply-swap change_scope
     - pattern_and_week → 両患者の今週 visits 再生成 (week_sync patients=2)
     - week_only        → PFV 不変・両側 visit が相互の新位置へ移動・source='manual_week'
     - 省略時 (pattern_only) → PFV のみ入れ替え・week_sync=null (従来)
  2. apply-individual change_scope
     - pattern_and_week → PFV 更新 + 当該患者の今週 visits 再生成 (week_sync)
     - 省略時 (pattern_only) → PFV のみ更新・visits 不変・week_sync=null
     - pattern_and_week で iso 欠落 → 422
  3. POST /v2/visit-move-week-only (汎用「1 手の週だけ移動」)
     - 移動成功: source='manual_week' / PFV 不変
     - 移動元に一致する pinned PFV → 422
     - 対象 visit なし → visits_moved=0 の 200
     - RBAC: staff は 403
  4. visits 一覧 API (/api/v1/visits) の item に source が乗る (FE「今週のみ」バッジ根拠)

設計書: docs/plans/change-scope-unification-design.md
Backend で APP_ENV=test ガード済み (conftest.py). 本番 DB 禁止 (ローカル SQLite).
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.user import User
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK, VISIT_STATUS_PLANNED, Visit

# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date(2026, 5, 11)

_SWAP_URL = "/api/v1/schedule/v2/improvement-suggestions/apply-swap"
_INDIVIDUAL_URL = "/api/v1/schedule/v2/apply-individual"
_MOVE_URL = "/api/v1/schedule/v2/visit-move-week-only"
_VISITS_URL = "/api/v1/visits"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, name: str) -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_patient(db, code: str, *, office_id=None) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        special_week_active=[],
        lat=35.65,
        lng=140.1,
        primary_office_id=office_id,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(db, *, patient, weekday, start, end, source="auto") -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=MON + timedelta(days=weekday),
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source=source,
        required_staff_count=1,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _make_pfv(
    db, *, patient, weekday, start, duration_min=30, is_pinned=False
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=duration_min,
        slot_index=0,
        movability="day_flexible",
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


async def _active_visits(db, patient_id):
    return (
        await db.scalars(
            select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
        )
    ).all()


async def _pfvs(db, patient_id):
    return (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
        )
    ).all()


# ---------------------------------------------------------------------------
# 1) apply-swap change_scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_swap_pattern_and_week_regenerates_both(client, db) -> None:
    """A: PFV 入れ替え + 両患者の今週 visits 再生成 (week_sync patients=2)."""
    admin = await _make_user(db, email="u2-swap-a@example.com", role="admin")
    office = await _make_office(db, "u2-swap-a-office")
    a = await _make_patient(db, "U2-SWAP-A-A", office_id=office.id)
    b = await _make_patient(db, "U2-SWAP-A-B", office_id=office.id)
    # A: Mon 10:00, B: Wed 09:00 (PFV + 今週 auto visit).
    await _make_pfv(db, patient=a, weekday=0, start=time(10, 0))
    await _make_pfv(db, patient=b, weekday=2, start=time(9, 0))
    await _make_visit(db, patient=a, weekday=0, start=time(10, 0), end=time(10, 30))
    await _make_visit(db, patient=b, weekday=2, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        _SWAP_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:00"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "change_scope": "pattern_and_week",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    assert body["change_scope"] == "pattern_and_week"
    assert body["week_sync"] is not None
    assert body["week_sync"]["patients"] == 2
    assert body["week_sync"]["visits_regenerated"] >= 1

    # PFV は入れ替え済み: A→Wed 09:00, B→Mon 10:00.
    a_rows = await _pfvs(db, a.id)
    b_rows = await _pfvs(db, b.id)
    assert len(a_rows) == 1 and a_rows[0].weekday == 2 and a_rows[0].start_time == time(9, 0)
    assert len(b_rows) == 1 and b_rows[0].weekday == 0 and b_rows[0].start_time == time(10, 0)

    # 今週 visits も新位置へ再生成 (A は Wed 09:00, B は Mon 10:00).
    a_starts = {(v.visit_date, v.start_time) for v in await _active_visits(db, a.id)}
    b_starts = {(v.visit_date, v.start_time) for v in await _active_visits(db, b.id)}
    assert (MON + timedelta(days=2), time(9, 0)) in a_starts, a_starts
    assert (MON, time(10, 0)) in b_starts, b_starts


@pytest.mark.asyncio
async def test_apply_swap_week_only_moves_visits_keeps_pfv(client, db) -> None:
    """B: PFV 不変 / 両側の今週 visit が相互の新位置へ移動 / source='manual_week'."""
    admin = await _make_user(db, email="u2-swap-b@example.com", role="admin")
    office = await _make_office(db, "u2-swap-b-office")
    a = await _make_patient(db, "U2-SWAP-B-A", office_id=office.id)
    b = await _make_patient(db, "U2-SWAP-B-B", office_id=office.id)
    await _make_pfv(db, patient=a, weekday=0, start=time(10, 0))
    await _make_pfv(db, patient=b, weekday=2, start=time(9, 0))
    await _make_visit(db, patient=a, weekday=0, start=time(10, 0), end=time(10, 30))
    await _make_visit(db, patient=b, weekday=2, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        _SWAP_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:00"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "change_scope": "week_only",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["change_scope"] == "week_only"
    assert body["week_sync"] is not None
    assert body["week_sync"]["patients"] == 2
    assert body["week_sync"]["visits_regenerated"] == 2
    assert body["week_sync"]["visits_soft_deleted"] == 0

    # PFV は完全に不変 (A→Mon 10:00, B→Wed 09:00 のまま).
    a_rows = await _pfvs(db, a.id)
    b_rows = await _pfvs(db, b.id)
    assert a_rows[0].weekday == 0 and a_rows[0].start_time == time(10, 0)
    assert b_rows[0].weekday == 2 and b_rows[0].start_time == time(9, 0)

    # 今週 visit は相互の新位置へ移動し source='manual_week'.
    a_vs = await _active_visits(db, a.id)
    b_vs = await _active_visits(db, b.id)
    assert len(a_vs) == 1 and a_vs[0].visit_date == MON + timedelta(days=2)
    assert a_vs[0].start_time == time(9, 0) and a_vs[0].source == VISIT_SOURCE_MANUAL_WEEK
    assert len(b_vs) == 1 and b_vs[0].visit_date == MON
    assert b_vs[0].start_time == time(10, 0) and b_vs[0].source == VISIT_SOURCE_MANUAL_WEEK


@pytest.mark.asyncio
async def test_apply_swap_pattern_only_leaves_visits_untouched(client, db) -> None:
    """省略時 (pattern_only): PFV のみ入れ替え / 今週 visits 不変 / week_sync=null."""
    admin = await _make_user(db, email="u2-swap-legacy@example.com", role="admin")
    office = await _make_office(db, "u2-swap-legacy-office")
    a = await _make_patient(db, "U2-SWAP-L-A", office_id=office.id)
    b = await _make_patient(db, "U2-SWAP-L-B", office_id=office.id)
    await _make_pfv(db, patient=a, weekday=0, start=time(10, 0))
    await _make_pfv(db, patient=b, weekday=2, start=time(9, 0))
    va = await _make_visit(db, patient=a, weekday=0, start=time(10, 0), end=time(10, 30))
    vb = await _make_visit(db, patient=b, weekday=2, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        _SWAP_URL,
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a.id),
            "patient_b_id": str(b.id),
            "a_new": {"weekday": 2, "start_time": "09:00"},
            "b_new": {"weekday": 0, "start_time": "10:00"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["change_scope"] == "pattern_only"
    assert body["week_sync"] is None

    # PFV は入れ替え済み.
    a_rows = await _pfvs(db, a.id)
    b_rows = await _pfvs(db, b.id)
    assert a_rows[0].weekday == 2 and b_rows[0].weekday == 0
    # 今週 visits は不変 (元の id・位置のまま).
    assert [v.id for v in await _active_visits(db, a.id)] == [va.id]
    assert [v.id for v in await _active_visits(db, b.id)] == [vb.id]


# ---------------------------------------------------------------------------
# 2) apply-individual change_scope
# ---------------------------------------------------------------------------


def _individual_plan(office_id) -> dict:
    return {
        "weekday": 0,
        "start_time": "10:00",
        "end_time": "10:30",
        "duration_min": 30,
        "course_code": "A",
        "office_id": str(office_id),
        "am_pm": "am",
    }


@pytest.mark.asyncio
async def test_apply_individual_pattern_and_week_regenerates(client, db) -> None:
    """A: PFV を Mon 10:00 に更新 + 今週 visits も Mon 10:00 に再生成 (week_sync)."""
    admin = await _make_user(db, email="u2-ind-a@example.com", role="admin")
    office = await _make_office(db, "u2-ind-a-office")
    p = await _make_patient(db, "U2-IND-A", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    await _make_visit(db, patient=p, weekday=0, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        _INDIVIDUAL_URL,
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [_individual_plan(office.id)],
            "change_scope": "pattern_and_week",
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applied"] is True
    assert body["week_sync"] is not None
    assert body["week_sync"]["visits_regenerated"] >= 1

    # 今週 visit は Mon 10:00 へ再生成 (旧 09:00 auto は soft-delete).
    starts = {v.start_time for v in await _active_visits(db, p.id)}
    assert time(10, 0) in starts, starts
    assert time(9, 0) not in starts


@pytest.mark.asyncio
async def test_apply_individual_pattern_only_leaves_visits_untouched(client, db) -> None:
    """省略時 (pattern_only): PFV は更新されるが今週 visits は不変 (week_sync=null)."""
    admin = await _make_user(db, email="u2-ind-legacy@example.com", role="admin")
    office = await _make_office(db, "u2-ind-legacy-office")
    p = await _make_patient(db, "U2-IND-L", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    existing = await _make_visit(db, patient=p, weekday=0, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        _INDIVIDUAL_URL,
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [_individual_plan(office.id)],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["week_sync"] is None

    # 今週 visits は不変 (旧 09:00 のまま).
    visits = await _active_visits(db, p.id)
    assert [v.id for v in visits] == [existing.id]
    assert visits[0].start_time == time(9, 0)


@pytest.mark.asyncio
async def test_apply_individual_pattern_and_week_missing_iso_422(client, db) -> None:
    """pattern_and_week で iso_year/iso_week 欠落 → 422 (PFV 更新前に弾く)."""
    admin = await _make_user(db, email="u2-ind-422@example.com", role="admin")
    office = await _make_office(db, "u2-ind-422-office")
    p = await _make_patient(db, "U2-IND-422", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))

    res = await client.post(
        _INDIVIDUAL_URL,
        headers=_bearer(admin),
        json={
            "patient_id": str(p.id),
            "confirm": True,
            "visit_plans": [_individual_plan(office.id)],
            "change_scope": "pattern_and_week",
        },
    )
    assert res.status_code == 422, res.text

    # PFV は変更されていない (Mon 09:00 のまま).
    rows = await _pfvs(db, p.id)
    assert rows[0].start_time == time(9, 0)


# ---------------------------------------------------------------------------
# 3) POST /v2/visit-move-week-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_move_week_only_moves_and_keeps_pfv(client, db) -> None:
    """移動成功: visit が新位置へ / source='manual_week' / PFV 不変."""
    admin = await _make_user(db, email="u2-move-ok@example.com", role="admin")
    office = await _make_office(db, "u2-move-ok-office")
    p = await _make_patient(db, "U2-MOVE-OK", office_id=office.id)
    # 非 pinned PFV Mon 10:00 (PFV 不変を検証) + 今週 visit Mon 10:00.
    await _make_pfv(db, patient=p, weekday=0, start=time(10, 0))
    await _make_visit(db, patient=p, weekday=0, start=time(10, 0), end=time(10, 30))

    res = await client.post(
        _MOVE_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_id": str(p.id),
            "old_weekday": 0,
            "old_start_time": "10:00:00",
            "new_weekday": 1,
            "new_start_time": "11:00:00",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1

    # visit は Tue 11:00 へ移動し source='manual_week'.
    visits = await _active_visits(db, p.id)
    assert len(visits) == 1
    assert visits[0].visit_date == MON + timedelta(days=1)
    assert visits[0].start_time == time(11, 0)
    assert visits[0].source == VISIT_SOURCE_MANUAL_WEEK

    # PFV は完全に不変 (Mon 10:00 のまま).
    rows = await _pfvs(db, p.id)
    assert rows[0].weekday == 0 and rows[0].start_time == time(10, 0)


@pytest.mark.asyncio
async def test_visit_move_week_only_pinned_pfv_is_422(client, db) -> None:
    """移動元に一致する pinned PFV があれば 422 / visit は動かない."""
    admin = await _make_user(db, email="u2-move-pinned@example.com", role="admin")
    office = await _make_office(db, "u2-move-pinned-office")
    p = await _make_patient(db, "U2-MOVE-PIN", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(10, 0), is_pinned=True)
    await _make_visit(db, patient=p, weekday=0, start=time(10, 0), end=time(10, 30))

    res = await client.post(
        _MOVE_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_id": str(p.id),
            "old_weekday": 0,
            "old_start_time": "10:00:00",
            "new_weekday": 1,
            "new_start_time": "11:00:00",
        },
    )
    assert res.status_code == 422, res.text

    # visit は動いていない (Mon 10:00 のまま).
    visits = await _active_visits(db, p.id)
    assert len(visits) == 1
    assert visits[0].visit_date == MON and visits[0].start_time == time(10, 0)


@pytest.mark.asyncio
async def test_visit_move_week_only_no_target_returns_zero(client, db) -> None:
    """移動元に該当 visit が無ければ visits_moved=0 の 200 (この週に元々出ていない)."""
    admin = await _make_user(db, email="u2-move-none@example.com", role="admin")
    office = await _make_office(db, "u2-move-none-office")
    p = await _make_patient(db, "U2-MOVE-NONE", office_id=office.id)

    res = await client.post(
        _MOVE_URL,
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_id": str(p.id),
            "old_weekday": 0,
            "old_start_time": "10:00:00",
            "new_weekday": 1,
            "new_start_time": "11:00:00",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 0


@pytest.mark.asyncio
async def test_visit_move_week_only_rbac_staff_forbidden(client, db) -> None:
    """RBAC: staff ロールは 403."""
    staff = await _make_user(db, email="u2-move-staff@example.com", role="staff")
    office = await _make_office(db, "u2-move-staff-office")
    p = await _make_patient(db, "U2-MOVE-STAFF", office_id=office.id)

    res = await client.post(
        _MOVE_URL,
        headers=_bearer(staff),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "patient_id": str(p.id),
            "old_weekday": 0,
            "old_start_time": "10:00:00",
            "new_weekday": 1,
            "new_start_time": "11:00:00",
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 4) visits 一覧 API に source が乗る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visits_list_includes_source(client, db) -> None:
    """/api/v1/visits の item に source が含まれる (manual_week バッジの根拠)."""
    admin = await _make_user(db, email="u2-visits-src@example.com", role="admin")
    office = await _make_office(db, "u2-visits-src-office")
    p = await _make_patient(db, "U2-VIS-SRC", office_id=office.id)
    await _make_visit(
        db, patient=p, weekday=0, start=time(10, 0), end=time(10, 30), source="manual_week"
    )

    res = await client.get(
        _VISITS_URL,
        headers=_bearer(admin),
        params={
            "week_start": MON.isoformat(),
            "week_end": (MON + timedelta(days=6)).isoformat(),
            "limit": 500,
        },
    )
    assert res.status_code == 200, res.text
    items = res.json()
    mine = [it for it in items if it["patient_id"] == str(p.id)]
    assert len(mine) == 1
    assert mine[0]["source"] == "manual_week"
