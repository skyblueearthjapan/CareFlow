"""Stage P-2 テスト: POST /api/v1/schedule/v2/pool-overview.

保留プールの患者ごとに「最良候補 (delta 最小) 1 件」を軽量計算して返す read-only
endpoint. 候補生成は propose-slots と完全に同一 (``compute_all_proposed_slots`` 再利用)
で、best_delta_minutes は propose-slots 単体呼び出しの先頭候補と一致する (整合クロスチェック).

検証内容:
    1. 複数患者で items が返り、best_delta_minutes が propose-slots 先頭候補と一致.
    2. 候補 0 件の患者は best_slot=null / candidate_count=0 / top_excluded_reason 非 null.
    3. patient_ids 51 件で 422.
    4. 空 patient_ids → items=[] (200).
    5. office_id フィルタの効き (別拠点のコースが候補に出ない).
    6. RBAC (staff 403 / no-auth 401).

ローカル SQLite のみ (本番 DB 禁止). 座標は test_propose_slots_api.py と整合.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office_staff(
    db, *, name: str = "稲", code: str | None = "INAGE"
) -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name="担当看護師", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    return office, staff


async def _seed_patient(
    db,
    *,
    office: Office,
    code: str,
    lat: float | None,
    lng: float | None,
    name: str | None = None,
    weekly_pattern: dict | None = None,
    sex_restriction: str | None = None,
    requires_multiple_staff: bool = False,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
        weekly_pattern=weekly_pattern,
        sex_restriction=sex_restriction,
        requires_multiple_staff=requires_multiple_staff,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_course(
    db, *, office: Office, staff: Staff | None, weekday: int = 0, code: str = "A"
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(db, *, patient: Patient, course: Course, start: time, end: time) -> Visit:
    visit = Visit(
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
    db.add(visit)
    await db.flush()
    return visit


async def _seed_anchor_course(
    db, *, office: Office, staff: Staff, weekday: int = 0, code: str = "A", anchor_xy=NEAR
) -> Course:
    """指定曜日に「近接の既存訪問 1 件だけ」の開講コースを作る (空き枠が出る状態)."""
    course = await _seed_course(db, office=office, staff=staff, weekday=weekday, code=code)
    anchor = await _seed_patient(
        db,
        office=office,
        code=f"ANC{weekday}{code}{uuid4().hex[:4]}",
        lat=anchor_xy[0],
        lng=anchor_xy[1],
    )
    await _seed_visit(db, patient=anchor, course=course, start=time(9, 30), end=time(10, 0))
    return course


def _pool_wp(**overrides: Any) -> dict[str, Any]:
    """propose-slots が受ける weekly_pattern サマリ形式 (Mon 終日 30分)."""
    wp: dict[str, Any] = {
        "service_minutes": 30,
        "time_type": "終日",
        "preferred_weekdays": ["Mon"],
    }
    wp.update(overrides)
    return wp


def _propose_payload_for(patient: Patient, office: Office, wp: dict[str, Any]) -> dict[str, Any]:
    """pool 患者と同一の CandidateInput を生む propose-slots リクエストを組む (クロスチェック用)."""
    time_type = wp["time_type"]
    show_range = time_type in ("固定", "時間帯")
    show_end = time_type == "時間帯"
    return {
        "lat": float(patient.lat),
        "lng": float(patient.lng),
        "service_minutes": wp["service_minutes"],
        "time_type": time_type,
        "preferred_start": wp.get("preferred_start") if show_range else None,
        "preferred_end": wp.get("preferred_end") if show_end else None,
        "preferred_weekdays": wp["preferred_weekdays"],
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_ids": [str(office.id)],
        "existing_patient_id": str(patient.id),
        "limit": 50,
    }


# ---------------------------------------------------------------------------
# 1. クロスチェック (best_delta が propose-slots 先頭候補と一致)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_best_delta_matches_propose_slots(client, db) -> None:
    admin = await _make_user(db, email="po-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # 2 つの開講コース (A: NEAR, B: FAR) に既存 1 件ずつ.
    await _seed_anchor_course(db, office=office, staff=staff, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office, staff=staff, code="B", anchor_xy=FAR)
    # プール患者 2 名 (異なる座標で最良候補が変わる).
    wp = _pool_wp()
    p1 = await _seed_patient(
        db, office=office, code="POOL1", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    p2 = await _seed_patient(
        db, office=office, code="POOL2", lat=FAR[0], lng=FAR[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p1.id), str(p2.id)],
        },
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 2
    by_pid = {it["patient_id"]: it for it in items}

    for patient in (p1, p2):
        item = by_pid[str(patient.id)]
        assert item["candidate_count"] > 0, item
        assert item["best_slot"] is not None
        assert item["best_delta_minutes"] is not None

        # 個別 propose-slots を同一 CandidateInput で呼び、先頭候補と一致することを確認.
        pres = await client.post(
            "/api/v1/schedule/v2/propose-slots",
            headers=_bearer(admin),
            json=_propose_payload_for(patient, office, wp),
        )
        assert pres.status_code == 200, pres.text
        pslots = pres.json()["slots"]
        assert pslots, pslots
        top = pslots[0]
        # best_delta_minutes が propose-slots 先頭候補の marginal_cost_minutes と一致.
        assert item["best_delta_minutes"] == top["marginal_cost_minutes"], (item, top)
        # best_slot の座標/曜日/コースも先頭候補と一致 (start_time は HH:MM:SS).
        assert item["best_slot"]["weekday"] == top["weekday"]
        assert item["best_slot"]["course_code"] == top["course_code"]
        assert item["best_slot"]["office_id"] == top["office_id"]
        assert item["best_slot"]["start_time"][:5] == top["start_time"]
        # candidate_count は propose-slots の (効率代替なし) 候補総数と一致.
        assert item["candidate_count"] == len(pslots)


# ---------------------------------------------------------------------------
# 2. 候補 0 件の患者
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_zero_candidates_has_reason(client, db) -> None:
    """希望曜日に開講コースが無い → best_slot=null / candidate_count=0 / reason 非 null."""
    admin = await _make_user(db, email="po-admin2@example.com", role="admin")
    office, _staff = await _seed_office_staff(db)
    # コースを一切作らない → Mon 希望は course_closed で 0 件.
    wp = _pool_wp()
    p = await _seed_patient(
        db, office=office, code="POOLZ", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["best_slot"] is None
    assert item["best_delta_minutes"] is None
    assert item["candidate_count"] == 0
    assert item["top_excluded_reason"] is not None
    assert item["top_excluded_reason"] in {
        "capacity_full",
        "lunch_window",
        "travel_shortage",
        "no_gap",
        "course_closed",
    }


@pytest.mark.asyncio
async def test_pool_overview_full_course_reason_capacity_full(client, db) -> None:
    """満杯コース (6 名) に異住所候補 → 0 件で top_excluded_reason=capacity_full."""
    admin = await _make_user(db, email="po-admin2b@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    full_course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    starts = [time(9, 30), time(10, 10), time(10, 50), time(13, 0), time(13, 40), time(14, 20)]
    for i, st in enumerate(starts):
        lat = BASE[0] + 0.02 * (i + 1)
        lng = BASE[1] + 0.02 * (i + 1)
        fp = await _seed_patient(db, office=office, code=f"FULL{i}", lat=lat, lng=lng)
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=fp, course=full_course, start=st, end=end)
    # 満杯コースに異住所 (FAR) の候補 → 入らない.
    wp = _pool_wp()
    p = await _seed_patient(
        db, office=office, code="POOLF", lat=FAR[0], lng=FAR[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["candidate_count"] == 0
    assert item["best_slot"] is None
    assert item["top_excluded_reason"] == "capacity_full"


# ---------------------------------------------------------------------------
# 3. サイズガード (51 件で 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_size_guard_422(client, db) -> None:
    admin = await _make_user(db, email="po-admin3@example.com", role="admin")
    office, _staff = await _seed_office_staff(db)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [str(uuid4()) for _ in range(51)],
        },
    )
    assert res.status_code == 422, res.text
    # メッセージにガード値 (50) が明記される.
    assert "50" in res.text


# ---------------------------------------------------------------------------
# 4. 空 patient_ids → items=[]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_empty_patient_ids_returns_empty(client, db) -> None:
    admin = await _make_user(db, email="po-admin4@example.com", role="admin")
    office, _staff = await _seed_office_staff(db)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office.id),
            "patient_ids": [],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["items"] == []


# ---------------------------------------------------------------------------
# 5. office_id フィルタ (別拠点のコースが候補に出ない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_office_filter(client, db) -> None:
    admin = await _make_user(db, email="po-admin5@example.com", role="admin")
    office_a, staff_a = await _seed_office_staff(db, name="稲", code="INAGE")
    office_b, staff_b = await _seed_office_staff(db, name="都賀", code="TSUGA")
    # 両拠点に近接アンカーコース (同一 geometry) を作る.
    await _seed_anchor_course(db, office=office_a, staff=staff_a, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office_b, staff=staff_b, code="A", anchor_xy=NEAR)
    wp = _pool_wp()
    p = await _seed_patient(
        db, office=office_a, code="POOLO", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    # office_id=A → A のコースのみ候補になる (B は出ない).
    res_a = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office_a.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res_a.status_code == 200, res_a.text
    item_a = res_a.json()["items"][0]
    assert item_a["best_slot"] is not None
    assert item_a["best_slot"]["office_id"] == str(office_a.id)
    count_a = item_a["candidate_count"]

    # office_id=B → B のコースのみ.
    res_b = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office_b.id),
            "patient_ids": [str(p.id)],
        },
    )
    assert res_b.status_code == 200, res_b.text
    item_b = res_b.json()["items"][0]
    assert item_b["best_slot"]["office_id"] == str(office_b.id)

    # office_id=None (全拠点) → 両拠点のコースが候補になり件数が増える.
    res_all = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": None,
            "patient_ids": [str(p.id)],
        },
    )
    assert res_all.status_code == 200, res_all.text
    item_all = res_all.json()["items"][0]
    assert item_all["candidate_count"] > count_a


# ---------------------------------------------------------------------------
# 6. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_overview_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="po-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(staff_user),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "patient_ids": []},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_pool_overview_rejects_no_auth(client, db) -> None:
    res = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "patient_ids": []},
    )
    assert res.status_code == 401
