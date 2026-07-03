"""Wave U-0 (変更反映先の統一 §2.2) の BE 共通プリミティブ + 既存バグ修正テスト.

対象:
  1. POST /api/v1/schedule/v2/sync-fixed-to-week (新設・1 患者版 型→週同期)
  2. reset_visits_to_fixed の patient_id フィルタが全患者版の挙動を変えない
  3. source='manual_week' が generate-week-only / reset-to-fixed 両方で保護される
  4. sync-week-visits-to-fixed が pinned PFV の is_pinned/movability を保持する
  5. from-week-bulk / from-week が pinned PFV を消さない + 重複 INSERT なし +
     非 pinned は置換 + movability 引継ぎ

設計書: docs/plans/change-scope-unification-design.md
Backend で APP_ENV=test ガード済み (conftest.py).
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User, Visit
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_SOURCE_MANUAL_WEEK
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed
from app.services.scheduling.layer1_expander import Layer1Expander

# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date(2026, 5, 11)
TUE = date(2026, 5, 12)


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


async def _make_patient(db, code: str, *, office_id=None, requires_multi: bool = False) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        special_week_active=[],
        lat=35.65,
        lng=140.10,
        primary_office_id=office_id,
        requires_multiple_staff=requires_multi,
    )
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
    visit_group_id=None,
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
        visit_group_id=visit_group_id,
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
    slot_index: int = 0,
    is_pinned: bool = False,
    movability: str = "unknown",
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        start_time=start,
        duration_min=duration_min,
        slot_index=slot_index,
        is_pinned=is_pinned,
        movability=movability,
    )
    db.add(pfv)
    await db.commit()
    await db.refresh(pfv)
    return pfv


async def _active_visits(db, patient_id):
    return (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient_id,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()


async def _pfvs(db, patient_id, mode: str = "normal"):
    return (
        await db.scalars(
            select(PatientFixedVisit)
            .where(
                PatientFixedVisit.patient_id == patient_id,
                PatientFixedVisit.mode == mode,
            )
            .order_by(PatientFixedVisit.weekday, PatientFixedVisit.slot_index)
        )
    ).all()


# ===========================================================================
# 1) sync-fixed-to-week (endpoint)
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_fixed_to_week_regenerates_patient_preserves_others_and_manual(
    client, db
) -> None:
    """PFV に枠追加→sync→当該患者の今週 visits に出現 / 他患者不変 / manual visit 温存."""
    admin = await _make_user(db, email="u0-sync-admin@example.com", role="admin")
    office = await _make_office(db, "u0-sync-office")
    p1 = await _make_patient(db, "U0-SYNC-1", office_id=office.id)
    p2 = await _make_patient(db, "U0-SYNC-2", office_id=office.id)

    # p1: 固定枠 Mon 09:00 (新規枠) + Tue 14:00 の手動 visit (保護対象).
    await _make_pfv(db, patient=p1, weekday=0, start=time(9, 0))
    manual_visit = await _make_visit(
        db, patient=p1, visit_date=TUE, start=time(14, 0), end=time(14, 30), source="manual"
    )
    # p2: 同じ office の別患者の visit (isolation: 触られてはいけない).
    p2_visit = await _make_visit(
        db, patient=p2, visit_date=MON, start=time(10, 0), end=time(10, 30), source="manual"
    )

    res = await client.post(
        "/api/v1/schedule/v2/sync-fixed-to-week",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "patient_id": str(p1.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patient_id"] == str(p1.id)
    assert body["visits_regenerated"] >= 1

    # p1: Mon 09:00 の visit が PFV から生成されている.
    p1_visits = await _active_visits(db, p1.id)
    mon_gen = [v for v in p1_visits if v.visit_date == MON and v.start_time == time(9, 0)]
    assert len(mon_gen) == 1, f"Mon 09:00 visit が生成されるはず: {p1_visits}"

    # p1: Tue の手動 visit は温存 (source='manual' 保護).
    manual_still = [v for v in p1_visits if v.id == manual_visit.id]
    assert len(manual_still) == 1, "source='manual' visit は soft-delete されない"

    # p2: 他患者の visit は不変.
    p2_visits = await _active_visits(db, p2.id)
    assert [v.id for v in p2_visits] == [p2_visit.id]


@pytest.mark.asyncio
async def test_sync_fixed_to_week_404_for_missing_patient(client, db) -> None:
    admin = await _make_user(db, email="u0-sync-404@example.com", role="admin")
    res = await client.post(
        "/api/v1/schedule/v2/sync-fixed-to-week",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "patient_id": str(uuid4())},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_sync_fixed_to_week_rbac_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="u0-sync-staff@example.com", role="staff")
    office = await _make_office(db, "u0-sync-rbac-office")
    p = await _make_patient(db, "U0-SYNC-RBAC", office_id=office.id)
    res = await client.post(
        "/api/v1/schedule/v2/sync-fixed-to-week",
        headers=_bearer(staff_user),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "patient_id": str(p.id)},
    )
    assert res.status_code == 403, res.text


# ===========================================================================
# 2) patient_id フィルタが全患者版の挙動を変えない (service)
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_patient_filter_scopes_to_single_patient(db) -> None:
    """reset_visits_to_fixed(patient_id=p1) は p1 のみ再生成し p2 に触れない."""
    office = await _make_office(db, "u0-filter-office")
    p1 = await _make_patient(db, "U0-FILT-1", office_id=office.id)
    p2 = await _make_patient(db, "U0-FILT-2", office_id=office.id)
    await _make_pfv(db, patient=p1, weekday=0, start=time(9, 0))
    await _make_pfv(db, patient=p2, weekday=0, start=time(11, 0))

    result = await reset_visits_to_fixed(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        office_ids=[office.id],
        patient_id=p1.id,
    )
    await db.commit()

    assert result["visits_regenerated"] == 1
    p1_visits = await _active_visits(db, p1.id)
    assert len(p1_visits) == 1 and p1_visits[0].start_time == time(9, 0)
    # p2 は再生成されない (フィルタ外).
    p2_visits = await _active_visits(db, p2.id)
    assert p2_visits == []


@pytest.mark.asyncio
async def test_reset_all_patients_unaffected_by_filter_param(db) -> None:
    """patient_id=None (既定) は従来どおり office 内全 active 患者を再生成する."""
    office = await _make_office(db, "u0-all-office")
    p1 = await _make_patient(db, "U0-ALL-1", office_id=office.id)
    p2 = await _make_patient(db, "U0-ALL-2", office_id=office.id)
    await _make_pfv(db, patient=p1, weekday=0, start=time(9, 0))
    await _make_pfv(db, patient=p2, weekday=0, start=time(11, 0))

    result = await reset_visits_to_fixed(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        office_ids=[office.id],
    )
    await db.commit()

    assert result["visits_regenerated"] == 2
    assert len(await _active_visits(db, p1.id)) == 1
    assert len(await _active_visits(db, p2.id)) == 1


# ===========================================================================
# 3) manual_week 保護 (reset-to-fixed + generate-week-only)
# ===========================================================================


@pytest.mark.asyncio
async def test_manual_week_survives_reset_to_fixed(db) -> None:
    """source='manual_week' の visit は reset-to-fixed で soft-delete されない."""
    office = await _make_office(db, "u0-mw-reset-office")
    p = await _make_patient(db, "U0-MW-RESET", office_id=office.id)
    # PFV Mon 09:00 (再生成対象) + manual_week visit Tue 14:00 (保護対象).
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0))
    mw_visit = await _make_visit(
        db,
        patient=p,
        visit_date=TUE,
        start=time(14, 0),
        end=time(14, 30),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )

    await reset_visits_to_fixed(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id], patient_id=p.id
    )
    await db.commit()

    survivors = await _active_visits(db, p.id)
    assert mw_visit.id in [v.id for v in survivors], "manual_week visit は reset で残る"


@pytest.mark.asyncio
async def test_manual_week_survives_generate_week_only_delete(db) -> None:
    """source='manual_week' の visit は generate-week-only (Layer1 auto 削除) で残る."""
    office = await _make_office(db, "u0-mw-gen-office")
    p = await _make_patient(db, "U0-MW-GEN", office_id=office.id)
    mw_visit = await _make_visit(
        db,
        patient=p,
        visit_date=MON,
        start=time(9, 0),
        end=time(9, 30),
        source=VISIT_SOURCE_MANUAL_WEEK,
    )
    auto_visit = await _make_visit(
        db,
        patient=p,
        visit_date=MON,
        start=time(15, 0),
        end=time(15, 30),
        source="auto",
    )

    # Layer1 の当該週 auto 削除を直接呼ぶ (generate-week-only の削除経路).
    await Layer1Expander()._delete_existing_auto_visits(db, week_monday=MON, patient_ids=[p.id])
    await db.commit()

    survivors = await _active_visits(db, p.id)
    survivor_ids = [v.id for v in survivors]
    assert mw_visit.id in survivor_ids, "manual_week は Layer1 auto 削除で残る"
    assert auto_visit.id not in survivor_ids, "source='auto' は削除される (対照)"


# ===========================================================================
# 4) sync-week-visits-to-fixed の is_pinned / movability 保持 (endpoint)
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_week_visits_to_fixed_preserves_pin_on_update(client, db) -> None:
    """pinned PFV がある weekday を sync (時刻変更→update) しても pin/movability 保持."""
    admin = await _make_user(db, email="u0-pin-sync@example.com", role="admin")
    p = await _make_patient(db, "U0-PIN-SYNC")
    # pinned PFV: Mon 09:00, movability='locked'.
    pfv = await _make_pfv(
        db, patient=p, weekday=0, start=time(9, 0), is_pinned=True, movability="locked"
    )
    # 今週 Mon の visit は 10:00 (PFV と時刻が異なる → update 経路).
    await _make_visit(db, patient=p, visit_date=MON, start=time(10, 0), end=time(10, 30))

    res = await client.post(
        f"/api/v1/patients/{p.id}/sync-week-visits-to-fixed",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "dry_run": False},
    )
    assert res.status_code == 200, res.text

    await db.refresh(pfv)
    assert pfv.start_time == time(10, 0), "start_time は update される"
    assert pfv.is_pinned is True, "is_pinned は保持される (silent に False にならない)"
    assert pfv.movability == "locked", "movability も保持される"


# ===========================================================================
# 5) from-week-bulk / from-week のピン消失修正 (endpoint)
# ===========================================================================


@pytest.mark.asyncio
async def test_from_week_bulk_preserves_pinned_and_no_duplicate(client, db) -> None:
    """全件保存 (from-week-bulk) しても pinned PFV は残り、同枠の重複 INSERT なし."""
    admin = await _make_user(db, email="u0-bulk-pin@example.com", role="admin")
    office = await _make_office(db, "u0-bulk-office")
    p = await _make_patient(db, "U0-BULK-PIN", office_id=office.id)
    # pinned PFV: Mon 09:00 (完全固定・残すべき).
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0), is_pinned=True, movability="locked")
    # 今週の visit: Mon 09:00 (= pinned と同枠). 重複 INSERT されてはいけない.
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 30))

    res = await client.post(
        "/api/v1/patients/fixed-visits/from-week-bulk",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text

    pfvs = await _pfvs(db, p.id)
    # Mon 09:00 の PFV は 1 行のみ (pinned が残り、重複 INSERT されない).
    mon = [x for x in pfvs if x.weekday == 0]
    assert len(mon) == 1, f"Mon の PFV は 1 行のみ (重複なし): {pfvs}"
    assert mon[0].is_pinned is True, "pinned PFV は残る"
    assert mon[0].movability == "locked", "pinned 行の movability も保持"


@pytest.mark.asyncio
async def test_from_week_bulk_replaces_nonpinned_and_inherits_movability(client, db) -> None:
    """非 pinned 行は従来どおり置換され、同一 (weekday, start_time) の movability を引き継ぐ."""
    admin = await _make_user(db, email="u0-bulk-mov@example.com", role="admin")
    office = await _make_office(db, "u0-bulk-mov-office")
    p = await _make_patient(db, "U0-BULK-MOV", office_id=office.id)
    # 非 pinned PFV: Mon 09:00, movability='time_flexible'.
    await _make_pfv(
        db,
        patient=p,
        weekday=0,
        start=time(9, 0),
        is_pinned=False,
        movability="time_flexible",
    )
    # 今週の visit: Mon 09:00 (同一 weekday+start → movability 引継ぎ).
    await _make_visit(db, patient=p, visit_date=MON, start=time(9, 0), end=time(9, 45))

    res = await client.post(
        "/api/v1/patients/fixed-visits/from-week-bulk",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text

    pfvs = await _pfvs(db, p.id)
    mon = [x for x in pfvs if x.weekday == 0]
    assert len(mon) == 1
    # 置換されて duration が visit に合う (09:00-09:45 = 45 分).
    assert mon[0].duration_min == 45, "非 pinned 行は visit で置換される"
    assert mon[0].is_pinned is False
    assert mon[0].movability == "time_flexible", "旧行から movability を引き継ぐ"


@pytest.mark.asyncio
async def test_from_week_single_preserves_pinned(client, db) -> None:
    """from-week (単一患者版) も pinned PFV を消さない."""
    admin = await _make_user(db, email="u0-single-pin@example.com", role="admin")
    office = await _make_office(db, "u0-single-office")
    p = await _make_patient(db, "U0-SINGLE-PIN", office_id=office.id)
    await _make_pfv(db, patient=p, weekday=0, start=time(9, 0), is_pinned=True, movability="locked")
    # Tue に非 pinned の新しい枠を作る visit.
    await _make_visit(db, patient=p, visit_date=TUE, start=time(13, 0), end=time(13, 30))

    res = await client.post(
        f"/api/v1/patients/{p.id}/fixed-visits/from-week",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text

    pfvs = await _pfvs(db, p.id)
    by_wd = {x.weekday: x for x in pfvs}
    assert 0 in by_wd, "pinned Mon PFV は from-week 後も残る"
    assert by_wd[0].is_pinned is True
    assert 1 in by_wd, "Tue の新規枠は INSERT される"
    assert by_wd[1].is_pinned is False
