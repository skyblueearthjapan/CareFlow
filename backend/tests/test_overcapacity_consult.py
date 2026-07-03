"""定員超過の管理者相談プロセス (方式b) の契約テスト.

対象 (PO 承認済み 2026-07-03):
  1. propose-slots の定員超過候補照会 (include_overcapacity / overcapacity_available_count /
     overcapacity_slots / ProposeSlotItem.overcapacity).
  2. 採用時の理由記録 (capacity_override_reason) — 検証はせず 200 で通り、長さ超過のみ 422.
  3. 設定値注入の是正 (pending_request_applier が config.max_patients_per_course を尊重).

方式b の要点 (背景調査):
  - proposal_solver の max_patients は既に config 経由でパラメータ化済み. 定員超過照会は
    **2 回目の列挙** を上位層 (compute_overcapacity_slots) で行い、config を +1 したコピーを
    渡すだけ (元 config は mutate しない). 1 回目の通常列挙・既存経路は一切変更しない.
  - 時間系制約 (90分同住所占有 / 昼休み / 移動 / 営業枠 / COURSE_MAX_MINUTES) は通常と同一に効く.

ローカル SQLite のみ (本番 DB 禁止).
座標メモ (千葉市近辺, test_propose_slots_api.py と整合):
    BASE = (35.6000, 140.1000) / NEAR = (35.6010, 140.1010) / FAR = (35.6300, 140.1400)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.pending_request import PendingRequest
from app.models.scheduling_settings import SchedulingSettings
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.pending_request_applier import (
    PendingRequestApplier,
    PendingRequestApplyError,
)

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)

_PROPOSE = "/api/v1/schedule/v2/propose-slots"


# ---------------------------------------------------------------------------
# Helpers (test_propose_slots_api.py と同一パターン)
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(db, *, name: str = "稲", code: str = "INAGE") -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(db, *, office: Office, code: str, lat: float, lng: float) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(db, *, office: Office, staff: Staff | None, weekday: int = 0) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(db, *, patient: Patient, course: Course, start: time, end: time) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY,
        start_time=start,
        end_time=end,
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(v)
    await db.flush()
    return v


def _base_payload(office: Office, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "lat": BASE[0],
        "lng": BASE[1],
        "service_minutes": 30,
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_ids": [str(office.id)],
        "preferred_weekdays": ["Mon"],
    }
    body.update(overrides)
    return body


async def _seed_full_course_time_room(db, *, office: Office, staff: Staff) -> Course:
    """定員 6 名 (満員) だが時間には余裕があるコース (異住所 6 名を 30 分刻みで散らす).

    通常候補 (FAR 候補) は容量で 0 件になるが、末尾 (14:50 以降) 等に時間の空きが残る
    ため、定員 +1 なら入れられる = 定員超過候補が出る (test_propose_excludes_full_course の
    満員シードと同一 geometry).
    """
    course = await _seed_course(db, office=office, staff=staff)
    starts = [time(9, 30), time(10, 10), time(10, 50), time(13, 0), time(13, 40), time(14, 20)]
    for i, st in enumerate(starts):
        lat = BASE[0] + 0.02 * (i + 1)
        lng = BASE[1] + 0.02 * (i + 1)
        p = await _seed_patient(db, office=office, code=f"FULL{i}", lat=lat, lng=lng)
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=p, course=course, start=st, end=end)
    return course


# ---------------------------------------------------------------------------
# 1. 満員 + 時間余裕あり → 通常候補 0 件 → overcapacity_available_count >= 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overcapacity_count_when_full_but_time_available(client, db) -> None:
    admin = await _make_user(db, email="oc-count@example.com")
    office, staff = await _seed_office_staff(db)
    await _seed_full_course_time_room(db, office=office, staff=staff)
    await db.commit()

    # include_overcapacity 未指定 (既定 False), 候補は FAR (同住所ペア成立を防ぐ).
    res = await client.post(
        _PROPOSE, headers=_bearer(admin), json=_base_payload(office, lat=FAR[0], lng=FAR[1])
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 通常候補は容量で 0 件.
    assert body["slots"] == [], body["slots"]
    # capacity_full が理由に含まれ、定員 +1 なら入る候補数が載る.
    assert any(e["reason"] == "capacity_full" for e in body["excluded_summary"]), body[
        "excluded_summary"
    ]
    assert body["overcapacity_available_count"] is not None
    assert body["overcapacity_available_count"] >= 1
    # 既定 False では overcapacity_slots は空 (件数のみ).
    assert body["overcapacity_slots"] == []


# ---------------------------------------------------------------------------
# 2. include_overcapacity=true → overcapacity_slots に overcapacity=True, 通常候補は空
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_include_overcapacity_returns_flagged_slots(client, db) -> None:
    admin = await _make_user(db, email="oc-slots@example.com")
    office, staff = await _seed_office_staff(db)
    await _seed_full_course_time_room(db, office=office, staff=staff)
    await db.commit()

    res = await client.post(
        _PROPOSE,
        headers=_bearer(admin),
        json=_base_payload(office, lat=FAR[0], lng=FAR[1], include_overcapacity=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 通常候補は従来どおり空 (容量で 0).
    assert body["slots"] == [], body["slots"]
    # 定員超過候補が別配列で返り、すべて overcapacity=True.
    assert body["overcapacity_slots"], body
    assert all(s["overcapacity"] is True for s in body["overcapacity_slots"])
    # 実現可能な時刻のみ (start < end, 18:00 以内) — 時間系制約は通常と同一に効く.
    for s in body["overcapacity_slots"]:
        assert s["start_time"] < s["end_time"]
        assert s["end_time"] <= "18:00"
        assert s["weekday_code"] == "Mon"
    # delta (marginal_cost_minutes) も付与される (上位候補).
    assert any(s["marginal_cost_minutes"] is not None for s in body["overcapacity_slots"])
    # True のときは件数フィールドは None のまま (実配列を返すため).
    assert body["overcapacity_available_count"] is None


# ---------------------------------------------------------------------------
# 3. 時間も入らない満員コース (隙間なし) → overcapacity_available_count = 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overcapacity_zero_when_no_time_gap(client, db) -> None:
    admin = await _make_user(db, email="oc-zero@example.com")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 同住所 6 名で AM/PM を隙間なく埋める (移動 0). 定員 +1 でも入る時間が無い.
    #   p0: 09:30-12:00 (AM 満杯) / p1..p5: 13:00-18:00 を 60 分刻みで満杯.
    p0 = await _seed_patient(db, office=office, code="PK0", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=p0, course=course, start=time(9, 30), end=time(12, 0))
    pm_starts = [time(13, 0), time(14, 0), time(15, 0), time(16, 0), time(17, 0)]
    for i, st in enumerate(pm_starts):
        p = await _seed_patient(db, office=office, code=f"PK{i + 1}", lat=NEAR[0], lng=NEAR[1])
        await _seed_visit(db, patient=p, course=course, start=st, end=time(st.hour + 1, 0))
    await db.commit()

    # 候補は FAR (同住所ペアを防ぐ). 通常 0 件 + 定員 +1 でも時間が無い → count 0.
    res = await client.post(
        _PROPOSE, headers=_bearer(admin), json=_base_payload(office, lat=FAR[0], lng=FAR[1])
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"] == []
    assert any(e["reason"] == "capacity_full" for e in body["excluded_summary"])
    assert body["overcapacity_available_count"] == 0


# ---------------------------------------------------------------------------
# 4. 通常候補が 1 件以上 → overcapacity_available_count は None / overcapacity_slots は空
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overcapacity_none_when_normal_candidates_exist(client, db) -> None:
    admin = await _make_user(db, email="oc-none@example.com")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 空きが十分あるコース (既存 1 名) → 通常候補が出る.
    pn = await _seed_patient(db, office=office, code="ANC", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.post(
        _PROPOSE, headers=_bearer(admin), json=_base_payload(office, lat=FAR[0], lng=FAR[1])
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body  # 通常候補あり.
    # 通常候補があるときは定員超過は計算しない.
    assert body["overcapacity_available_count"] is None
    assert body["overcapacity_slots"] == []


# ---------------------------------------------------------------------------
# 5. 既定 (include_overcapacity=false) で既存レスポンスが完全不変 (回帰)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_response_backward_compatible(client, db) -> None:
    admin = await _make_user(db, email="oc-regress@example.com")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    pn = await _seed_patient(db, office=office, code="RG1", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(db, patient=pn, course=course, start=time(9, 30), end=time(10, 0))
    await db.commit()

    res = await client.post(_PROPOSE, headers=_bearer(admin), json=_base_payload(office))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["slots"], body
    # 追加フィールドは既定値 (None / 空) で、通常 slots は overcapacity=False.
    assert body["overcapacity_available_count"] is None
    assert body["overcapacity_slots"] == []
    assert all(s["overcapacity"] is False for s in body["slots"])


# ---------------------------------------------------------------------------
# 6. capacity_override_reason: 付き PUT が 200 / 長さ 501 は 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_fixed_visits_accepts_capacity_override_reason(client, db) -> None:
    admin = await _make_user(db, email="oc-put@example.com")
    office, _ = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="OC-PUT", lat=NEAR[0], lng=NEAR[1])
    await db.commit()

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
            "capacity_override_reason": "定員超過だが現場判断で採用",
        },
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_put_fixed_visits_reason_too_long_422(client, db) -> None:
    admin = await _make_user(db, email="oc-put-long@example.com")
    office, _ = await _seed_office_staff(db)
    patient = await _seed_patient(db, office=office, code="OC-PUT-L", lat=NEAR[0], lng=NEAR[1])
    await db.commit()

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json={
            "mode": "normal",
            "items": [{"weekday": 0, "start_time": "10:00:00", "duration_min": 30}],
            "capacity_override_reason": "あ" * 501,
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 7. 設定値注入の是正: pending_request_applier が config.max_patients_per_course を尊重
# ---------------------------------------------------------------------------

_G7_ISO_YEAR = 2026
_G7_ISO_WEEK = 23


@pytest.mark.asyncio
async def test_pending_applier_respects_configured_capacity(db) -> None:
    """max_patients_per_course=7 設定時、既存 6 名は満員扱いにならず override_reason 無しで通る.

    バグ修正前は module 定数 (=6) 直参照で、6 名 → capacity_full → 422 になっていた.
    設定 (DB) を尊重すれば 6 < 7 で赤にならず、単発配置が成功する (回帰施錠).
    """
    # 事業所別設定を 7 名に設定 (シングルトン 1 行).
    db.add(SchedulingSettings(is_singleton=True, max_patients_per_course=7))
    await db.commit()

    user = await _make_user(db, email="oc-applier@example.com")
    office = Office(name="稲毛G7", code="INAGE")
    db.add(office)
    await db.flush()
    patient = Patient(code="P-OC-CAP", name="患者", status="active")
    db.add(patient)
    await db.flush()
    weekday = 1
    course = Course(
        iso_year=_G7_ISO_YEAR,
        iso_week=_G7_ISO_WEEK,
        weekday=weekday,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    visit_date = date.fromisocalendar(_G7_ISO_YEAR, _G7_ISO_WEEK, weekday + 1)

    # 既存 6 名 (時刻は重ならない 30 分刻み) → 設定 7 名では満員ではない.
    for i in range(6):
        ex = Patient(code=f"P-OC-FILL-{i}", name="既存", status="active")
        db.add(ex)
        await db.flush()
        start_min = 9 * 60 + i * 30
        end_min = start_min + 30
        db.add(
            Visit(
                patient_id=ex.id,
                visit_date=visit_date,
                start_time=time(start_min // 60, start_min % 60),
                end_time=time(end_min // 60, end_min % 60),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="manual",
                course_id=course.id,
                required_staff_count=1,
            )
        )
    await db.commit()

    # override_reason 無し・既存と重ならない 13:00 を提案 → capacity_full にならず成功.
    pr = PendingRequest(
        requester_user_id=user.id,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "course_id": str(course.id),
            "iso_year": _G7_ISO_YEAR,
            "iso_week": _G7_ISO_WEEK,
            "weekday": weekday,
            "proposed_visits": [{"weekday": weekday, "start_time": "13:00", "duration_min": 30}],
        },
        target_patient_id=patient.id,
        scope="one_time",
        status="pending",
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(rows) == 1
    assert rows[0].start_time == time(13, 0)


@pytest.mark.asyncio
async def test_pending_applier_default_capacity_still_blocks_at_six(db) -> None:
    """設定行なし (既定 6) では従来どおり 6 名で満員 → override_reason 無しは 422 (退行防止)."""
    user = await _make_user(db, email="oc-applier-default@example.com")
    office = Office(name="稲毛G7d", code="INAGE")
    db.add(office)
    await db.flush()
    patient = Patient(code="P-OC-CAP-D", name="患者", status="active")
    db.add(patient)
    await db.flush()
    weekday = 1
    course = Course(
        iso_year=_G7_ISO_YEAR,
        iso_week=_G7_ISO_WEEK,
        weekday=weekday,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    visit_date = date.fromisocalendar(_G7_ISO_YEAR, _G7_ISO_WEEK, weekday + 1)
    for i in range(6):
        ex = Patient(code=f"P-OC-FILL-D-{i}", name="既存", status="active")
        db.add(ex)
        await db.flush()
        start_min = 9 * 60 + i * 30
        end_min = start_min + 30
        db.add(
            Visit(
                patient_id=ex.id,
                visit_date=visit_date,
                start_time=time(start_min // 60, start_min % 60),
                end_time=time(end_min // 60, end_min % 60),
                type="regular",
                status=VISIT_STATUS_PLANNED,
                source="manual",
                course_id=course.id,
                required_staff_count=1,
            )
        )
    await db.commit()

    pr = PendingRequest(
        requester_user_id=user.id,
        request_type="patient_visit_add",
        payload={
            "patient_id": str(patient.id),
            "course_id": str(course.id),
            "iso_year": _G7_ISO_YEAR,
            "iso_week": _G7_ISO_WEEK,
            "weekday": weekday,
            "proposed_visits": [{"weekday": weekday, "start_time": "13:00", "duration_min": 30}],
        },
        target_patient_id=patient.id,
        scope="one_time",
        status="pending",
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)

    applier = PendingRequestApplier()
    with pytest.raises(PendingRequestApplyError) as exc_info:
        await applier.apply(db, pr)
    assert exc_info.value.http_status == 422
