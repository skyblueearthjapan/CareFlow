"""案Z「マスタを賢くする」BE (PO 決定 2026-08-09).

1. POST /patients/{id}/fixed-visits/validate — dry-run 検証 (書き込まない・
   患者間衝突 V3 が返る)
2. GET  /patients/{id}/fixed-visits/course-load — (曜日×コース) の他患者負荷
   (対象患者自身は含めない・定数つき)
"""

from __future__ import annotations

import uuid
from datetime import time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    CourseTemplate,
    Office,
    Patient,
    PatientNgStaff,
    Staff,
    User,
    Visit,
)
from app.models.patient_fixed_visit import PatientFixedVisit
from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base
from app.services.scheduling.config import load_scheduling_config
from app.services.scheduling.pfv_validator import validate_pfv_changes


async def _make_admin(db) -> User:
    user = User(
        email="smart-master-admin@example.com",
        password_hash=hash_password("does-not-matter-here"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed(db) -> dict:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    tpl = CourseTemplate(label="A", office_id=office.id)
    db.add(tpl)
    await db.flush()
    target = Patient(
        code="PT-SM-1",
        name="山田 太郎",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
        lat=35.65,
        lng=140.10,
    )
    other = Patient(
        code="PT-SM-2",
        name="佐藤 花子",
        status="active",
        insurance="medical",
        primary_office_id=office.id,
        # 意図的に離す (>100m) — 同住所ペア許容に吸われないように。
        lat=35.70,
        lng=140.20,
    )
    db.add_all([target, other])
    await db.flush()
    # 他患者の型: 月曜 10:00-10:35 コースA
    db.add(
        PatientFixedVisit(
            patient_id=other.id,
            mode="normal",
            weekday=0,
            start_time=time(10, 0),
            duration_min=35,
            slot_index=0,
            course_template_id=tpl.id,
            is_pinned=False,
        )
    )
    await db.commit()
    for obj in (office, tpl, target, other):
        await db.refresh(obj)
    return {"office": office, "tpl": tpl, "target": target, "other": other}


@pytest.mark.asyncio
async def test_validate_dry_run_returns_conflict_and_writes_nothing(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)

    res = await client.post(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/validate",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [
                {
                    "weekday": 0,
                    "start_time": "10:00",
                    "duration_min": 35,
                    "course_template_id": str(seeded["tpl"].id),
                    "slot_index": 0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    warnings = res.json()["warnings"]
    codes = {w["code"] for w in warnings}
    assert "patient_time_conflict" in codes
    conflict = next(w for w in warnings if w["code"] == "patient_time_conflict")
    assert "佐藤 花子" in conflict["message"]

    # dry-run: 対象患者の PFV は 1 行も作られていない
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == seeded["target"].id)
        )
    ).all()
    assert rows == []


@pytest.mark.asyncio
async def test_validate_clean_slot_returns_no_conflict(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)
    # 火曜 (他患者の型なし) → 衝突警告は出ない
    res = await client.post(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/validate",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [
                {
                    "weekday": 1,
                    "start_time": "10:00",
                    "duration_min": 35,
                    "course_template_id": str(seeded["tpl"].id),
                    "slot_index": 0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    codes = {w["code"] for w in res.json()["warnings"]}
    assert "patient_time_conflict" not in codes


@pytest.mark.asyncio
async def test_course_load_aggregates_other_patients_only(client, db) -> None:
    seeded = await _seed(db)
    admin = await _make_admin(db)
    # 対象患者自身にも型を作る — 集計に含まれないことの検証用
    db.add(
        PatientFixedVisit(
            patient_id=seeded["target"].id,
            mode="normal",
            weekday=0,
            start_time=time(14, 0),
            duration_min=60,
            slot_index=0,
            course_template_id=seeded["tpl"].id,
            is_pinned=False,
        )
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/course-load?mode=normal",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["course_max_minutes"] == 480
    assert body["max_patients_per_course"] >= 1
    cells = body["cells"]
    assert len(cells) == 1  # 他患者 (佐藤) の月曜 A のみ。自身の 14:00 は含めない
    cell = cells[0]
    assert cell["weekday"] == 0
    assert cell["course_template_id"] == str(seeded["tpl"].id)
    assert cell["used_minutes"] == 35
    assert cell["patient_count"] == 1


@pytest.mark.asyncio
async def test_course_load_unknown_patient_404(client, db) -> None:
    admin = await _make_admin(db)
    res = await client.get(
        f"/api/v1/patients/{uuid.uuid4()}/fixed-visits/course-load",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# V8: 二段検査 — 型 vs 今週の実配置 (PO確定 2026-08-09)
# ---------------------------------------------------------------------------

from datetime import date  # noqa: E402

WEEK_MONDAY = date(2026, 5, 11)  # 2026-W20 月曜


@pytest.mark.asyncio
async def test_week_conflict_two_tier(db, client) -> None:
    """型同士 (V3) は空いていても、今週の実配置 (カイポケ取込等) と重なるなら
    【今週のみ】の week_conflict が別段で出る。来週 (別週) の visit では出ない。"""
    seeded = await _seed(db)
    iso = WEEK_MONDAY.isocalendar()
    course = Course(
        iso_year=iso.year,
        iso_week=iso.week,
        weekday=1,  # 火曜
        code="A",
        course_status="staff_assigned",
        template_id=seeded["tpl"].id,
        office_id=seeded["office"].id,
    )
    db.add(course)
    await db.flush()
    # 他患者の「今週の実配置」: 火曜 10:00-10:35 (型には存在しない = カイポケ取込を模す)
    db.add(
        Visit(
            patient_id=seeded["other"].id,
            visit_date=WEEK_MONDAY.replace(day=WEEK_MONDAY.day + 1),
            start_time=time(10, 0),
            end_time=time(10, 35),
            type="regular",
            status="planned",
            source="import",
            required_staff_count=1,
            course_id=course.id,
        )
    )
    await db.commit()

    config = await load_scheduling_config(db)
    items_schema = __import__(
        "app.schemas.v2.patient_fixed_visit", fromlist=["PatientFixedVisitV2Base"]
    ).PatientFixedVisitV2Base
    items = [
        items_schema(
            weekday=1,
            start_time=time(10, 0),
            duration_min=35,
            course_template_id=seeded["tpl"].id,
            slot_index=0,
        )
    ]

    # 今週 (WEEK_MONDAY の週) を today に注入 → week_conflict が出る
    result = await validate_pfv_changes(
        db, seeded["target"].id, items, "normal", config=config, today=WEEK_MONDAY
    )
    codes = [w.code for w in result.warnings]
    assert "week_conflict" in codes
    msg = next(w.message for w in result.warnings if w.code == "week_conflict")
    assert "【今週のみ】" in msg
    assert "佐藤 花子" in msg
    # 型同士 (V3) では衝突していない (他患者の型は月曜のみ)
    assert codes.count("patient_time_conflict") == 0

    # 別の週を today にすると week_conflict は出ない (恒久検査 V3 のみが残る)
    result2 = await validate_pfv_changes(
        db,
        seeded["target"].id,
        items,
        "normal",
        config=config,
        today=date(2026, 6, 1),
    )
    assert "week_conflict" not in [w.code for w in result2.warnings]


# ---------------------------------------------------------------------------
# V9: コース担当 × NG スタッフ / 性別制限 (NG スタッフ設計 §6 Phase 3)
# ---------------------------------------------------------------------------


async def _seed_course_with_staff(
    db,
    seeded: dict,
    *,
    staff_name: str,
    staff_sex: str | None,
    weekday: int = 1,
) -> Staff:
    """今週 (WEEK_MONDAY の週) の当該曜日コースに担当スタッフを立てる。"""
    staff = Staff(name=staff_name, sex=staff_sex, status="active")
    db.add(staff)
    await db.flush()
    iso = WEEK_MONDAY.isocalendar()
    db.add(
        Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=weekday,
            code="A",
            course_status="staff_assigned",
            template_id=seeded["tpl"].id,
            office_id=seeded["office"].id,
            assigned_staff_id=staff.id,
        )
    )
    await db.commit()
    await db.refresh(staff)
    return staff


def _v9_items(seeded: dict, weekday: int = 1) -> list:
    return [
        PatientFixedVisitV2Base(
            weekday=weekday,
            start_time=time(14, 0),
            duration_min=35,
            course_template_id=seeded["tpl"].id,
            slot_index=0,
        )
    ]


@pytest.mark.asyncio
async def test_v9_course_staff_ng_warns(db) -> None:
    """コース担当が当該患者の NG スタッフ → course_staff_ng 警告 (保存はブロックしない)。"""
    seeded = await _seed(db)
    staff = await _seed_course_with_staff(db, seeded, staff_name="鈴木 一郎", staff_sex="male")
    db.add(PatientNgStaff(patient_id=seeded["target"].id, staff_id=staff.id, note="相性"))
    await db.commit()

    config = await load_scheduling_config(db)
    result = await validate_pfv_changes(
        db, seeded["target"].id, _v9_items(seeded), "normal", config=config, today=WEEK_MONDAY
    )
    ng = [w for w in result.warnings if w.code == "course_staff_ng"]
    assert len(ng) == 1
    assert "鈴木 一郎" in ng[0].message
    assert "コースA" in ng[0].message
    assert ng[0].severity == "warning"
    assert result.has_errors is False


@pytest.mark.asyncio
async def test_v9_no_warning_when_not_ng(db) -> None:
    """NG 行が無ければ従来どおり (V9 警告なし)。"""
    seeded = await _seed(db)
    await _seed_course_with_staff(db, seeded, staff_name="鈴木 一郎", staff_sex="male")

    config = await load_scheduling_config(db)
    result = await validate_pfv_changes(
        db, seeded["target"].id, _v9_items(seeded), "normal", config=config, today=WEEK_MONDAY
    )
    codes = {w.code for w in result.warnings}
    assert "course_staff_ng" not in codes
    assert "course_staff_sex_mismatch" not in codes


@pytest.mark.asyncio
async def test_v9_sex_restriction_mismatch_warns(db) -> None:
    """女性のみの患者 × 男性のコース担当 → course_staff_sex_mismatch 警告。"""
    seeded = await _seed(db)
    seeded["target"].sex_restriction = "female_only"
    await _seed_course_with_staff(db, seeded, staff_name="鈴木 一郎", staff_sex="male")

    config = await load_scheduling_config(db)
    result = await validate_pfv_changes(
        db, seeded["target"].id, _v9_items(seeded), "normal", config=config, today=WEEK_MONDAY
    )
    sex = [w for w in result.warnings if w.code == "course_staff_sex_mismatch"]
    assert len(sex) == 1
    assert "女性のみ" in sex[0].message
    assert "鈴木 一郎" in sex[0].message


@pytest.mark.asyncio
async def test_v9_unknown_staff_sex_is_not_flagged(db) -> None:
    """staff.sex 未設定は OK 側に倒す (提案系と同じ誤検知回避)。"""
    seeded = await _seed(db)
    seeded["target"].sex_restriction = "female_only"
    await _seed_course_with_staff(db, seeded, staff_name="性別未設定", staff_sex=None)

    config = await load_scheduling_config(db)
    result = await validate_pfv_changes(
        db, seeded["target"].id, _v9_items(seeded), "normal", config=config, today=WEEK_MONDAY
    )
    assert "course_staff_sex_mismatch" not in {w.code for w in result.warnings}


@pytest.mark.asyncio
async def test_v9_unassigned_course_no_warning(db) -> None:
    """コース担当が未割当 (assigned_staff_id=None) なら警告を出さない。"""
    seeded = await _seed(db)
    seeded["target"].sex_restriction = "female_only"
    iso = WEEK_MONDAY.isocalendar()
    db.add(
        Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=1,
            code="A",
            course_status="course_fixed",
            template_id=seeded["tpl"].id,
            office_id=seeded["office"].id,
        )
    )
    await db.commit()

    config = await load_scheduling_config(db)
    result = await validate_pfv_changes(
        db, seeded["target"].id, _v9_items(seeded), "normal", config=config, today=WEEK_MONDAY
    )
    codes = {w.code for w in result.warnings}
    assert "course_staff_ng" not in codes
    assert "course_staff_sex_mismatch" not in codes


@pytest.mark.asyncio
async def test_v9_surfaces_in_validate_endpoint(db, client) -> None:
    """FE のライブ検査 (POST /validate) の warnings に既存警告と同じ形で載る。"""
    seeded = await _seed(db)
    admin = await _make_admin(db)
    today = date.today()
    iso = today.isocalendar()
    staff = Staff(name="鈴木 一郎", sex="male", status="active")
    db.add(staff)
    await db.flush()
    db.add(
        Course(
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=1,
            code="A",
            course_status="staff_assigned",
            template_id=seeded["tpl"].id,
            office_id=seeded["office"].id,
            assigned_staff_id=staff.id,
        )
    )
    db.add(PatientNgStaff(patient_id=seeded["target"].id, staff_id=staff.id))
    await db.commit()

    res = await client.post(
        f"/api/v1/patients/{seeded['target'].id}/fixed-visits/validate",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [
                {
                    "weekday": 1,
                    "start_time": "14:00",
                    "duration_min": 35,
                    "course_template_id": str(seeded["tpl"].id),
                    "slot_index": 0,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    ng = [w for w in res.json()["warnings"] if w["code"] == "course_staff_ng"]
    assert len(ng) == 1
    assert ng[0]["severity"] == "warning"
    assert ng[0]["weekday"] == 1
