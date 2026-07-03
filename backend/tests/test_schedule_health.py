"""Tests for GET /api/v1/schedule/v2/schedule-health (schedule-advisor Phase 1「診る」).

固定スケジュールの「移動 / 隙間の無駄」を週次・拠点別・曜日別・コース別に集計して
返す read-only エンドポイント. 効果の物差しは提案エンジンと同一の travel モデル
(``auto_allocator_v2`` の haversine / _address_bucket / config.speed & buffer).

ローカル SQLite のみ (本番 DB 禁止).

座標メモ (千葉市近辺, test_propose_slots_api.py と整合):
    BASE = (35.6000, 140.1000)
    NEAR = (35.6010, 140.1010)  → BASE から ~0.1433km (異住所 / travel 1分@20km/h)
    FAR  = (35.6300, 140.1400)  → BASE から ~4.9196km (異住所 / travel 15分@20km/h, 7分@40km/h)
    SAME = (35.60005, 140.10005) → BASE と同住所 (_address_bucket 一致, travel/buffer=0)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.scheduling_settings import SchedulingSettings
from app.models.staff import Staff
from app.models.visit import (
    VISIT_STATUS_CANCELLED,
    VISIT_STATUS_COMPLETED,
    VISIT_STATUS_IN_PROGRESS,
    VISIT_STATUS_PLANNED,
    Visit,
)

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)
SAME = (35.60005, 140.10005)


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
    db, *, name: str = "稲", code: str | None = "INAGE", staff_name: str = "担当看護師"
) -> tuple[Office, Staff]:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    staff = Staff(name=staff_name, role="staff", is_trainee=False, primary_office_id=office.id)
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
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        primary_office_id=office.id,
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


async def _seed_visit(
    db,
    *,
    patient: Patient,
    course: Course,
    start: time,
    end: time,
    status: str = VISIT_STATUS_PLANNED,
    deleted: bool = False,
) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=WEEK_MONDAY,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    if deleted:
        from datetime import UTC, datetime

        visit.deleted_at = datetime.now(UTC)
    db.add(visit)
    await db.flush()
    return visit


def _params(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK}
    body.update(overrides)
    return body


async def _get(client, user: User, **params: Any) -> Any:
    return await client.get(
        "/api/v1/schedule/v2/schedule-health",
        headers=_bearer(user),
        params=_params(**params),
    )


def _only_course(body: dict[str, Any]) -> dict[str, Any]:
    """単一拠点・単一曜日・単一コースを想定したレスポンスから course を取り出す."""
    assert len(body["offices"]) == 1, body
    office = body["offices"][0]
    assert len(office["weekdays"]) == 1, office
    wd = office["weekdays"][0]
    assert len(wd["courses"]) == 1, wd
    return wd["courses"][0]


# ---------------------------------------------------------------------------
# 集計の正しさ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_visits_cross_address_metrics(client, db) -> None:
    """異住所 2 訪問: travel/buffer/gap が手計算と一致.

    V1 BASE 09:00-09:30 → V2 FAR 10:30-11:00 (連続).
    haversine_km(BASE, FAR) = 4.9196km → round(,1)=4.9km.
    travel = haversine_minutes(4.9196, 20km/h) = round(4.9196/20*60)=15 分.
    buffer = config.visit_buffer_min (既定 8).
    gap = max(0, 10:30 - 09:30 - travel - buffer) = max(0, 60 - 15 - 8) = 37 分.
    service = 30 + 30 = 60 分. visit=2, patient=2.
    """
    admin = await _make_user(db, email="sh-cross@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="C1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="C2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(10, 30), end=time(11, 0))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    body = res.json()
    c = _only_course(body)
    assert c["course_code"] == "A"
    assert c["staff_name"] == "担当看護師"
    assert c["visit_count"] == 2
    assert c["patient_count"] == 2
    assert c["service_minutes"] == 60
    assert c["travel_minutes"] == 15
    assert c["travel_km"] == 4.9
    assert c["buffer_minutes"] == 8
    assert c["gap_minutes"] == 37
    # 曜日小計・週小計も同値 (単一コースのため).
    wd_totals = body["offices"][0]["weekdays"][0]["totals"]
    assert wd_totals == {
        "visit_count": 2,
        "service_minutes": 60,
        "travel_minutes": 15,
        "travel_km": 4.9,
        "buffer_minutes": 8,
        "gap_minutes": 37,
    }
    assert body["offices"][0]["week_totals"] == wd_totals


@pytest.mark.asyncio
async def test_same_address_zero_travel_buffer(client, db) -> None:
    """同住所連続: travel=0 / travel_km=0 / buffer=0 (_address_bucket 一致).

    V1 BASE 09:00-09:30 → V2 SAME 09:40-10:10 (同住所, 100m 以内).
    gap = max(0, 09:40 - 09:30 - 0 - 0) = 10 分.
    """
    admin = await _make_user(db, email="sh-same@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="S1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="S2", lat=SAME[0], lng=SAME[1])
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(9, 40), end=time(10, 10))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["travel_minutes"] == 0
    assert c["travel_km"] == 0.0
    assert c["buffer_minutes"] == 0
    assert c["gap_minutes"] == 10


@pytest.mark.asyncio
async def test_gap_clamped_to_zero_when_packed(client, db) -> None:
    """詰まった異住所連続では gap が負になるが 0 にクランプされる.

    V1 BASE 09:00-09:30 → V2 FAR 09:35-10:05.
    travel=15, buffer=8. gap_raw = 09:35 - 09:30 - 15 - 8 = 5 - 23 = -18 → 0.
    travel/buffer は依然計上される.
    """
    admin = await _make_user(db, email="sh-gap@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="G1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="G2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(9, 35), end=time(10, 5))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["gap_minutes"] == 0
    assert c["travel_minutes"] == 15
    assert c["buffer_minutes"] == 8


@pytest.mark.asyncio
async def test_status_filter_excludes_cancelled_and_deleted(client, db) -> None:
    """cancelled / deleted は除外, in_progress / completed は含む."""
    admin = await _make_user(db, email="sh-status@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    # 含む: completed + in_progress (異住所連続).
    p1 = await _seed_patient(db, office=office, code="ST1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="ST2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(
        db,
        patient=p1,
        course=course,
        start=time(9, 0),
        end=time(9, 30),
        status=VISIT_STATUS_COMPLETED,
    )
    await _seed_visit(
        db,
        patient=p2,
        course=course,
        start=time(10, 30),
        end=time(11, 0),
        status=VISIT_STATUS_IN_PROGRESS,
    )
    # 除外: cancelled + deleted.
    p3 = await _seed_patient(db, office=office, code="ST3", lat=NEAR[0], lng=NEAR[1])
    p4 = await _seed_patient(db, office=office, code="ST4", lat=NEAR[0], lng=NEAR[1])
    await _seed_visit(
        db,
        patient=p3,
        course=course,
        start=time(14, 0),
        end=time(14, 30),
        status=VISIT_STATUS_CANCELLED,
    )
    await _seed_visit(
        db,
        patient=p4,
        course=course,
        start=time(15, 0),
        end=time(15, 30),
        status=VISIT_STATUS_PLANNED,
        deleted=True,
    )
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    # completed + in_progress の 2 件のみ.
    assert c["visit_count"] == 2
    assert c["patient_count"] == 2
    assert c["service_minutes"] == 60
    assert c["travel_minutes"] == 15  # BASE→FAR のみ (cancelled/deleted は遷移対象外).


@pytest.mark.asyncio
async def test_office_id_filter(client, db) -> None:
    """office_id 指定で対象拠点のみ集計する."""
    admin = await _make_user(db, email="sh-officefilter@example.com", role="admin")
    office_a, staff_a = await _seed_office_staff(db, name="稲", code="INAGE")
    office_b, staff_b = await _seed_office_staff(db, name="津", code="TSUGA")
    for office, staff, tag in ((office_a, staff_a, "A"), (office_b, staff_b, "B")):
        course = await _seed_course(db, office=office, staff=staff)
        p = await _seed_patient(db, office=office, code=f"OF{tag}", lat=BASE[0], lng=BASE[1])
        await _seed_visit(db, patient=p, course=course, start=time(9, 0), end=time(9, 30))
    await db.commit()

    res = await _get(client, admin, office_id=str(office_a.id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["offices"]) == 1
    assert body["offices"][0]["office_id"] == str(office_a.id)
    assert body["offices"][0]["office_name"] == "稲"

    # 未指定なら両拠点.
    res_all = await _get(client, admin)
    assert res_all.status_code == 200
    assert len(res_all.json()["offices"]) == 2


@pytest.mark.asyncio
async def test_empty_week_returns_empty_offices(client, db) -> None:
    """対象週に visit が無ければ offices=[] で 200."""
    admin = await _make_user(db, email="sh-empty@example.com", role="admin")
    office, _ = await _seed_office_staff(db)
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["offices"] == []
    assert body["iso_year"] == ISO_YEAR
    assert body["iso_week"] == ISO_WEEK


@pytest.mark.asyncio
async def test_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="sh-staff@example.com", role="staff")
    res = await _get(client, staff_user)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_rejects_no_auth(client, db) -> None:
    res = await client.get(
        "/api/v1/schedule/v2/schedule-health",
        params=_params(),
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_config_override_reflects_buffer_and_speed(client, db) -> None:
    """SchedulingSettings で buffer / speed 変更が集計に反映される.

    既定 (buffer=8 / speed=20): 異住所 BASE→FAR で travel=15, buffer=8.
    上書き (buffer=20 / speed=40): travel=haversine_minutes(4.9196, 40)=7, buffer=20.
    """
    admin = await _make_user(db, email="sh-config@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="CF1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="CF2", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(10, 30), end=time(11, 0))
    db.add(
        SchedulingSettings(
            is_singleton=True,
            visit_buffer_min=20,
            travel_speed_kmh=40,
        )
    )
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["buffer_minutes"] == 20
    assert c["travel_minutes"] == 7
    # travel_km は距離なので config に依らず不変.
    assert c["travel_km"] == 4.9
    # gap = max(0, 60 - 7 - 20) = 33.
    assert c["gap_minutes"] == 33


@pytest.mark.asyncio
async def test_single_visit_no_travel(client, db) -> None:
    """1 visit のみのコース → travel/buffer/gap = 0 (連続ペアが存在しない)."""
    admin = await _make_user(db, email="sh-single@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p = await _seed_patient(db, office=office, code="SV1", lat=BASE[0], lng=BASE[1])
    await _seed_visit(db, patient=p, course=course, start=time(9, 0), end=time(9, 30))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["visit_count"] == 1
    assert c["service_minutes"] == 30
    assert c["travel_minutes"] == 0
    assert c["travel_km"] == 0.0
    assert c["buffer_minutes"] == 0
    assert c["gap_minutes"] == 0


@pytest.mark.asyncio
async def test_same_start_time_two_visits_no_exception(client, db) -> None:
    """同一 start_time の 2 visit → visit_count=2 で例外なく集計できる.

    start_time が同じ場合、sorted() 後に隣接ペアとして計算される。
    gap_raw = next.start - prev.end - travel - buffer は負値になり 0 にクランプされる。
    service_minutes はそれぞれ独立に計上 (合計 60 分)。
    """
    admin = await _make_user(db, email="sh-sametime@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="TM1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="TM2", lat=FAR[0], lng=FAR[1])
    # 同時刻 09:00 スタート (異住所).
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(9, 0), end=time(9, 30))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["visit_count"] == 2
    assert c["patient_count"] == 2
    assert c["service_minutes"] == 60
    # gap_raw = 09:00 - 09:30 - travel - buffer < 0 → クランプ = 0.
    assert c["gap_minutes"] == 0


@pytest.mark.asyncio
async def test_missing_coords_transition_zero_travel_buffer_config(client, db) -> None:
    """座標欠損患者を含む遷移: travel=0 だが buffer=config値, visit はレスポンスに含む.

    V1 BASE 09:00-09:30 → V2 (座標 None) 10:00-10:30.
    距離算出不能 → travel=0, buffer=8 (config既定). visit_count=2 (除外しない).
    gap = max(0, 10:00 - 09:30 - 0 - 8) = max(0, 30 - 8) = 22.
    """
    admin = await _make_user(db, email="sh-nocoord@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="NC1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="NC2", lat=None, lng=None)
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await _get(client, admin, office_id=str(office.id))
    assert res.status_code == 200, res.text
    c = _only_course(res.json())
    assert c["visit_count"] == 2  # 座標欠損でも除外しない.
    assert c["travel_minutes"] == 0
    assert c["travel_km"] == 0.0
    assert c["buffer_minutes"] == 8
    assert c["gap_minutes"] == 22


# ---------------------------------------------------------------------------
# 見直しどきトレンド (schedule-advisor Phase 3「見直しどき通知」)
#   GET /api/v1/schedule/v2/schedule-health/trend
#   指定週から遡る週次の office 横断合計を古→新順で返す (劣化判定は FE).
# ---------------------------------------------------------------------------

TREND_PATH = "/api/v1/schedule/v2/schedule-health/trend"


async def _seed_course_at(
    db, *, office, staff, iso_year: int, iso_week: int, weekday: int = 0, code: str = "A"
) -> Course:
    course = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit_at(
    db,
    *,
    patient,
    course,
    visit_date,
    start: time,
    end: time,
    status: str = VISIT_STATUS_PLANNED,
) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(visit)
    await db.flush()
    return visit


async def _seed_cross_pair_week(
    db, *, office, staff, iso_year: int, iso_week: int, tag: str
) -> None:
    """指定 ISO 週に BASE→FAR の異住所 2 訪問を 1 コース分投入する.

    期待メトリクス: travel_minutes=15, travel_km=4.9, gap=37, visit_count=2.
    """
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    course = await _seed_course_at(
        db, office=office, staff=staff, iso_year=iso_year, iso_week=iso_week
    )
    p1 = await _seed_patient(db, office=office, code=f"{tag}-1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code=f"{tag}-2", lat=FAR[0], lng=FAR[1])
    await _seed_visit_at(
        db, patient=p1, course=course, visit_date=monday, start=time(9, 0), end=time(9, 30)
    )
    await _seed_visit_at(
        db, patient=p2, course=course, visit_date=monday, start=time(10, 30), end=time(11, 0)
    )


async def _trend(client, user: User, **params: Any) -> Any:
    body: dict[str, Any] = {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK}
    body.update(params)
    return await client.get(TREND_PATH, headers=_bearer(user), params=body)


@pytest.mark.asyncio
async def test_trend_walks_back_weeks_old_to_new(client, db) -> None:
    """weeks=3 で指定週から遡り、古→新順に3週返す (空週は totals 全0)."""
    admin = await _make_user(db, email="tr-walk@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # 指定週 (W20) と前週 (W19) に visit を入れ、W18 は空にする.
    await _seed_cross_pair_week(
        db, office=office, staff=staff, iso_year=ISO_YEAR, iso_week=ISO_WEEK, tag="W20"
    )
    await _seed_cross_pair_week(
        db, office=office, staff=staff, iso_year=ISO_YEAR, iso_week=ISO_WEEK - 1, tag="W19"
    )
    await db.commit()

    res = await _trend(client, admin, office_id=str(office.id), weeks=3)
    assert res.status_code == 200, res.text
    weeks = res.json()["weeks"]
    assert len(weeks) == 3
    # 古→新: [W18, W19, W20].
    assert [w["iso_week"] for w in weeks] == [ISO_WEEK - 2, ISO_WEEK - 1, ISO_WEEK]
    # 最古週 (W18) は visit なし → totals 全 0.
    assert weeks[0]["totals"] == {
        "visit_count": 0,
        "travel_minutes": 0,
        "travel_km": 0.0,
        "gap_minutes": 0,
    }
    # W19 / W20 は同一メトリクス.
    for w in weeks[1:]:
        assert w["totals"] == {
            "visit_count": 2,
            "travel_minutes": 15,
            "travel_km": 4.9,
            "gap_minutes": 37,
        }


@pytest.mark.asyncio
async def test_trend_empty_weeks_all_zero(client, db) -> None:
    """visit ゼロでも weeks 数だけ totals 全 0 の週が古→新順で返る."""
    admin = await _make_user(db, email="tr-empty@example.com", role="admin")
    office, _ = await _seed_office_staff(db)
    await db.commit()

    res = await _trend(client, admin, office_id=str(office.id), weeks=4)
    assert res.status_code == 200, res.text
    weeks = res.json()["weeks"]
    assert len(weeks) == 4
    assert [w["iso_week"] for w in weeks] == [ISO_WEEK - 3, ISO_WEEK - 2, ISO_WEEK - 1, ISO_WEEK]
    for w in weeks:
        assert w["totals"] == {
            "visit_count": 0,
            "travel_minutes": 0,
            "travel_km": 0.0,
            "gap_minutes": 0,
        }


@pytest.mark.asyncio
async def test_trend_year_crossing(client, db) -> None:
    """年跨ぎ: 2026-W01 から遡ると前週は 2025-W52 / W51 として導出される."""
    admin = await _make_user(db, email="tr-yearcross@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # 前年 2025-W52 に visit を入れる.
    await _seed_cross_pair_week(
        db, office=office, staff=staff, iso_year=2025, iso_week=52, tag="Y52"
    )
    await db.commit()

    res = await _trend(client, admin, office_id=str(office.id), iso_year=2026, iso_week=1, weeks=3)
    assert res.status_code == 200, res.text
    weeks = res.json()["weeks"]
    assert len(weeks) == 3
    # 古→新: [2025-W51, 2025-W52, 2026-W01].
    assert [(w["iso_year"], w["iso_week"]) for w in weeks] == [
        (2025, 51),
        (2025, 52),
        (2026, 1),
    ]
    assert weeks[1]["totals"]["visit_count"] == 2  # 2025-W52 に visit.
    assert weeks[1]["totals"]["travel_minutes"] == 15
    assert weeks[2]["totals"]["visit_count"] == 0  # 2026-W01 は空.


@pytest.mark.asyncio
async def test_trend_sums_across_offices(client, db) -> None:
    """office_id 未指定なら全拠点の week_totals を横断合算する."""
    admin = await _make_user(db, email="tr-crossoffice@example.com", role="admin")
    office_a, staff_a = await _seed_office_staff(db, name="稲", code="INAGE")
    office_b, staff_b = await _seed_office_staff(db, name="津", code="TSUGA")
    await _seed_cross_pair_week(
        db, office=office_a, staff=staff_a, iso_year=ISO_YEAR, iso_week=ISO_WEEK, tag="OA"
    )
    await _seed_cross_pair_week(
        db, office=office_b, staff=staff_b, iso_year=ISO_YEAR, iso_week=ISO_WEEK, tag="OB"
    )
    await db.commit()

    res = await _trend(client, admin, weeks=1)
    assert res.status_code == 200, res.text
    weeks = res.json()["weeks"]
    assert len(weeks) == 1
    # 2 拠点分を横断合算.
    assert weeks[0]["totals"] == {
        "visit_count": 4,
        "travel_minutes": 30,
        "travel_km": 9.8,
        "gap_minutes": 74,
    }


@pytest.mark.asyncio
async def test_trend_weeks_clamped_to_max(client, db) -> None:
    """weeks 上限は 12. Query 上限 (le=12) で 12 週返る."""
    admin = await _make_user(db, email="tr-clamp@example.com", role="admin")
    office, _ = await _seed_office_staff(db)
    await db.commit()

    res = await _trend(client, admin, office_id=str(office.id), weeks=12)
    assert res.status_code == 200, res.text
    assert len(res.json()["weeks"]) == 12
    # 上限超過はバリデーションで 422.
    res_over = await _trend(client, admin, office_id=str(office.id), weeks=13)
    assert res_over.status_code == 422


@pytest.mark.asyncio
async def test_trend_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="tr-staff@example.com", role="staff")
    res = await _trend(client, staff_user)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_trend_rejects_no_auth(client, db) -> None:
    res = await client.get(TREND_PATH, params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK})
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# H1: 原因ドリルダウン (GET /v2/schedule-health/course-detail)
# ---------------------------------------------------------------------------


async def _get_detail(client, user: User, office, course_code: str = "A") -> Any:
    return await client.get(
        "/api/v1/schedule/v2/schedule-health/course-detail",
        headers=_bearer(user),
        params=_params(office_id=str(office.id), course_code=course_code),
    )


@pytest.mark.asyncio
async def test_course_detail_transitions_and_patient_costs(client, db) -> None:
    """遷移内訳と患者別配置コストを返す (物差しは健康診断と同一・厳密限界コスト).

    構成: BASE — FAR — BASE2(BASEと同住所)。
      - 遷移: BASE→FAR (重い) / FAR→BASE2 (重い) の 2 件。
      - 配置コスト: FAR の患者が最大 (抜くと BASE→BASE2 が同住所直結で全額浮く)。
    """
    admin = await _make_user(db, email="cd-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="D1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="D2", lat=FAR[0], lng=FAR[1], name="遠方 太郎")
    p3 = await _seed_patient(db, office=office, code="D3", lat=SAME[0], lng=SAME[1])
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(10, 30), end=time(11, 0))
    await _seed_visit(db, patient=p3, course=course, start=time(12, 30), end=time(13, 0))
    await db.commit()

    res = await _get_detail(client, admin, office)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["course_code"] == "A"
    assert len(data["weekdays"]) == 1
    wd = data["weekdays"][0]
    assert wd["weekday"] == 0
    assert wd["staff_name"] == "担当看護師"

    # 遷移 2 件 (BASE→FAR / FAR→BASE2)。どちらも異住所で travel > 0。
    trs = wd["transitions"]
    assert len(trs) == 2
    assert trs[0]["to_patient_name"] == "遠方 太郎"
    assert trs[0]["travel_minutes"] > 0 and trs[1]["travel_minutes"] > 0
    # 曜日合計 = 遷移の和。
    assert wd["totals"]["travel_minutes"] == sum(t["travel_minutes"] for t in trs)

    # 配置コスト: FAR 患者が最大 (BASE と BASE2 は同住所なので直結で 0 になる)。
    costs = wd["patient_costs"]
    assert costs[0]["patient_name"] == "遠方 太郎"
    assert (
        costs[0]["marginal_minutes"] == wd["totals"]["travel_minutes"] + 2 * 8
    )  # 移動2辺+buffer2辺
    assert all(costs[0]["marginal_minutes"] >= c["marginal_minutes"] for c in costs)


@pytest.mark.asyncio
async def test_course_detail_missing_coords_excluded_from_costs(client, db) -> None:
    """座標欠損の患者は遷移では travel 0、配置コストのランキング対象外."""
    admin = await _make_user(db, email="cd-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff)
    p1 = await _seed_patient(db, office=office, code="M1", lat=BASE[0], lng=BASE[1])
    p2 = await _seed_patient(db, office=office, code="M2", lat=None, lng=None, name="座標 なし")
    await _seed_visit(db, patient=p1, course=course, start=time(9, 0), end=time(9, 30))
    await _seed_visit(db, patient=p2, course=course, start=time(10, 0), end=time(10, 30))
    await db.commit()

    res = await _get_detail(client, admin, office)
    assert res.status_code == 200
    wd = res.json()["weekdays"][0]
    assert len(wd["transitions"]) == 1
    assert wd["transitions"][0]["travel_minutes"] == 0  # 座標欠損 → travel 0 (健康診断規約)
    names = [c["patient_name"] for c in wd["patient_costs"]]
    assert "座標 なし" not in names


@pytest.mark.asyncio
async def test_course_detail_unknown_course_returns_empty(client, db) -> None:
    admin = await _make_user(db, email="cd-admin3@example.com", role="admin")
    office, _staff = await _seed_office_staff(db)
    await db.commit()
    res = await _get_detail(client, admin, office, course_code="Z")
    assert res.status_code == 200
    assert res.json()["weekdays"] == []


@pytest.mark.asyncio
async def test_course_detail_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="cd-staff1@example.com", role="staff")
    office, _staff = await _seed_office_staff(db)
    await db.commit()
    res = await _get_detail(client, staff_user, office)
    assert res.status_code == 403
