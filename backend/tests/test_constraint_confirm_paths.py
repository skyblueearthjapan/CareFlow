"""「患者を動かす」経路の制約確認フロー (NG スタッフ / 性別制限) tests.

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §7 (PO 決定2)。

背景 (PO 実機テスト 2026-08-11): 既存の確認フロー (``PATCH /courses/{id}``・
``test_constraint_override.py``) は「コース担当を変える」方向だけを見ており、
**患者を NG スタッフのコースへ動かす**方向は全経路ノーチェックだった。

検査観点 (各経路で 422 → acknowledge → 200 + 通知 の 3 点セット):
  M1  visit-move-week-only: NG 違反 → 422 (detail 形状) / visit は動いていない
  M2  visit-move-week-only: acknowledge → 200 + 通知 + 担当付け替え
  M3  visit-move-week-only: 移動先コース未作成 → 検査スキップ (200・通知なし)
  M4  visit-move-week-only: 移動先コースの担当未割当 → 検査スキップ
  M5  visit-move-week-only 副次バグ回帰: コース跨ぎ移動で primary_staff_id / VSA
      が移動先コースの担当へ付け替わる (2 名体制の相方 VSA を巻き添えにしない)
  P1  place-and-fix: NG 違反 → 422 / visit も PFV も作られない
  P2  place-and-fix: acknowledge → 200 + 通知 (op_group_id で冪等)
  P3  place-and-fix: 違反なし → 素通り・通知なし
  S1  special-visit place: 性別違反 → 422 → acknowledge → 200 + 通知
  F1  PUT fixed-visits pattern_and_week: NG 違反 → 422 / PFV は無傷
  F2  PUT fixed-visits pattern_and_week: acknowledge → 200 + 通知
  F3  PUT fixed-visits pattern_only: 週が特定できないためスキップ
  A1  apply-individual pattern_and_week: NG 違反 → 422 → acknowledge → 200 + 通知
  A2  apply-individual pattern_only: スキップ

Backend で APP_ENV=test ガード済み (conftest.py). 本番 DB 禁止 (ローカル SQLite).
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.notification import Notification
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.special_visit import SpecialVisitMark, SpecialVisitPeriod
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

# ISO 2026 W20 = 2026-05-11 (Mon) .. 2026-05-17 (Sun)
ISO_YEAR = 2026
ISO_WEEK = 20
MON = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

_MOVE_URL = "/api/v1/schedule/v2/visit-move-week-only"
_PLACE_FIX_URL = "/api/v1/schedule/place-and-fix"
_INDIVIDUAL_URL = "/api/v1/schedule/v2/apply-individual"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed(
    db,
    *,
    prefix: str,
    staff_sex: str | None = "female",
    sex_restriction: str | None = None,
    ng: bool = False,
    ng_note: str | None = None,
    weekday: int = 1,
    code: str = "A",
    assign_staff: bool = True,
    make_course: bool = True,
) -> dict:
    """1 拠点 + 1 スタッフ + 1 患者 + 1 コーステンプレート (+ 当該週 Course) を seed する.

    Course は「移動先 / 配置先」= 既に担当が居るコースを表す。
    ``make_course=False`` で「その週にコース実体が無い」ケース、
    ``assign_staff=False`` で「担当未割当」ケースを作る。
    """
    office = Office(name=f"{prefix} 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code=f"{prefix}-S",
        name=f"{prefix} スタッフ",
        role="staff",
        status="active",
        sex=staff_sex,
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()

    patient = Patient(
        code=f"{prefix}-P",
        name=f"{prefix} 患者",
        status="active",
        sex_restriction=sex_restriction,
        special_week_active=[],
        lat=35.65,
        lng=140.1,
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()

    template = CourseTemplate(
        office_id=office.id,
        label=code,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
    )
    db.add(template)
    await db.flush()

    course_id = None
    if make_course:
        course = Course(
            iso_year=ISO_YEAR,
            iso_week=ISO_WEEK,
            weekday=weekday,
            code=code,
            course_status=COURSE_STATUS_STAFF_ASSIGNED,
            assigned_staff_id=staff.id if assign_staff else None,
            office_id=office.id,
            template_id=template.id,
        )
        db.add(course)
        await db.flush()
        course_id = course.id

    if ng:
        db.add(PatientNgStaff(patient_id=patient.id, staff_id=staff.id, note=ng_note))
    await db.commit()

    return {
        "office_id": office.id,
        "staff_id": staff.id,
        "staff_name": staff.name,
        "patient_id": patient.id,
        "patient_name": patient.name,
        "template_id": template.id,
        "course_id": course_id,
        "weekday": weekday,
        "code": code,
    }


async def _make_visit(
    db, *, patient_id, weekday: int, start: time, end: time, course_id=None, staff_id=None
) -> Visit:
    v = Visit(
        patient_id=patient_id,
        visit_date=MON + timedelta(days=weekday),
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course_id,
        primary_staff_id=staff_id,
    )
    db.add(v)
    await db.flush()
    if staff_id is not None:
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=staff_id))
    await db.commit()
    await db.refresh(v)
    return v


async def _refresh_session(db) -> None:
    """client 側 session の commit を確実に見るため TX を切り直し identity map を捨てる.

    ``rollback()`` だけでは、fixture session が既にロード済みの ORM オブジェクト
    (``_make_visit`` の戻り値等) が古い値のまま返ることがあるため
    ``expire_all()`` も必ず併用する。
    """
    await db.rollback()
    db.expire_all()


async def _notifications(db) -> list[Notification]:
    await _refresh_session(db)
    return list(
        (
            await db.scalars(select(Notification).where(Notification.type == "constraint_override"))
        ).all()
    )


async def _active_visits(db, patient_id) -> list[Visit]:
    await _refresh_session(db)
    return list(
        (
            await db.scalars(
                select(Visit).where(Visit.patient_id == patient_id, Visit.deleted_at.is_(None))
            )
        ).all()
    )


async def _vsa_staff_ids(db, visit_id) -> set:
    await _refresh_session(db)
    return set(
        (
            await db.scalars(
                select(VisitStaffAssignment.staff_id).where(
                    VisitStaffAssignment.visit_id == visit_id
                )
            )
        ).all()
    )


def _assert_detail_shape(detail: dict, seed: dict, kind: str, note: str | None = None) -> None:
    """422 detail が既存経路 (PATCH /courses) と **完全同形**であることを検証する."""
    assert detail["code"] == "constraint_confirmation_required"
    assert len(detail["warnings"]) == 1
    w = detail["warnings"][0]
    assert set(w) == {"kind", "patient_id", "patient_name", "staff_id", "staff_name", "note"}
    assert w["kind"] == kind
    assert w["patient_id"] == str(seed["patient_id"])
    assert w["patient_name"] == seed["patient_name"]
    assert w["staff_id"] == str(seed["staff_id"])
    assert w["staff_name"] == seed["staff_name"]
    assert w["note"] == note


def _move_body(seed: dict, **overrides) -> dict:
    body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "patient_id": str(seed["patient_id"]),
        "old_weekday": 0,
        "old_start_time": "10:00:00",
        "new_weekday": seed["weekday"],
        "new_start_time": "11:00:00",
        "new_course_template_id": str(seed["template_id"]),
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# M1 / M2: visit-move-week-only (PO 実機テストで見つかった経路)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m1_move_to_ng_staff_course_requires_confirmation(client, db) -> None:
    admin = await _make_user(db, "cc-move-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-MV1", ng=True, ng_note="相性不良")
    await _make_visit(
        db, patient_id=seed["patient_id"], weekday=0, start=time(10, 0), end=time(10, 30)
    )

    res = await client.post(_MOVE_URL, headers=headers, json=_move_body(seed))
    assert res.status_code == 422, res.text
    _assert_detail_shape(res.json()["detail"], seed, "ng_staff", note="相性不良")

    # visit は動いていない (月曜 10:00 のまま) + 通知も無い。
    visits = await _active_visits(db, seed["patient_id"])
    assert len(visits) == 1
    assert visits[0].visit_date == MON and visits[0].start_time == time(10, 0)
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_m2_move_acknowledge_applies_and_notifies(client, db) -> None:
    admin = await _make_user(db, "cc-move-2@example.com")
    headers = _bearer(admin)
    admin_id = admin.id
    seed = await _seed(db, prefix="CC-MV2", ng=True, ng_note="要注意")
    await _make_visit(
        db, patient_id=seed["patient_id"], weekday=0, start=time(10, 0), end=time(10, 30)
    )

    res = await client.post(
        _MOVE_URL,
        headers=headers,
        json=_move_body(seed, acknowledge_constraint_warnings=True),
    )
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1

    visits = await _active_visits(db, seed["patient_id"])
    assert visits[0].visit_date == MON + timedelta(days=seed["weekday"])
    assert visits[0].course_id == seed["course_id"]
    # 副次バグ修正: コース跨ぎ移動で担当も移動先コースのものへ付け替わる。
    assert visits[0].primary_staff_id == seed["staff_id"]

    rows = await _notifications(db)
    assert len(rows) == 1
    assert rows[0].user_id == admin_id
    assert "NGスタッフ割当を承認" in rows[0].title
    assert "メモ: 要注意" in (rows[0].body or "")


@pytest.mark.asyncio
async def test_m3_move_to_uncreated_course_skips_check(client, db) -> None:
    """移動先コースがその週に未作成 = 担当も居ない → 検査スキップ (fail-open)."""
    admin = await _make_user(db, "cc-move-3@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-MV3", ng=True, make_course=False)
    await _make_visit(
        db, patient_id=seed["patient_id"], weekday=0, start=time(10, 0), end=time(10, 30)
    )

    res = await client.post(_MOVE_URL, headers=headers, json=_move_body(seed))
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_m4_move_to_unassigned_course_skips_check(client, db) -> None:
    """移動先コースはあるが担当未割当 → 確認すべき相手が居ないので素通し."""
    admin = await _make_user(db, "cc-move-4@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-MV4", ng=True, assign_staff=False)
    await _make_visit(
        db, patient_id=seed["patient_id"], weekday=0, start=time(10, 0), end=time(10, 30)
    )

    res = await client.post(_MOVE_URL, headers=headers, json=_move_body(seed))
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1

    # 担当未割当コースへ移ったので primary も None に揃う。
    visits = await _active_visits(db, seed["patient_id"])
    assert visits[0].primary_staff_id is None
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_m5_move_repoints_primary_and_vsa(client, db) -> None:
    """副次バグ回帰: コース跨ぎ移動で primary_staff_id と VSA が付け替わる.

    旧実装は course_id だけ差し替えて担当を放置していたため、移動後も旧コースの
    担当が visit に残っていた (訪問モニター / 子アプリの可視性が実態とズレる)。
    2 名体制の相方 (別スタッフの VSA 行) は巻き添えで消さないことも同時に検証する。
    """
    admin = await _make_user(db, "cc-move-5@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-MV5", ng=False, weekday=2)

    # 旧コース (月曜) + その担当。visit は旧担当を持つ。
    old_staff = Staff(code="CC-MV5-OLD", name="旧担当", role="staff", status="active", sex="female")
    db.add(old_staff)
    await db.flush()
    old_course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="B",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=old_staff.id,
        office_id=seed["office_id"],
    )
    db.add(old_course)
    await db.flush()
    old_staff_id, old_course_id = old_staff.id, old_course.id
    await db.commit()

    visit = await _make_visit(
        db,
        patient_id=seed["patient_id"],
        weekday=0,
        start=time(10, 0),
        end=time(10, 30),
        course_id=old_course_id,
        staff_id=old_staff_id,
    )
    visit_id = visit.id
    # 2 名体制の相方に相当する第 3 のスタッフ VSA (巻き添え削除の検出用)。
    partner = Staff(code="CC-MV5-PT", name="相方", role="staff", status="active", sex="female")
    db.add(partner)
    await db.flush()
    partner_id = partner.id
    db.add(VisitStaffAssignment(visit_id=visit_id, staff_id=partner_id))
    await db.commit()

    res = await client.post(_MOVE_URL, headers=headers, json=_move_body(seed))
    assert res.status_code == 200, res.text
    assert res.json()["visits_moved"] == 1

    visits = await _active_visits(db, seed["patient_id"])
    assert len(visits) == 1
    assert visits[0].course_id == seed["course_id"]
    assert visits[0].primary_staff_id == seed["staff_id"]

    # VSA: 旧担当は外れ、新担当が入り、相方は残る。
    assert await _vsa_staff_ids(db, visit_id) == {seed["staff_id"], partner_id}


@pytest.mark.asyncio
async def test_m5b_move_within_same_course_keeps_staff(client, db) -> None:
    """同一コース内の時刻移動は担当が変わらない = 検査も付け替えもしない."""
    admin = await _make_user(db, "cc-move-5b@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-MV5B", ng=True, weekday=0)
    visit = await _make_visit(
        db,
        patient_id=seed["patient_id"],
        weekday=0,
        start=time(10, 0),
        end=time(10, 30),
        course_id=seed["course_id"],
        staff_id=seed["staff_id"],
    )
    visit_id = visit.id

    res = await client.post(
        _MOVE_URL,
        headers=headers,
        json=_move_body(seed, new_weekday=0, new_start_time="13:00:00"),
    )
    # NG 患者だが「既に同じコースに載っている」ため確認は出ない (担当が変わらない)。
    assert res.status_code == 200, res.text
    visits = await _active_visits(db, seed["patient_id"])
    assert visits[0].start_time == time(13, 0)
    assert visits[0].primary_staff_id == seed["staff_id"]
    assert await _vsa_staff_ids(db, visit_id) == {seed["staff_id"]}
    assert await _notifications(db) == []


# ---------------------------------------------------------------------------
# P1 / P2 / P3: place-and-fix (ドロップ即固定枠化)
# ---------------------------------------------------------------------------


def _place_body(seed: dict, **overrides) -> dict:
    body = {
        "patient_id": str(seed["patient_id"]),
        "course_template_id": str(seed["template_id"]),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "weekday": seed["weekday"],
        "start_time": "09:00:00",
        "duration_min": 30,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_p1_place_and_fix_ng_requires_confirmation(client, db) -> None:
    admin = await _make_user(db, "cc-paf-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-PAF1", ng=True, ng_note="配置NG")

    res = await client.post(_PLACE_FIX_URL, headers=headers, json=_place_body(seed))
    assert res.status_code == 422, res.text
    _assert_detail_shape(res.json()["detail"], seed, "ng_staff", note="配置NG")

    # visit も PFV も作られていない (422 は書き込み前に返る)。
    assert await _active_visits(db, seed["patient_id"]) == []
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == seed["patient_id"])
        )
    ).all()
    assert list(rows) == []
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_p2_place_and_fix_acknowledge_notifies(client, db) -> None:
    admin = await _make_user(db, "cc-paf-2@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-PAF2", ng=True, ng_note="承知の上")
    op_group_id = "bbbbbbbb-2222-4222-8222-222222222222"

    res = await client.post(
        _PLACE_FIX_URL,
        headers=headers,
        json=_place_body(seed, acknowledge_constraint_warnings=True, op_group_id=op_group_id),
    )
    assert res.status_code == 200, res.text

    rows = await _notifications(db)
    assert len(rows) == 1
    assert str(rows[0].reference_id) == op_group_id
    assert "NGスタッフ割当を承認" in rows[0].title
    assert "メモ: 承知の上" in (rows[0].body or "")

    # 実際に visit が作られている。
    assert len(await _active_visits(db, seed["patient_id"])) == 1


@pytest.mark.asyncio
async def test_p3_place_and_fix_clean_passes_without_notification(client, db) -> None:
    """違反なし = 従来と完全同一挙動 (後方互換)."""
    admin = await _make_user(db, "cc-paf-3@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-PAF3", ng=False)

    res = await client.post(_PLACE_FIX_URL, headers=headers, json=_place_body(seed))
    assert res.status_code == 200, res.text
    assert len(await _active_visits(db, seed["patient_id"])) == 1
    assert await _notifications(db) == []


# ---------------------------------------------------------------------------
# S1: 特別訪問週間の配置 (性別制限ケース)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s1_special_visit_place_gender_violation(client, db) -> None:
    """性別違反 (female_only 患者 × 男性担当) → 422 → acknowledge → 200 + 通知."""
    admin = await _make_user(db, "cc-svw-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(
        db, prefix="CC-SVW1", staff_sex="male", sex_restriction="female_only", weekday=2
    )

    period = SpecialVisitPeriod(
        patient_id=seed["patient_id"],
        start_date=MON,
        end_date=MON + timedelta(days=13),
        weekly_target=3,
        status="active",
    )
    db.add(period)
    await db.flush()
    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=seed["patient_id"],
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=2,
        kind="extra",
        status="pool",
    )
    db.add(mark)
    await db.commit()
    mark_id = mark.id

    url = f"/api/v1/special-visit-marks/{mark_id}/place"
    res = await client.post(
        url,
        headers=headers,
        json={"course_id": str(seed["course_id"]), "start_time": "14:00"},
    )
    assert res.status_code == 422, res.text
    _assert_detail_shape(res.json()["detail"], seed, "gender", note=None)
    assert await _active_visits(db, seed["patient_id"]) == []

    res = await client.post(
        url,
        headers=headers,
        json={
            "course_id": str(seed["course_id"]),
            "start_time": "14:00",
            "acknowledge_constraint_warnings": True,
        },
    )
    assert res.status_code == 200, res.text

    visits = await _active_visits(db, seed["patient_id"])
    assert len(visits) == 1
    assert visits[0].primary_staff_id == seed["staff_id"]

    rows = await _notifications(db)
    assert len(rows) == 1
    assert "性別制限外の割当を承認" in rows[0].title


# ---------------------------------------------------------------------------
# F1 / F2 / F3: PUT /patients/{id}/fixed-visits (提案採用「固定訪問週間に登録」)
# ---------------------------------------------------------------------------


def _fv_body(seed: dict, **overrides) -> dict:
    body = {
        "mode": "normal",
        "items": [
            {
                "weekday": seed["weekday"],
                "start_time": "09:00:00",
                "duration_min": 30,
                "slot_index": 0,
                "course_template_id": str(seed["template_id"]),
                "movability": "day_flexible",
            }
        ],
        "change_scope": "pattern_and_week",
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_f1_put_fixed_visits_ng_requires_confirmation(client, db) -> None:
    admin = await _make_user(db, "cc-fv-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-FV1", ng=True, ng_note="型登録NG")

    res = await client.put(
        f"/api/v1/patients/{seed['patient_id']}/fixed-visits",
        headers=headers,
        json=_fv_body(seed),
    )
    assert res.status_code == 422, res.text
    _assert_detail_shape(res.json()["detail"], seed, "ng_staff", note="型登録NG")

    # 破壊的な DELETE→INSERT の前に弾いているので PFV は無傷 (0 件のまま)。
    await _refresh_session(db)
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == seed["patient_id"])
        )
    ).all()
    assert list(rows) == []
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_f2_put_fixed_visits_acknowledge_notifies(client, db) -> None:
    admin = await _make_user(db, "cc-fv-2@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-FV2", ng=True, ng_note="承認して登録")

    res = await client.put(
        f"/api/v1/patients/{seed['patient_id']}/fixed-visits",
        headers=headers,
        json=_fv_body(seed, acknowledge_constraint_warnings=True),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1

    rows = await _notifications(db)
    assert len(rows) == 1
    assert "NGスタッフ割当を承認" in rows[0].title
    assert "メモ: 承認して登録" in (rows[0].body or "")


@pytest.mark.asyncio
async def test_f3_put_fixed_visits_pattern_only_skips_check(client, db) -> None:
    """pattern_only は週が特定できない = 配置先コースが決まらないためスキップ (後方互換)."""
    admin = await _make_user(db, "cc-fv-3@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-FV3", ng=True)

    res = await client.put(
        f"/api/v1/patients/{seed['patient_id']}/fixed-visits",
        headers=headers,
        json=_fv_body(seed, change_scope="pattern_only", iso_year=None, iso_week=None),
    )
    assert res.status_code == 200, res.text
    assert await _notifications(db) == []


# ---------------------------------------------------------------------------
# A1 / A2: apply-individual (提案採用)
# ---------------------------------------------------------------------------


def _individual_body(seed: dict, **overrides) -> dict:
    body = {
        "patient_id": str(seed["patient_id"]),
        "confirm": True,
        "visit_plans": [
            {
                "weekday": seed["weekday"],
                "start_time": "10:00",
                "end_time": "10:30",
                "duration_min": 30,
                "course_code": seed["code"],
                "office_id": str(seed["office_id"]),
                "am_pm": "am",
            }
        ],
        "change_scope": "pattern_and_week",
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_a1_apply_individual_ng_requires_confirmation_then_notifies(client, db) -> None:
    admin = await _make_user(db, "cc-ai-1@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-AI1", ng=True, ng_note="採用NG")

    res = await client.post(_INDIVIDUAL_URL, headers=headers, json=_individual_body(seed))
    assert res.status_code == 422, res.text
    _assert_detail_shape(res.json()["detail"], seed, "ng_staff", note="採用NG")

    await _refresh_session(db)
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == seed["patient_id"])
        )
    ).all()
    assert list(rows) == []
    assert await _notifications(db) == []

    res = await client.post(
        _INDIVIDUAL_URL,
        headers=headers,
        json=_individual_body(seed, acknowledge_constraint_warnings=True),
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    notes = await _notifications(db)
    assert len(notes) == 1
    assert "NGスタッフ割当を承認" in notes[0].title
    assert "メモ: 採用NG" in (notes[0].body or "")


@pytest.mark.asyncio
async def test_a2_apply_individual_pattern_only_skips_check(client, db) -> None:
    admin = await _make_user(db, "cc-ai-2@example.com")
    headers = _bearer(admin)
    seed = await _seed(db, prefix="CC-AI2", ng=True)

    res = await client.post(
        _INDIVIDUAL_URL,
        headers=headers,
        json=_individual_body(seed, change_scope="pattern_only", iso_year=None, iso_week=None),
    )
    assert res.status_code == 200, res.text
    assert await _notifications(db) == []
