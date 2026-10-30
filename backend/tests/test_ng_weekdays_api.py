"""NG 曜日 (weekly_pattern.ng_weekdays) — 日を選ぶ API の 422・固定訪問の検査・点検のテスト.

正典: docs/plans/biweekly-monthly-design-2026-10-30.md §9 (PO 決定 2026-10-30
「NG 曜日＝画面に出して全経路で守る」)。ローカル SQLite のみ。

検証の軸:
    A. 今週だけ移動 / 追加・固定枠化・提案の採用・一括投入・コースの曜日移動 は、
       移動先 / 置き先の曜日が NG 曜日なら 422 ``code='ng_weekday'`` (書き込み前)。
       同じ曜日の中で時刻だけ変えるのは止めない。
    B. 固定訪問の枠 (PUT fixed-visits / 検査): NG 曜日に枠を作るのは error (422)。
       保存前からその曜日にある枠は warning (止めない)。
    C. 提案 (propose-slots) / 保留プール (pool-overview) は NG 曜日を候補にせず、理由を返す。
    D. 点検 (feasibility) は NG 曜日に入っている予定を △ として知らせる (週の生成は消さない)。
    E. 患者の保存は ng_weekdays を検査してそろえ、ほかのキーを消さない。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, Staff, User
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.pending_request import PendingRequest
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.pending_request_applier import (
    PendingRequestApplier,
    PendingRequestApplyError,
)
from app.services.scheduling.feasibility_check import (
    KIND_NG_WEEKDAY,
    load_ng_weekday_findings,
)
from app.services.scheduling.layer1_expander import Layer1Expander
from app.services.scheduling.ng_weekdays import ng_weekday_message

_TODAY_JST = datetime.now(UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()
WEEK_MONDAY = _TODAY_JST + timedelta(days=7 - _TODAY_JST.weekday())
ISO_YEAR, ISO_WEEK, _ = WEEK_MONDAY.isocalendar()


async def _admin(db, email: str) -> dict[str, str]:
    user = User(email=email, password_hash=hash_password("pw"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _office(db, name: str = "稲毛") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _patient(db, *, code: str, office: Office | None = None, ng=("Tue",), **wp) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status="active",
        primary_office_id=office.id if office is not None else None,
        weekly_pattern={"ng_weekdays": list(ng), **wp},
        special_week_active=[],
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _template(db, office: Office, label: str = "A") -> CourseTemplate:
    tpl = CourseTemplate(
        office_id=office.id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


async def _visit(db, patient: Patient, *, weekday: int = 0, course: Course | None = None) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY + timedelta(days=weekday),
        start_time=time(9, 0),
        end_time=time(9, 30),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
        course_id=course.id if course is not None else None,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _assert_ng(res, patient: Patient, weekday: int) -> None:
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "ng_weekday"
    assert detail["patient_id"] == str(patient.id)
    assert detail["weekday"] == weekday
    assert f"{patient.name}様" in detail["message"]
    assert "NG 曜日（訪問しない曜日）" in detail["message"]


# ---------------------------------------------------------------------------
# A. 日を選ぶ API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_rejects_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-paf@example.com")
    office = await _office(db)
    tpl = await _template(db, office)
    p = await _patient(db, code="NG-PAF", office=office)
    body = {
        "patient_id": str(p.id),
        "course_template_id": str(tpl.id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "weekday": 1,
        "start_time": "10:00:00",
        "duration_min": 45,
        "staff_count": 1,
        "fix_pattern": True,
    }
    res = await client.post("/api/v1/schedule/place-and-fix", headers=h, json=body)
    _assert_ng(res, p, 1)
    assert (await db.scalars(select(Visit).where(Visit.patient_id == p.id))).all() == []
    assert (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all() == []

    body["weekday"] = 2  # NG でない曜日は通る
    res = await client.post("/api/v1/schedule/place-and-fix", headers=h, json=body)
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["this_week_only", "pattern_change"])
async def test_fix_or_pattern_rejects_move_onto_ng_weekday(client, db, mode: str) -> None:
    h = await _admin(db, f"ng-fop-{mode}@example.com")
    office = await _office(db)
    p = await _patient(db, code=f"NG-FOP-{mode}", office=office)
    v = await _visit(db, p, weekday=0)
    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=h,
        json={
            "visit_id": str(v.id),
            "mode": mode,
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "new_weekday": 1,
            "new_start_time": "11:00:00",
            "new_duration_min": 30,
        },
    )
    _assert_ng(res, p, 1)
    await db.refresh(v)
    assert v.visit_date == WEEK_MONDAY


@pytest.mark.asyncio
async def test_fix_or_pattern_allows_time_change_on_existing_ng_day(client, db) -> None:
    """すでに NG 曜日にある予定の時刻だけを変えるのは止めない (別の日へ動かせなくしない)."""
    h = await _admin(db, "ng-fop-same@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-FOP-SAME", office=office)
    v = await _visit(db, p, weekday=1)
    res = await client.post(
        "/api/v1/schedule/fix-or-pattern",
        headers=h,
        json={
            "visit_id": str(v.id),
            "mode": "this_week_only",
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "new_weekday": 1,
            "new_start_time": "11:00:00",
            "new_duration_min": 30,
        },
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_create_visit_rejects_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-cv@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-CV", office=office)
    body = {
        "patient_id": str(p.id),
        "visit_date": (WEEK_MONDAY + timedelta(days=1)).isoformat(),
        "start_time": "09:00:00",
        "end_time": "09:30:00",
        "type": "regular",
    }
    res = await client.post("/api/v1/visits", headers=h, json=body)
    _assert_ng(res, p, 1)
    body["visit_date"] = WEEK_MONDAY.isoformat()
    res = await client.post("/api/v1/visits", headers=h, json=body)
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_patch_visit_rejects_date_move_onto_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-pv@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PV", office=office)
    v = await _visit(db, p, weekday=0)
    res = await client.patch(
        f"/api/v1/visits/{v.id}",
        headers=h,
        json={"visit_date": (WEEK_MONDAY + timedelta(days=1)).isoformat()},
    )
    _assert_ng(res, p, 1)
    # メモだけの変更は通る.
    res = await client.patch(f"/api/v1/visits/{v.id}", headers=h, json={"note": "x"})
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_patch_visit_rejects_reassign_to_patient_with_ng_on_that_day(client, db) -> None:
    h = await _admin(db, "ng-pv2@example.com")
    office = await _office(db)
    a = await _patient(db, code="NG-PV2-A", office=office, ng=())
    b = await _patient(db, code="NG-PV2-B", office=office, ng=("Mon",))
    v = await _visit(db, a, weekday=0)
    res = await client.patch(f"/api/v1/visits/{v.id}", headers=h, json={"patient_id": str(b.id)})
    _assert_ng(res, b, 0)


def _move_body(p: Patient, new_weekday: int) -> dict:
    return {
        "patient_id": str(p.id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "old_weekday": 0,
        "old_start_time": "09:00:00",
        "new_weekday": new_weekday,
        "new_start_time": "10:00:00",
    }


@pytest.mark.asyncio
async def test_visit_move_week_only_rejects_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-mvw@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-MVW", office=office)
    v = await _visit(db, p, weekday=0)
    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only", headers=h, json=_move_body(p, 1)
    )
    _assert_ng(res, p, 1)
    await db.refresh(v)
    assert v.visit_date == WEEK_MONDAY
    res = await client.post(
        "/api/v1/schedule/v2/visit-move-week-only", headers=h, json=_move_body(p, 2)
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_course_move_weekday_rejects_when_a_patient_has_ng(client, db) -> None:
    h = await _admin(db, "ng-cmw@example.com")
    office = await _office(db)
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status="course_fixed",
        office_id=office.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    ok = await _patient(db, code="NG-CMW-OK", office=office, ng=())
    ng = await _patient(db, code="NG-CMW-NG", office=office, ng=("Wed",))
    await _visit(db, ok, weekday=0, course=course)
    v_ng = await _visit(db, ng, weekday=0, course=course)
    res = await client.post(
        "/api/v1/schedule/v2/course-move-weekday-week-only",
        headers=h,
        json={"course_id": str(course.id), "to_weekday": 2},
    )
    _assert_ng(res, ng, 2)
    await db.refresh(course)
    await db.refresh(v_ng)
    assert course.weekday == 0 and v_ng.visit_date == WEEK_MONDAY


@pytest.mark.asyncio
async def test_apply_individual_rejects_new_slot_on_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-ai@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-AI", office=office)

    def body(weekday: int) -> dict:
        return {
            "confirm": True,
            "patient_id": str(p.id),
            "change_scope": "pattern_only",
            "visit_plans": [
                {
                    "weekday": weekday,
                    "start_time": "10:00:00",
                    "end_time": "10:30:00",
                    "duration_min": 30,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "am_pm": "am",
                }
            ],
        }

    res = await client.post("/api/v1/schedule/v2/apply-individual", headers=h, json=body(1))
    _assert_ng(res, p, 1)
    assert (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all() == []
    res = await client.post("/api/v1/schedule/v2/apply-individual", headers=h, json=body(3))
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# B. 固定訪問の枠
# ---------------------------------------------------------------------------


def _pfv_body(weekday: int, start: str = "10:00:00") -> dict:
    return {
        "mode": "normal",
        "items": [{"weekday": weekday, "start_time": start, "duration_min": 30}],
        "change_scope": "pattern_only",
    }


@pytest.mark.asyncio
async def test_put_fixed_visits_rejects_new_slot_on_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-pfv@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PFV", office=office)
    res = await client.put(f"/api/v1/patients/{p.id}/fixed-visits", headers=h, json=_pfv_body(1))
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "ng_weekday"
    assert detail["weekdays"] == [1]
    assert "NG 曜日（訪問しない曜日）" in detail["message"]
    # 画面は message と violations[].message を並べて出すので、同じ文言を二度入れない.
    assert detail["violations"] == []
    assert (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all() == []


@pytest.mark.asyncio
async def test_put_fixed_visits_keeps_existing_ng_slot_with_warning(client, db) -> None:
    """保存前からある NG 曜日の枠は止めない (時刻の変更も通す) — 知らせるだけ."""
    h = await _admin(db, "ng-pfv-keep@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PFV-KEEP", office=office)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()
    res = await client.post(
        f"/api/v1/patients/{p.id}/fixed-visits/validate",
        headers=h,
        json={
            "mode": "normal",
            "items": [{"weekday": 1, "start_time": "11:00:00", "duration_min": 30}],
        },
    )
    assert res.status_code == 200, res.text
    ng = [w for w in res.json()["warnings"] if w["code"] == "ng_weekday"]
    assert len(ng) == 1 and ng[0]["severity"] == "warning"
    res = await client.put(
        f"/api/v1/patients/{p.id}/fixed-visits", headers=h, json=_pfv_body(1, "11:00:00")
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# C. 提案・保留プール
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_slots_reports_ng_weekday_when_all_preferred_days_are_ng(client, db) -> None:
    h = await _admin(db, "ng-ps@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PS", office=office)
    res = await client.post(
        "/api/v1/schedule/v2/propose-slots",
        headers=h,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "existing_patient_id": str(p.id),
            "service_minutes": 30,
            "lat": 35.6,
            "lng": 140.1,
            "office_ids": [str(office.id)],
            "preferred_weekdays": ["Tue"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"] == []
    assert [(e["reason"], e["weekday"]) for e in body["excluded_summary"]] == [("ng_weekday", 1)]


@pytest.mark.asyncio
async def test_pool_overview_top_reason_is_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-po@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PO", office=office, preferred_weekdays=["Tue"], ng=("Tue",))
    p.lat, p.lng = 35.6, 140.1
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=h,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res.status_code == 200, res.text
    (item,) = res.json()["items"]
    assert item["candidate_count"] == 0
    assert item["top_excluded_reason"] == "ng_weekday"


# ---------------------------------------------------------------------------
# D. 点検 (feasibility)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feasibility_lists_visits_on_ng_weekday(db) -> None:
    office = await _office(db)
    p = await _patient(db, code="NG-FEAS", office=office)
    await _visit(db, p, weekday=1)  # NG (火)
    await _visit(db, p, weekday=2)  # NG でない
    findings = await load_ng_weekday_findings(
        db, week_start=WEEK_MONDAY, week_end=WEEK_MONDAY + timedelta(days=6)
    )
    assert [(f.kind, f.day, f.to) for f in findings] == [
        (KIND_NG_WEEKDAY, WEEK_MONDAY + timedelta(days=1), p.name)
    ]
    assert findings[0].severity == "soft"
    other = await _office(db, name="都賀")
    assert (
        await load_ng_weekday_findings(
            db,
            week_start=WEEK_MONDAY,
            week_end=WEEK_MONDAY + timedelta(days=6),
            office_id=other.id,
        )
        == []
    )


# ---------------------------------------------------------------------------
# E. 患者の保存
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_patch_normalizes_ng_weekdays_and_rejects_bad_codes(client, db) -> None:
    h = await _admin(db, "ng-pp@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PP", office=office, ng=())
    res = await client.patch(
        f"/api/v1/patients/{p.id}",
        headers=h,
        json={
            "weekly_pattern": {
                "ng_weekdays": ["Sun", "Mon", "Sun"],
                "preferred_weekdays": ["Wed"],
                "visit_frequency": "every",
            }
        },
    )
    assert res.status_code == 200, res.text
    wp = res.json()["weekly_pattern"]
    assert wp["ng_weekdays"] == ["Mon", "Sun"]
    assert wp["preferred_weekdays"] == ["Wed"]
    assert wp["visit_frequency"] == "every"

    res = await client.patch(
        f"/api/v1/patients/{p.id}",
        headers=h,
        json={"weekly_pattern": {"ng_weekdays": ["Someday"]}},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_pool_bulk_apply_rejects_stale_placement_on_ng_weekday(client, db) -> None:
    """simulate の後に NG 曜日が付いた古い placements も、1 人も置かずに 422."""
    h = await _admin(db, "ng-pba@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PBA", office=office)
    sim = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=h,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [],
        },
    )
    assert sim.status_code == 200, sim.text
    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-apply",
        headers=h,
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "state_token": sim.json()["state_token"],
            "placements": [
                {
                    "seq": 1,
                    "patient_id": str(p.id),
                    "patient_name": p.name,
                    "weekday": 1,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "start_time": "10:00:00",
                    "service_minutes": 30,
                }
            ],
        },
    )
    _assert_ng(res, p, 1)
    assert (await db.scalars(select(Visit).where(Visit.patient_id == p.id))).all() == []


# ---------------------------------------------------------------------------
# F. ステージ 3 レビューの指摘 (現場ボードの申請・全面最適化・固定枠の書き込み口)
# ---------------------------------------------------------------------------


async def _staff(db, name: str = "職員NG") -> Staff:
    s = Staff(name=name, role="staff", is_trainee=False)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _pending(db, *, user: User, patient: Patient, payload: dict, scope: str | None = None):
    row = PendingRequest(
        requester_user_id=user.id,
        request_type="patient_visit_add",
        payload=payload,
        target_patient_id=patient.id,
        scope=scope,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _plain_user(db, email: str) -> User:
    u = User(email=email, password_hash="x", role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _assert_apply_ng(exc: PendingRequestApplyError, pid, name: str, weekday: int) -> None:
    assert exc.http_status == 422
    assert exc.detail is not None
    assert exc.detail["code"] == "ng_weekday"
    assert exc.detail["weekday"] == weekday
    assert exc.detail["patient_id"] == str(pid)
    assert exc.detail["message"] == ng_weekday_message(name, weekday)
    # HTTP 層へ渡す detail は構造化した dict (ほかの経路の NG 曜日の 422 と同じ形).
    assert exc.http_detail == exc.detail


@pytest.mark.asyncio
async def test_visit_add_permanent_request_rejects_ng_weekday(db) -> None:
    user = await _plain_user(db, "ng-va-perm@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-VA-PERM", office=office)
    pid, name = p.id, p.name

    def payload(weekday: int) -> dict:
        return {
            "patient_id": str(p.id),
            "proposed_visits": [{"weekday": weekday, "start_time": "10:00", "duration_min": 30}],
        }

    pr = await _pending(db, user=user, patient=p, payload=payload(1))
    with pytest.raises(PendingRequestApplyError) as ei:
        await PendingRequestApplier().apply(db, pr)
    _assert_apply_ng(ei.value, pid, name, 1)
    await db.rollback()
    assert (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == pid))
    ).all() == []

    await db.refresh(user)
    await db.refresh(p)
    pr2 = await _pending(db, user=user, patient=p, payload=payload(2))
    await PendingRequestApplier().apply(db, pr2)
    await db.commit()
    rows = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all()
    assert [r.weekday for r in rows] == [2]


@pytest.mark.asyncio
async def test_visit_add_one_time_request_rejects_ng_weekday(db) -> None:
    user = await _plain_user(db, "ng-va-one@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-VA-ONE", office=office)
    pid, name = p.id, p.name
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=1,
        code="A",
        course_status="course_fixed",
        office_id=office.id,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    pr = await _pending(
        db,
        user=user,
        patient=p,
        scope="one_time",
        payload={
            "patient_id": str(p.id),
            "course_id": str(course.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "weekday": 1,
            "proposed_visits": [{"weekday": 1, "start_time": "13:30", "duration_min": 30}],
        },
    )
    with pytest.raises(PendingRequestApplyError) as ei:
        await PendingRequestApplier().apply(db, pr)
    _assert_apply_ng(ei.value, pid, name, 1)
    await db.rollback()
    assert (await db.scalars(select(Visit).where(Visit.patient_id == pid))).all() == []


@pytest.mark.asyncio
async def test_reschedule_permanent_request_keeps_other_weekly_pattern_keys(db) -> None:
    """時刻変更の申請 (恒常) は、申請に載ったキーだけを書き換える (NG 曜日などを消さない)."""
    user = await _plain_user(db, "ng-resched@example.com")
    office = await _office(db)
    p = await _patient(
        db,
        code="NG-RESCHED",
        office=office,
        ng=("Tue",),
        preferred_weekdays=["Mon"],
        visit_frequency="biweekly",
        biweekly_anchor="2026-11-02",
        visit_weeks=[1, 3],
        entries=[{"weekday": "Mon", "preferred_start": "09:00"}],
        future_key={"keep": True},
    )
    v = await _visit(db, p, weekday=0)
    row = PendingRequest(
        requester_user_id=user.id,
        request_type="patient_reschedule",
        payload={
            "visit_id": str(v.id),
            "new_start_time": "10:00",
            "new_end_time": "10:30",
            "new_weekly_pattern": {"preferred_start": "10:00", "preferred_weekdays": ["Mon"]},
        },
        target_patient_id=p.id,
        scope="permanent",
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await PendingRequestApplier().apply(db, row)
    await db.commit()
    await db.refresh(p)
    wp = p.weekly_pattern
    assert wp["preferred_start"] == "10:00"
    assert wp["ng_weekdays"] == ["Tue"]
    assert wp["visit_frequency"] == "biweekly"
    assert wp["biweekly_anchor"] == "2026-11-02"
    assert wp["visit_weeks"] == [1, 3]
    assert wp["entries"] == [{"weekday": "Mon", "preferred_start": "09:00"}]
    assert wp["future_key"] == {"keep": True}


@pytest.mark.asyncio
async def test_pfv_validate_second_slot_on_ng_weekday_is_error(client, db) -> None:
    """保存前からある NG 曜日の枠は slot ごとに見る: 同じ曜日の 2 つ目の枠は新しく作る扱い."""
    h = await _admin(db, "ng-pfv-slot@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PFV-SLOT", office=office)
    p.requires_multiple_staff = True
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()
    res = await client.post(
        f"/api/v1/patients/{p.id}/fixed-visits/validate",
        headers=h,
        json={
            "mode": "normal",
            "items": [
                {"weekday": 1, "start_time": "10:00:00", "duration_min": 30, "slot_index": 0},
                {"weekday": 1, "start_time": "10:00:00", "duration_min": 30, "slot_index": 1},
            ],
        },
    )
    assert res.status_code == 200, res.text
    ng = [w for w in res.json()["warnings"] if w["code"] == "ng_weekday"]
    assert len(ng) == 1 and ng[0]["severity"] == "error"


@pytest.mark.asyncio
async def test_apply_individual_slot1_only_on_ng_weekday_does_not_count_as_existing(
    client, db
) -> None:
    """採用は slot 0 を書くので、その曜日に slot 1 しか無ければ新しい枠として 422."""
    h = await _admin(db, "ng-ai-slot@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-AI-SLOT", office=office)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,
            start_time=time(10, 0),
            duration_min=30,
            slot_index=1,
        )
    )
    await db.commit()
    res = await client.post(
        "/api/v1/schedule/v2/apply-individual",
        headers=h,
        json={
            "confirm": True,
            "patient_id": str(p.id),
            "change_scope": "pattern_only",
            "visit_plans": [
                {
                    "weekday": 1,
                    "start_time": "10:00:00",
                    "end_time": "10:30:00",
                    "duration_min": 30,
                    "course_code": "A",
                    "office_id": str(office.id),
                    "am_pm": "am",
                }
            ],
        },
    )
    _assert_ng(res, p, 1)


@pytest.mark.asyncio
async def test_update_fixed_time_master_rejects_new_slot_on_ng_weekday(client, db) -> None:
    h = await _admin(db, "ng-uftm@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-UFTM", office=office)
    body = {"patient_id": str(p.id), "weekday": 1, "new_start": "10:00", "new_end": "10:30"}
    res = await client.post("/api/v1/schedule/v2/update-fixed-time-master", headers=h, json=body)
    _assert_ng(res, p, 1)
    assert (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).all() == []

    # 保存前からある NG 曜日の枠の時刻だけを変えるのは通す.
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()
    res = await client.post("/api/v1/schedule/v2/update-fixed-time-master", headers=h, json=body)
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_patch_visit_on_existing_ng_day_allows_staff_or_time_only(client, db) -> None:
    """すでに NG 曜日にある訪問の担当だけ / 時刻だけの変更は止めない."""
    h = await _admin(db, "ng-pv-same@example.com")
    office = await _office(db)
    p = await _patient(db, code="NG-PV-SAME", office=office)
    staff = await _staff(db)
    v = await _visit(db, p, weekday=1)  # NG (火)
    res = await client.patch(
        f"/api/v1/visits/{v.id}", headers=h, json={"primary_staff_id": str(staff.id)}
    )
    assert res.status_code == 200, res.text
    res = await client.patch(
        f"/api/v1/visits/{v.id}",
        headers=h,
        json={"start_time": "11:00:00", "end_time": "11:30:00"},
    )
    assert res.status_code == 200, res.text
    await db.refresh(v)
    assert v.primary_staff_id == staff.id
    assert v.start_time == time(11, 0)


@pytest.mark.asyncio
async def test_week_generation_still_expands_existing_ng_weekday_slot(db) -> None:
    """週の生成は保存済みの枠をそのまま作る (NG 曜日でも黙って落とさない)."""
    office = await _office(db)
    p = await _patient(db, code="NG-L1", office=office)
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=1,  # NG (火)
            start_time=time(10, 0),
            duration_min=30,
            slot_index=0,
        )
    )
    await db.commit()
    await Layer1Expander().expand_week(db, iso_year=ISO_YEAR, iso_week=ISO_WEEK)
    await db.commit()
    visits = (
        await db.scalars(select(Visit).where(Visit.patient_id == p.id, Visit.deleted_at.is_(None)))
    ).all()
    assert [v.visit_date for v in visits] == [WEEK_MONDAY + timedelta(days=1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra_wp",
    [
        # 希望 (月・火) + NG 曜日 (火) に保存済みの枠: 希望から作る患者.
        {"preferred_weekdays": ["Mon", "Tue"], "preferred_start": "10:00", "service_minutes": 30},
        # 使える希望が無い (NG 曜日だけ) 患者: 固定訪問の表から作る.
        {},
    ],
    ids=["with-preferences", "ng-only"],
)
async def test_full_optimize_then_apply_week_only_keeps_ng_weekday_visit(
    client, db, extra_wp: dict
) -> None:
    """全面最適化 → この週だけ反映 で、NG 曜日にある保存済みの枠の訪問が消えない."""
    from tests.test_schedule_v2_api import _seed_office_with_staff, _seed_patient

    h = await _admin(db, f"ng-fo-{len(extra_wp)}@example.com")
    office, _ = await _seed_office_with_staff(db)
    p = await _seed_patient(
        db,
        office=office,
        code=f"NG-FO-{len(extra_wp)}",
        weekly_pattern={"ng_weekdays": ["Tue"], "time_type": "固定", **extra_wp},
    )
    for wd in (0, 1):
        db.add(
            PatientFixedVisit(
                patient_id=p.id,
                mode="normal",
                weekday=wd,
                start_time=time(10, 0),
                duration_min=30,
                slot_index=0,
            )
        )
    await db.commit()

    fo = await client.post(
        "/api/v1/schedule/v2/full-optimize",
        headers=h,
        json={"iso_year": 2026, "iso_week": 20, "office_ids": [str(office.id)]},
    )
    assert fo.status_code == 200, fo.text
    body = fo.json()
    plans = [
        {
            "weekday": day["weekday"],
            "start_time": v["start_time"],
            "end_time": v["end_time"],
            "duration_min": v["duration_min"],
            "course_code": c["code"],
            "office_id": c["office_id"],
            "am_pm": v["am_pm"],
        }
        for day in body["week_proposals"]
        for c in (day["after"] or {}).get("courses", [])
        for v in c["visits"]
        if v["patient_id"] == str(p.id)
    ]
    assert sorted(pl["weekday"] for pl in plans) == [0, 1]
    assert any(
        w["patient_id"] == str(p.id) and w["weekday"] == 1 and "NG 曜日" in w["message"]
        for w in body["warnings"]
    )

    res = await client.post(
        "/api/v1/schedule/v2/apply-week-only",
        headers=h,
        json={
            "iso_year": 2026,
            "iso_week": 20,
            "office_ids": [str(office.id)],
            "visit_plans_per_patient": [{"patient_id": str(p.id), "visit_plans": plans}],
            "confirm": True,
        },
    )
    assert res.status_code == 200, res.text
    week_monday = date.fromisocalendar(2026, 20, 1)
    days = sorted(
        v.visit_date
        for v in (
            await db.scalars(
                select(Visit).where(Visit.patient_id == p.id, Visit.deleted_at.is_(None))
            )
        ).all()
    )
    assert days == [week_monday, week_monday + timedelta(days=1)]
