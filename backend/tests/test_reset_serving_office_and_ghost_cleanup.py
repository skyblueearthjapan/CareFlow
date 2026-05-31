"""CareFlow 本番バグ修正の回帰テスト (reset_visits_to_fixed).

Bug B: 稼働曜日判定が「患者の主拠点」基準で、クロス拠点の土曜が落ちる.
    主拠点 = 都賀 (TSUGA, 月-金 / 土休業) の患者が PFV で 稲毛 (INAGE, 土稼働)
    コースに割当てられていても、 旧実装は operating_weekday 判定を主拠点基準で
    行うため土曜だけ生成 skip された.
    修正後は serving office (sub_office > course_template.office_id >
    primary_office_id) 基準で判定し、 土曜 visit が生成される.

Bug A: 非稼働患者の古い Visit が残存 (ゴースト).
    患者が非稼働 (suspended / inactive / pending) になると、 過去に生成された
    再生成系 Visit (source=auto/reset_v2, status=planned) が削除されず週ビューに
    残った. 修正後は対象週 × 対象 office 範囲の再生成対象 visit 全般 (status 不問
    の patient 範囲) を削除する. ただし manual / completed / cancelled は保護する.
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.models import Office, Patient
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.visit import (
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_PLANNED,
    Visit,
)
from app.services.scheduling.auto_allocator_v2 import reset_visits_to_fixed

# ---------------------------------------------------------------------------
# Bug B: serving office 基準の operating_weekday 判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_cross_office_saturday_pfv_is_generated(db) -> None:
    """主拠点=都賀 (土休業) × PFV=稲毛 (土稼働) コース → 土曜 visit が生成される.

    主拠点稼働日 (火) も従来通り生成される.
    """
    tsuga = Office(name="bugB-TSUGA", code="BUGB-T", operating_weekdays=[0, 1, 2, 3, 4])
    inage = Office(name="bugB-INAGE", code="BUGB-I", operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add_all([tsuga, inage])
    await db.flush()

    # 稲毛コーステンプレート (土稼働拠点).
    ct_inage_a = CourseTemplate(office_id=inage.id, label="A", capacity_mon=6)
    db.add(ct_inage_a)
    await db.flush()

    # 主拠点 = 都賀 の患者.
    p = Patient(
        code="BUGB-P",
        name="清水政憲 (主拠点都賀)",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=tsuga.id,
    )
    db.add(p)
    await db.flush()

    # 土曜 (weekday=5, 都賀休業 / 稲毛稼働) の PFV を 稲毛コースに割当て.
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=5,  # Sat
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
            is_pinned=True,
            course_template_id=ct_inage_a.id,
        )
    )
    # 火曜 (weekday=1, 都賀稼働) も稲毛コースに割当て (regression: 従来通り生成).
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,  # Tue
            start_time=time(9, 0),
            duration_min=35,
            slot_index=0,
            is_pinned=True,
            course_template_id=ct_inage_a.id,
        )
    )

    # 稲毛のスタッフ (土・火 出勤).
    s = Staff(name="bugB-st", role="staff", is_trainee=False, primary_office_id=inage.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=5, is_on=True))
    db.add(StaffShift(staff_id=s.id, weekday=1, is_on=True))
    await db.commit()

    # reset 対象 = 両拠点 (患者は primary=都賀 で pool 化され、 PFV の serving
    # office = 稲毛 で稼働曜日判定される). 本番の「全拠点指定」運用に相当.
    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[tsuga.id, inage.id],
        mode="legacy",
    )
    await db.commit()

    week_monday = date.fromisocalendar(2026, 20, 1)
    sat_date = date.fromordinal(week_monday.toordinal() + 5)
    tue_date = date.fromordinal(week_monday.toordinal() + 1)

    visits = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == p.id,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    dates = sorted(v.visit_date for v in visits)
    assert sat_date in dates, (
        f"Bug B: 主拠点都賀の土曜 PFV (稲毛コース) の visit が生成されていない: {dates}"
    )
    assert tue_date in dates, (
        f"Bug B regression: 主拠点稼働日 (火) の visit が生成されていない: {dates}"
    )
    assert result["visits_regenerated"] == 2


@pytest.mark.asyncio
async def test_reset_cross_office_pfv_skipped_when_serving_office_closed(db) -> None:
    """serving office 自体が休業の曜日は引き続き skip される (regression).

    主拠点=稲毛 (土稼働) だが、 PFV が 都賀 (土休業) コースを土曜に指す場合は
    serving office = 都賀 が休業なので skip される.
    """
    tsuga = Office(name="bugB2-TSUGA", code="BUGB2-T", operating_weekdays=[0, 1, 2, 3, 4])
    inage = Office(name="bugB2-INAGE", code="BUGB2-I", operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add_all([tsuga, inage])
    await db.flush()
    ct_tsuga_a = CourseTemplate(office_id=tsuga.id, label="A", capacity_mon=6)
    db.add(ct_tsuga_a)
    await db.flush()
    p = Patient(
        code="BUGB2-P",
        name="bugB2 patient",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=inage.id,
    )
    db.add(p)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=5,  # Sat, but serving=TSUGA closed
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
            is_pinned=True,
            course_template_id=ct_tsuga_a.id,
        )
    )
    s = Staff(name="bugB2-st", role="staff", is_trainee=False, primary_office_id=tsuga.id)
    db.add(s)
    await db.flush()
    db.add(StaffShift(staff_id=s.id, weekday=5, is_on=True))
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[tsuga.id, inage.id],
        mode="legacy",
        dry_run=True,
    )
    assert result.get("visits_to_insert") == 0, (
        f"serving office (都賀) 休業日の土曜 PFV は skip されるべき: {result}"
    )
    warnings = result.get("warnings") or []
    assert any("休業日" in w for w in warnings), f"休業 warning が出るはず: {warnings}"


# ---------------------------------------------------------------------------
# Bug A: 非稼働患者の旧 visit ゴースト掃除 + 保護
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_deletes_inactive_patient_ghost_visit_but_protects_manual(db) -> None:
    """非稼働患者の旧 auto/reset visit が削除され、 manual/completed は保護される."""
    office = Office(name="bugA-office", code="BUGA", operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add(office)
    await db.flush()

    # 非稼働患者 (suspended) の旧再生成 visit (削除対象).
    p_suspended = Patient(
        code="BUGA-SUS",
        name="田中妙子 (suspended)",
        status="suspended",
        lat=35.66,
        lng=140.11,
        primary_office_id=office.id,
    )
    # 非稼働患者 (pending) の手動 visit (保護対象).
    p_pending = Patient(
        code="BUGA-PEND",
        name="福島千春 (pending)",
        status="pending",
        lat=35.67,
        lng=140.12,
        primary_office_id=office.id,
    )
    db.add_all([p_suspended, p_pending])
    await db.flush()

    week_monday = date.fromisocalendar(2026, 20, 1)  # Mon

    # suspended 患者の旧 reset_v2 visit (planned) → 削除されるべき.
    ghost = Visit(
        patient_id=p_suspended.id,
        visit_date=week_monday,
        start_time=time(11, 0),
        end_time=time(11, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="reset_v2",
        required_staff_count=1,
    )
    # pending 患者の manual visit (planned) → 保護されるべき.
    manual_v = Visit(
        patient_id=p_pending.id,
        visit_date=week_monday,
        start_time=time(13, 0),
        end_time=time(13, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
        required_staff_count=1,
    )
    # suspended 患者の completed visit → 保護されるべき.
    completed_v = Visit(
        patient_id=p_suspended.id,
        visit_date=week_monday,
        start_time=time(14, 0),
        end_time=time(14, 35),
        type="regular",
        status=VISIT_STATUS_COMPLETED,
        source="reset_v2",
        required_staff_count=1,
    )
    # suspended 患者の cancelled visit → 保護されるべき.
    cancelled_v = Visit(
        patient_id=p_suspended.id,
        visit_date=week_monday,
        start_time=time(15, 0),
        end_time=time(15, 35),
        type="regular",
        status=VISIT_STATUS_CANCELLED,
        source="reset_v2",
        required_staff_count=1,
    )
    db.add_all([ghost, manual_v, completed_v, cancelled_v])
    await db.commit()

    result = await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[office.id],
        mode="legacy",
    )
    await db.commit()

    # ghost (suspended の auto/reset planned) は soft-delete されている.
    await db.refresh(ghost)
    assert ghost.deleted_at is not None, (
        "Bug A: 非稼働患者の旧 reset_v2 visit が削除されていない (ゴースト残存)"
    )

    # manual / completed / cancelled は保護されている.
    await db.refresh(manual_v)
    await db.refresh(completed_v)
    await db.refresh(cancelled_v)
    assert manual_v.deleted_at is None, "manual visit を誤って削除した"
    assert completed_v.deleted_at is None, "completed visit を誤って削除した"
    assert cancelled_v.deleted_at is None, "cancelled visit を誤って削除した"

    # 非稼働患者は再生成されない (PFV 無し + active 限定再生成).
    regen = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id.in_([p_suspended.id, p_pending.id]),
                Visit.source == "reset_v2",
                Visit.status == VISIT_STATUS_PLANNED,
                Visit.deleted_at.is_(None),
            )
        )
    ).all()
    assert regen == [], f"非稼働患者の visit が再生成された: {regen}"

    assert result["visits_soft_deleted"] >= 1


# ---------------------------------------------------------------------------
# 誤削除リスク回帰 (HIGH): 単一 office reset で cross-office 患者の visit を消さない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_office_reset_does_not_delete_cross_office_patient_visit(db) -> None:
    """主拠点=範囲外 office / sub_office PFV=範囲内 office の患者の既存 visit を保護.

    削除対象 patient 範囲 (``_load_visit_delete_target_patient_ids``) が再生成
    スコープ (``_load_active_patients`` = primary_office_id 基準) と非対称だと、
    主拠点が ``office_ids`` 範囲外で sub_office PFV だけ範囲内の cross-office 患者
    (例: 都賀 primary / 稲毛 serving) が step1 で削除されながら step2 で再生成
    されず消失する. 削除/再生成スコープを primary-office 基準で対称にした後は、
    単一 office reset でこの患者の既存 visit が **削除されない** ことを確認する.
    """
    tsuga = Office(name="xoff-TSUGA", code="XOFF-T", operating_weekdays=[0, 1, 2, 3, 4])
    inage = Office(name="xoff-INAGE", code="XOFF-I", operating_weekdays=[0, 1, 2, 3, 4, 5])
    db.add_all([tsuga, inage])
    await db.flush()

    # 稲毛コーステンプレート (serving office).
    ct_inage = CourseTemplate(office_id=inage.id, label="A", capacity_mon=6)
    db.add(ct_inage)
    await db.flush()

    # 主拠点 = 都賀 (= reset 範囲外) の患者. sub_office = 稲毛 (= reset 範囲内).
    p = Patient(
        code="XOFF-P",
        name="都賀primary稲毛serving患者",
        status="active",
        lat=35.65,
        lng=140.10,
        primary_office_id=tsuga.id,
    )
    db.add(p)
    await db.flush()

    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,  # Tue
            start_time=time(9, 0),
            duration_min=35,
            slot_index=0,
            is_pinned=True,
            course_template_id=ct_inage.id,
            sub_office_id=inage.id,
        )
    )

    week_monday = date.fromisocalendar(2026, 20, 1)  # Mon
    tue_date = date.fromordinal(week_monday.toordinal() + 1)

    # この患者の既存 reset_v2 / planned visit (= 削除されてはならない).
    existing = Visit(
        patient_id=p.id,
        visit_date=tue_date,
        start_time=time(9, 0),
        end_time=time(9, 35),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="reset_v2",
        required_staff_count=1,
    )
    db.add(existing)
    await db.commit()

    # 単一 office reset: 稲毛 (= sub_office) のみを指定. 主拠点 都賀 は範囲外.
    await reset_visits_to_fixed(
        db,
        iso_year=2026,
        iso_week=20,
        office_ids=[inage.id],
        mode="legacy",
    )
    await db.commit()

    await db.refresh(existing)
    assert existing.deleted_at is None, (
        "誤削除リスク: 主拠点が reset 範囲外の cross-office 患者の visit が、"
        "単一 office reset (sub_office 範囲内) で削除された (再生成されず消失する)"
    )
