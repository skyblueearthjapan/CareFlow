"""「🛌 休みにする」 — POST /api/v1/schedule/v2/staff-off-week (PO 決定 2026-08-23).

これまでは FE が ①休みの登録 → ②訪問ごとの付替 の 2 段階で呼んでいたため、
①が op_log の対象外で「戻る」を押すと②だけ戻り **休みだけ残る** 半端な状態に
なっていた。新 API は ①②③ (休み / 訪問 / コース) を 1 トランザクション +
**同一 op_group_id** で書く = 「戻る」1 回で全部戻る。

検証観点:
  1. 担当なしへ戻す: override 作成 + primary NULL / manual False / VSA 空 + コース担当 NULL
  2. 別スタッフへ引き受け: primary / manual / VSA / コース担当が引き受け先に
  3. undo: 訪問・コースだけでなく **休み (override) も消える**
  4. redo: もう一度休みになる
  5. 青ピン 422 (件数と患者名)
  6. 過去日 (JST) 422
  7. 冪等: 同じ日を 2 回押しても override は 1 件 (流用)
  8. 引き受け先が 新人 / 退職 / 本人 / その日休み なら 422
  9. 2 名体制: 休む人の枠だけ差し替え、相方の VSA は残す (undo も相方を壊さない)
 10. planned 以外 (打刻済み・完了・取消済み) は据え置き = skipped_visit_ids
 11. NG スタッフ: 422 constraint_confirmation_required → acknowledge 再送で通る
 12. 元が 時間変更 / 午前休 / 午後休 の日でも、undo でその型・時刻・理由ごと戻る
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffWeeklyOverride
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

_URL = "/api/v1/schedule/v2/staff-off-week"
_UNDO_URL = "/api/v1/schedule/v2/op-log/undo"
_REDO_URL = "/api/v1/schedule/v2/op-log/redo"


def _today_jst() -> date:
    """エンドポイントの過去日ガードと **同じ基準** (JST) の今日."""
    return datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()


def _target_date() -> date:
    """常に未来日 (過去日ガードに掛からない日) を返す."""
    return _today_jst() + timedelta(days=7)


async def _make_user(db, *, email: str, role: str = "admin", staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str = "テスト拠点") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_staff(db, *, name: str, is_trainee: bool = False, status: str = "active") -> Staff:
    s = Staff(name=name, is_trainee=is_trainee, status=status)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_patient(db, *, code: str, name: str = "テスト患者") -> Patient:
    p = Patient(code=code, name=name, status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(db, *, office_id: UUID, target: date, staff: Staff, code: str = "A"):
    iso = target.isocalendar()
    c = Course(
        iso_year=iso.year,
        iso_week=iso.week,
        weekday=target.weekday(),
        code=code,
        office_id=office_id,
        course_status="staff_assigned",
        assigned_staff_id=staff.id,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    *,
    patient: Patient,
    target: date,
    staff: Staff | None,
    course: Course | None = None,
    week_pinned: bool = False,
    start_h: int = 10,
    status_value: str = "planned",
    members: list[Staff] | None = None,
) -> Visit:
    """訪問 1 件。``members`` を渡すとその面々で VSA を作る (2 名体制の再現)."""
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id if staff is not None else None,
        course_id=course.id if course is not None else None,
        visit_date=target,
        start_time=time(start_h, 0),
        end_time=time(start_h + 1, 0),
        type="regular",
        status=status_value,
        source="auto",
        week_pinned=week_pinned,
    )
    db.add(v)
    await db.flush()
    assigned = members if members is not None else ([staff] if staff is not None else [])
    for member in assigned:
        db.add(VisitStaffAssignment(visit_id=v.id, staff_id=member.id))
    await db.commit()
    await db.refresh(v)
    return v


async def _vsa_staff_ids(db, visit_id) -> set:
    rows = await db.scalars(
        select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == visit_id)
    )
    return set(rows.all())


async def _overrides(db, staff_id) -> list[StaffWeeklyOverride]:
    rows = await db.scalars(
        select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff_id)
    )
    return list(rows.all())


# ---------------------------------------------------------------------------
# 1. 担当なしへ戻す
# ---------------------------------------------------------------------------


async def test_off_to_unassigned(client, db) -> None:
    admin = await _make_user(db, email="sow-1@example.com")
    office = await _make_office(db)
    staff_a = await _make_staff(db, name="休む人")
    patient = await _make_patient(db, code="SOW-1")
    target = _target_date()
    course = await _make_course(db, office_id=office.id, target=target, staff=staff_a)
    visit = await _make_visit(db, patient=patient, target=target, staff=staff_a, course=course)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"staff_id": str(staff_a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved_visit_ids"] == [str(visit.id)]
    assert body["moved_course_ids"] == [str(course.id)]
    assert body["to_staff_id"] is None

    rows = await _overrides(db, staff_a.id)
    assert len(rows) == 1
    assert rows[0].override_type == "off"
    assert str(rows[0].id) == body["override_id"]

    await db.refresh(visit)
    await db.refresh(course)
    assert visit.primary_staff_id is None
    assert visit.manual_staff_override is False
    assert await _vsa_staff_ids(db, visit.id) == set()
    assert course.assigned_staff_id is None


# ---------------------------------------------------------------------------
# 2. 別スタッフが引き受ける
# ---------------------------------------------------------------------------


async def test_off_to_other_staff(client, db) -> None:
    admin = await _make_user(db, email="sow-2@example.com")
    office = await _make_office(db)
    staff_a = await _make_staff(db, name="休む人2")
    staff_b = await _make_staff(db, name="引き受ける人2")
    patient = await _make_patient(db, code="SOW-2")
    target = _target_date()
    course = await _make_course(db, office_id=office.id, target=target, staff=staff_a)
    # primary NULL + コース担当が休む人 = 盤面上は休む人の行に並ぶ訪問。
    visit = await _make_visit(db, patient=patient, target=target, staff=None, course=course)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["moved_visit_ids"] == [str(visit.id)]

    await db.refresh(visit)
    await db.refresh(course)
    assert visit.primary_staff_id == staff_b.id
    assert visit.manual_staff_override is True
    assert await _vsa_staff_ids(db, visit.id) == {staff_b.id}
    assert course.assigned_staff_id == staff_b.id


# ---------------------------------------------------------------------------
# 3-4. undo / redo
# ---------------------------------------------------------------------------


async def test_undo_restores_visits_courses_and_removes_override(client, db) -> None:
    """「戻る」1 回で 訪問・コースだけでなく **休みも** 消える (今回の主眼)."""
    admin = await _make_user(db, email="sow-3@example.com")
    office = await _make_office(db)
    staff_a = await _make_staff(db, name="休む人3")
    staff_b = await _make_staff(db, name="引き受ける人3")
    patient = await _make_patient(db, code="SOW-3")
    target = _target_date()
    iso = target.isocalendar()
    course = await _make_course(db, office_id=office.id, target=target, staff=staff_a)
    visit = await _make_visit(db, patient=patient, target=target, staff=staff_a, course=course)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text

    undo = await client.post(
        _UNDO_URL,
        headers=_bearer(admin),
        json={"iso_year": iso.year, "iso_week": iso.week},
    )
    assert undo.status_code == 200, undo.text

    await db.refresh(visit)
    await db.refresh(course)
    assert visit.primary_staff_id == staff_a.id
    assert await _vsa_staff_ids(db, visit.id) == {staff_a.id}
    assert course.assigned_staff_id == staff_a.id
    # 休みも一緒に消える (半端な「休みだけ残る」状態を作らない)。
    assert await _overrides(db, staff_a.id) == []


async def test_redo_reapplies_off(client, db) -> None:
    admin = await _make_user(db, email="sow-4@example.com")
    office = await _make_office(db)
    staff_a = await _make_staff(db, name="休む人4")
    staff_b = await _make_staff(db, name="引き受ける人4")
    patient = await _make_patient(db, code="SOW-4")
    target = _target_date()
    iso = target.isocalendar()
    course = await _make_course(db, office_id=office.id, target=target, staff=staff_a)
    visit = await _make_visit(db, patient=patient, target=target, staff=staff_a, course=course)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text
    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    redo = await client.post(
        _REDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert redo.status_code == 200, redo.text

    await db.refresh(visit)
    await db.refresh(course)
    assert visit.primary_staff_id == staff_b.id
    assert course.assigned_staff_id == staff_b.id
    rows = await _overrides(db, staff_a.id)
    assert len(rows) == 1
    assert rows[0].override_type == "off"


# ---------------------------------------------------------------------------
# 5-6. ガード
# ---------------------------------------------------------------------------


async def test_week_pinned_returns_422(client, db) -> None:
    admin = await _make_user(db, email="sow-5@example.com")
    staff_a = await _make_staff(db, name="休む人5")
    patient = await _make_patient(db, code="SOW-5", name="青ピン太郎")
    target = _target_date()
    await _make_visit(db, patient=patient, target=target, staff=staff_a, week_pinned=True)

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"staff_id": str(staff_a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert "1 件" in detail
    assert "青ピン太郎" in detail
    # 休みは登録されない (途中まで書いて止まらない)。
    assert await _overrides(db, staff_a.id) == []


async def test_past_date_returns_422(client, db) -> None:
    admin = await _make_user(db, email="sow-6@example.com")
    staff_a = await _make_staff(db, name="休む人6")

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": (date.today() - timedelta(days=1)).isoformat(),
            "to_staff_id": None,
        },
    )
    assert res.status_code == 422, res.text
    assert "過去" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 7. 冪等
# ---------------------------------------------------------------------------


async def test_second_call_reuses_override(client, db) -> None:
    admin = await _make_user(db, email="sow-7@example.com")
    staff_a = await _make_staff(db, name="休む人7")
    patient = await _make_patient(db, code="SOW-7")
    target = _target_date()
    await _make_visit(db, patient=patient, target=target, staff=staff_a)

    payload = {"staff_id": str(staff_a.id), "date": target.isoformat(), "to_staff_id": None}
    first = await client.post(_URL, headers=_bearer(admin), json=payload)
    assert first.status_code == 200, first.text
    second = await client.post(_URL, headers=_bearer(admin), json=payload)
    assert second.status_code == 200, second.text

    assert second.json()["override_id"] == first.json()["override_id"]
    assert len(await _overrides(db, staff_a.id)) == 1


# ---------------------------------------------------------------------------
# 8. 引き受け先のガード
# ---------------------------------------------------------------------------


async def test_invalid_to_staff_returns_422(client, db) -> None:
    admin = await _make_user(db, email="sow-8@example.com")
    staff_a = await _make_staff(db, name="休む人8")
    trainee = await _make_staff(db, name="新人8", is_trainee=True)
    retired = await _make_staff(db, name="退職者8", status="retired")
    target = _target_date()

    for to_staff_id, keyword in (
        (str(trainee.id), "新人"),
        (str(retired.id), "退職"),
        (str(staff_a.id), "本人"),
    ):
        res = await client.post(
            _URL,
            headers=_bearer(admin),
            json={
                "staff_id": str(staff_a.id),
                "date": target.isoformat(),
                "to_staff_id": to_staff_id,
            },
        )
        assert res.status_code == 422, res.text
        assert keyword in res.json()["detail"]
    assert await _overrides(db, staff_a.id) == []


async def test_staff_role_returns_403(client, db) -> None:
    staff_a = await _make_staff(db, name="休む人9")
    user = await _make_user(db, email="sow-9@example.com", role="staff", staff_id=staff_a.id)

    res = await client.post(
        _URL,
        headers=_bearer(user),
        json={
            "staff_id": str(staff_a.id),
            "date": _target_date().isoformat(),
            "to_staff_id": None,
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 9. 2 名体制 — 相方を巻き込まない (レビュー H2)
# ---------------------------------------------------------------------------


async def test_two_staff_visit_keeps_partner(client, db) -> None:
    """2 名体制: 休む人の枠だけ差し替わり、相方の VSA は残る。undo で本人が戻る."""
    admin = await _make_user(db, email="sow-10@example.com")
    staff_a = await _make_staff(db, name="休む人10")
    partner = await _make_staff(db, name="相方10")
    staff_b = await _make_staff(db, name="引き受ける人10")
    patient = await _make_patient(db, code="SOW-10")
    target = _target_date()
    iso = target.isocalendar()
    visit = await _make_visit(
        db, patient=patient, target=target, staff=staff_a, members=[staff_a, partner]
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text

    await db.refresh(visit)
    assert visit.primary_staff_id == staff_b.id
    # 相方は残る (旧実装の「VSA 全消し + 1 人だけ入れ直し」の回帰防止)。
    assert await _vsa_staff_ids(db, visit.id) == {partner.id, staff_b.id}

    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    await db.refresh(visit)
    assert visit.primary_staff_id == staff_a.id
    # undo でも相方は 1 人のまま (二重登録しない)。
    assert await _vsa_staff_ids(db, visit.id) == {partner.id, staff_a.id}
    # 手動上書きフラグも元 (False) に戻る。
    assert visit.manual_staff_override is False


async def test_secondary_only_visit_moves_slot_without_touching_primary(client, db) -> None:
    """休む人が **2 人目としてだけ** 載っている訪問も対象。primary は動かさない."""
    admin = await _make_user(db, email="sow-11@example.com")
    staff_a = await _make_staff(db, name="休む人11")
    owner = await _make_staff(db, name="主担当11")
    staff_b = await _make_staff(db, name="引き受ける人11")
    patient = await _make_patient(db, code="SOW-11")
    target = _target_date()
    iso = target.isocalendar()
    visit = await _make_visit(
        db, patient=patient, target=target, staff=owner, members=[owner, staff_a]
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["moved_visit_ids"] == [str(visit.id)]

    await db.refresh(visit)
    assert visit.primary_staff_id == owner.id  # 主担当は無傷
    assert await _vsa_staff_ids(db, visit.id) == {owner.id, staff_b.id}

    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    await db.refresh(visit)
    assert visit.primary_staff_id == owner.id
    assert await _vsa_staff_ids(db, visit.id) == {owner.id, staff_a.id}


async def test_to_staff_already_on_visit_is_not_duplicated(client, db) -> None:
    """引き受け先が既に相方として入っている訪問: VSA を二重登録しない."""
    admin = await _make_user(db, email="sow-12@example.com")
    staff_a = await _make_staff(db, name="休む人12")
    staff_b = await _make_staff(db, name="相方かつ引き受け先12")
    patient = await _make_patient(db, code="SOW-12")
    target = _target_date()
    visit = await _make_visit(
        db, patient=patient, target=target, staff=staff_a, members=[staff_a, staff_b]
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text
    await db.refresh(visit)
    assert visit.primary_staff_id == staff_b.id
    assert await _vsa_staff_ids(db, visit.id) == {staff_b.id}


# ---------------------------------------------------------------------------
# 10. planned 以外は据え置き (レビュー M3)
# ---------------------------------------------------------------------------


async def test_non_planned_visits_are_skipped(client, db) -> None:
    admin = await _make_user(db, email="sow-13@example.com")
    staff_a = await _make_staff(db, name="休む人13")
    staff_b = await _make_staff(db, name="引き受ける人13")
    patient = await _make_patient(db, code="SOW-13")
    target = _target_date()
    planned = await _make_visit(db, patient=patient, target=target, staff=staff_a, start_h=9)
    done = await _make_visit(
        db, patient=patient, target=target, staff=staff_a, start_h=13, status_value="completed"
    )

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved_visit_ids"] == [str(planned.id)]
    assert body["skipped_visit_ids"] == [str(done.id)]

    await db.refresh(done)
    assert done.primary_staff_id == staff_a.id  # 実績は動かさない
    assert await _vsa_staff_ids(db, done.id) == {staff_a.id}


# ---------------------------------------------------------------------------
# 11. NG スタッフ (レビュー H1)
# ---------------------------------------------------------------------------


async def test_ng_staff_requires_acknowledge(client, db) -> None:
    """引き受け先が対象患者の NG スタッフ: 422 → acknowledge 再送で通る (§7-2)."""
    admin = await _make_user(db, email="sow-14@example.com")
    staff_a = await _make_staff(db, name="休む人14")
    ng_staff = await _make_staff(db, name="NG対象14")
    patient = await _make_patient(db, code="SOW-14")
    db.add(PatientNgStaff(patient_id=patient.id, staff_id=ng_staff.id, note="相性"))
    await db.commit()
    target = _target_date()
    visit = await _make_visit(db, patient=patient, target=target, staff=staff_a)

    body = {
        "staff_id": str(staff_a.id),
        "date": target.isoformat(),
        "to_staff_id": str(ng_staff.id),
    }
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "constraint_confirmation_required"
    # 確認前は休みも登録されない (聞く前に書かない)。
    assert await _overrides(db, staff_a.id) == []

    res2 = await client.post(
        _URL,
        headers=_bearer(admin),
        json={**body, "acknowledge_constraint_warnings": True},
    )
    assert res2.status_code == 200, res2.text
    await db.refresh(visit)
    assert visit.primary_staff_id == ng_staff.id
    assert len(await _overrides(db, staff_a.id)) == 1


# ---------------------------------------------------------------------------
# 12. 引き受け先がその日休み (レビュー M4)
# ---------------------------------------------------------------------------


async def test_to_staff_off_that_day_returns_422(client, db) -> None:
    admin = await _make_user(db, email="sow-15@example.com")
    staff_a = await _make_staff(db, name="休む人15")
    staff_b = await _make_staff(db, name="その日休みの人15")
    patient = await _make_patient(db, code="SOW-15")
    target = _target_date()
    iso = target.isocalendar()
    await _make_visit(db, patient=patient, target=target, staff=staff_a)
    db.add(
        StaffWeeklyOverride(
            staff_id=staff_b.id,
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=target.weekday(),
            override_type="off",
        )
    )
    await db.commit()

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={
            "staff_id": str(staff_a.id),
            "date": target.isoformat(),
            "to_staff_id": str(staff_b.id),
        },
    )
    assert res.status_code == 422, res.text
    assert "休み" in res.json()["detail"]
    assert await _overrides(db, staff_a.id) == []


# ---------------------------------------------------------------------------
# 13. 既存の override 型を壊さない (時間変更 / 午前休 / 午後休)
# ---------------------------------------------------------------------------


async def test_existing_custom_time_override_is_restored_by_undo(client, db) -> None:
    """元が「時間変更」の日を休みにしても、undo で型・時刻・理由ごと戻る."""
    admin = await _make_user(db, email="sow-16@example.com")
    staff_a = await _make_staff(db, name="休む人16")
    target = _target_date()
    iso = target.isocalendar()
    db.add(
        StaffWeeklyOverride(
            staff_id=staff_a.id,
            iso_year=iso.year,
            iso_week=iso.week,
            weekday=target.weekday(),
            override_type="custom_time",
            start_time=time(13, 0),
            end_time=time(17, 0),
            reason="通院のため",
        )
    )
    await db.commit()

    res = await client.post(
        _URL,
        headers=_bearer(admin),
        json={"staff_id": str(staff_a.id), "date": target.isoformat(), "to_staff_id": None},
    )
    assert res.status_code == 200, res.text
    rows = await _overrides(db, staff_a.id)
    assert len(rows) == 1
    await db.refresh(rows[0])
    assert rows[0].override_type == "off"

    undo = await client.post(
        _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
    )
    assert undo.status_code == 200, undo.text
    restored = await _overrides(db, staff_a.id)
    assert len(restored) == 1
    await db.refresh(restored[0])
    assert restored[0].override_type == "custom_time"
    assert restored[0].start_time == time(13, 0)
    assert restored[0].end_time == time(17, 0)
    assert restored[0].reason == "通院のため"


async def test_existing_half_day_override_is_restored_by_undo(client, db) -> None:
    """午前休 / 午後休の日を休みにしても、undo でその型に戻る (行は 1 件のまま)."""
    admin = await _make_user(db, email="sow-17@example.com")
    target = _target_date()
    iso = target.isocalendar()

    for idx, kind in enumerate(("am_off", "pm_off")):
        staff = await _make_staff(db, name=f"半休の人17-{idx}")
        db.add(
            StaffWeeklyOverride(
                staff_id=staff.id,
                iso_year=iso.year,
                iso_week=iso.week,
                weekday=target.weekday(),
                override_type=kind,
            )
        )
        await db.commit()

        res = await client.post(
            _URL,
            headers=_bearer(admin),
            json={"staff_id": str(staff.id), "date": target.isoformat(), "to_staff_id": None},
        )
        assert res.status_code == 200, res.text
        rows = await _overrides(db, staff.id)
        assert len(rows) == 1
        await db.refresh(rows[0])
        assert rows[0].override_type == "off"

        undo = await client.post(
            _UNDO_URL, headers=_bearer(admin), json={"iso_year": iso.year, "iso_week": iso.week}
        )
        assert undo.status_code == 200, undo.text
        restored = await _overrides(db, staff.id)
        assert len(restored) == 1
        await db.refresh(restored[0])
        assert restored[0].override_type == kind
