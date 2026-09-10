"""患者ステータス連動 Phase 3 — 表示の保険 (§3-4) と カイポケ突合 (§3-5)。

正典 = ``docs/plans/patient-status-schedule-design-2026-09-09.md`` §3-4 / §3-5
＋ Phase 3 レビュー決定 (2026-09-10)。

守りたい性質:
    A. 訪問 DTO (一覧 / 盤面 / モニター) が ``patient_status`` と ``source`` を載せる
       (稼働中も非稼働も。FE が「入院中なのに予定がある」をバッジで出せる)。
    B. **非稼働患者の予定を engine から隠さない**。取消されずに残っている planned は
       ⭐ 配置 (PO が「残す」と決めたもの) か実績で、実際に枠を占有している。
       落とすと重なりが見えず二重予約を許す (レビュー BLOCKER)。除外するのは
       「新しく入れる候補」だけ = ``pool_bulk_inserter``。
    C. カイポケ突合は「非稼働 × カイポケのみ × 今日以降」だけを削除候補に出す。
       過去日 (実績) と 一致/相違 (らく助にも実体がある) は従来判定のまま。
    D. ●未送信サマリが「◯◯様 入院中の取消 N 件」(``inactive_groups``・総数と
       送れる件数) と残骸 (``inactive_residue``・⭐ 除外・拠点スコープ) を返す。
    E. カイポケ送信 CSV (csv_builder) は **非稼働でも落とさない** (実績と ⭐ を守る)。

ローカル SQLite のみ (本番 DB 禁止)。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.special_visit import (
    MARK_STATUS_PLACED,
    PERIOD_STATUS_ACTIVE,
    SpecialVisitMark,
    SpecialVisitPeriod,
)
from app.models.staff import Staff, StaffShift
from app.models.visit import VISIT_STATUS_PLANNED, Visit

# 非稼働の代表値 (ラベルは Phase 1 のテストで網羅済み)。
INACTIVE = "admitted"
INACTIVE_LABEL = "入院中"

# 過去の固定週 (DTO / engine の構造検証用。日付の意味は問わない)。
ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # 2026-05-11 (Mon)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)


def _jst_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def _future_monday() -> date:
    """月をまたがない未来週の月曜 (未送信/突合は月跨ぎをフェイルクローズする)。"""
    today = _jst_today()
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    while (monday + timedelta(days=6)).month != monday.month:
        monday += timedelta(days=7)
    return monday


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_office(db, *, name: str = "稲毛", code: str | None = "INAGE") -> Office:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    return office


async def _seed_staff(db, *, office: Office, name: str = "看護A") -> Staff:
    staff = Staff(
        name=name, role="staff", is_trainee=False, status="active", primary_office_id=office.id
    )
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=wd < 5))
    await db.flush()
    return staff


async def _seed_patient(
    db,
    *,
    office: Office | None,
    code: str,
    status: str = "active",
    lat: float = BASE[0],
    lng: float = BASE[1],
    name: str | None = None,
    deleted: bool = False,
    status_changed_at: datetime | None = None,
) -> Patient:
    p = Patient(
        code=code,
        name=name or f"P-{code}",
        status=status,
        insurance="medical",
        lat=lat,
        lng=lng,
        primary_office_id=office.id if office is not None else None,
        status_changed_at=status_changed_at,
    )
    if deleted:
        p.deleted_at = datetime.now(ZoneInfo("UTC")).replace(tzinfo=None)
    db.add(p)
    await db.flush()
    return p


async def _seed_course(db, *, office: Office, staff: Staff, weekday: int = 0, code: str = "A"):
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    return course


async def _seed_visit(
    db,
    *,
    patient: Patient,
    course: Course | None = None,
    staff: Staff | None = None,
    visit_date: date = WEEK_MONDAY,
    start: time = time(9, 30),
    end: time = time(10, 5),
    source: str = "auto",
    status: str = VISIT_STATUS_PLANNED,
) -> Visit:
    visit = Visit(
        patient_id=patient.id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        source=source,
        required_staff_count=1,
        course_id=course.id if course is not None else None,
        primary_staff_id=(
            staff.id if staff is not None else (course.assigned_staff_id if course else None)
        ),
    )
    db.add(visit)
    await db.flush()
    return visit


async def _seed_star_placement(db, *, patient: Patient, visit: Visit) -> SpecialVisitMark:
    """⭐ 特別訪問週間の「配置済み ●」を作る (visit と紐づける)。"""
    period = SpecialVisitPeriod(
        patient_id=patient.id,
        start_date=visit.visit_date - timedelta(days=7),
        end_date=visit.visit_date + timedelta(days=7),
        weekly_target=3,
        status=PERIOD_STATUS_ACTIVE,
    )
    db.add(period)
    await db.flush()
    iso_y, iso_w, _ = visit.visit_date.isocalendar()
    mark = SpecialVisitMark(
        period_id=period.id,
        patient_id=patient.id,
        iso_year=iso_y,
        iso_week=iso_w,
        # ○ は月〜土のみ (ck_svm_weekday)。日曜の訪問でも制約に触れないよう丸める
        # (この helper が確かめたいのは placed_visit_id の紐付けだけ)。
        weekday=min(visit.visit_date.weekday(), 5),
        kind="extra",
        status=MARK_STATUS_PLACED,
        placed_visit_id=visit.id,
    )
    db.add(mark)
    await db.flush()
    return mark


def _find_cell(body: dict[str, Any], office_id: str, weekday: int) -> dict[str, Any]:
    for c in body["board"]:
        if c["office_id"] == office_id and c["weekday"] == weekday:
            return c
    raise AssertionError(f"cell not found office={office_id} weekday={weekday}")


# ---------------------------------------------------------------------------
# A. DTO に patient_status / source が載る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visits_list_dto_carries_patient_status(client, db) -> None:
    admin = await _make_user(db, email="p3-visits@example.com")
    office = await _seed_office(db)
    staff = await _seed_staff(db, office=office)
    active = await _seed_patient(db, office=office, code="P3V-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3V-B", status=INACTIVE)
    await _seed_visit(db, patient=active, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=admitted, staff=staff, start=time(11, 0), end=time(11, 35))
    await db.commit()

    res = await client.get(
        "/api/v1/visits",
        headers=_bearer(admin),
        params={"date_from": WEEK_MONDAY.isoformat(), "date_to": WEEK_MONDAY.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"] if isinstance(body, dict) else body
    by_patient = {it["patient_id"]: it for it in items}
    assert by_patient[str(active.id)]["patient_status"] == "active"
    assert by_patient[str(admitted.id)]["patient_status"] == INACTIVE


@pytest.mark.asyncio
async def test_board_dto_carries_source_and_patient_status(client, db) -> None:
    admin = await _make_user(db, email="p3-board@example.com")
    office = await _seed_office(db)
    staff = await _seed_staff(db, office=office)
    course = await _seed_course(db, office=office, staff=staff)
    active = await _seed_patient(db, office=office, code="P3B-A", status="active")
    admitted = await _seed_patient(
        db, office=office, code="P3B-B", status=INACTIVE, lat=NEAR[0], lng=NEAR[1]
    )
    await _seed_visit(db, patient=active, course=course, start=time(9, 0), end=time(9, 35))
    await _seed_visit(
        db,
        patient=admitted,
        course=course,
        start=time(11, 0),
        end=time(11, 35),
        source="manual_week",
    )
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    cell = _find_cell(res.json(), str(office.id), 0)
    visits = cell["courses"][0]["visits"]
    by_patient = {v["patient_id"]: v for v in visits}
    assert by_patient[str(active.id)]["patient_status"] == "active"
    assert by_patient[str(active.id)]["source"] == "auto"
    assert by_patient[str(admitted.id)]["patient_status"] == INACTIVE
    assert by_patient[str(admitted.id)]["source"] == "manual_week"


@pytest.mark.asyncio
async def test_monitor_dto_carries_source_and_patient_status(db) -> None:
    from app.services.checkin.monitor import build_monitor

    office = await _seed_office(db, name="都賀", code="TSUGA")
    staff = await _seed_staff(db, office=office, name="看護M")
    active = await _seed_patient(db, office=office, code="P3M-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3M-B", status=INACTIVE)
    await _seed_visit(db, patient=active, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(
        db, patient=admitted, staff=staff, start=time(11, 0), end=time(11, 35), source="manual_week"
    )
    await db.commit()

    resp = await build_monitor(db, WEEK_MONDAY)
    seen = {str(mv.patient_id): mv for row in resp.staff for mv in row.visits}
    assert seen[str(active.id)].patient_status == "active"
    assert seen[str(admitted.id)].patient_status == INACTIVE
    assert seen[str(admitted.id)].source == "manual_week"


# ---------------------------------------------------------------------------
# A-2. DTO に patient_status_since (ステータスを変えた日・JST) が載る
#      PO フィードバック 2026-09-10: 「入院中」バッジはこの日以降の予定にだけ。
# ---------------------------------------------------------------------------

#: UTC 23:00 = JST 翌日 08:00。日付境界を跨ぐ値で「JST 日付である」ことを示す。
CHANGED_AT_UTC = datetime(2026, 9, 7, 23, 0, tzinfo=ZoneInfo("UTC"))
CHANGED_AT_JST_DATE = date(2026, 9, 8)


@pytest.mark.asyncio
async def test_visits_list_dto_carries_patient_status_since(client, db) -> None:
    admin = await _make_user(db, email="p3-since-visits@example.com")
    office = await _seed_office(db)
    staff = await _seed_staff(db, office=office)
    known = await _seed_patient(
        db,
        office=office,
        code="P3S-A",
        status=INACTIVE,
        status_changed_at=CHANGED_AT_UTC,
    )
    # mig 0082 以前に変えられた行 = 起点日が分からない (FE は「今日」に倒す)。
    legacy = await _seed_patient(db, office=office, code="P3S-B", status=INACTIVE)
    await _seed_visit(db, patient=known, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=legacy, staff=staff, start=time(11, 0), end=time(11, 35))
    await db.commit()

    res = await client.get(
        "/api/v1/visits",
        headers=_bearer(admin),
        params={"date_from": WEEK_MONDAY.isoformat(), "date_to": WEEK_MONDAY.isoformat()},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    items = body["items"] if isinstance(body, dict) else body
    by_patient = {it["patient_id"]: it for it in items}
    assert by_patient[str(known.id)]["patient_status_since"] == CHANGED_AT_JST_DATE.isoformat()
    assert by_patient[str(legacy.id)]["patient_status_since"] is None


@pytest.mark.asyncio
async def test_board_dto_carries_patient_status_since(client, db) -> None:
    admin = await _make_user(db, email="p3-since-board@example.com")
    office = await _seed_office(db)
    staff = await _seed_staff(db, office=office)
    course = await _seed_course(db, office=office, staff=staff)
    known = await _seed_patient(
        db,
        office=office,
        code="P3SB-A",
        status=INACTIVE,
        status_changed_at=CHANGED_AT_UTC,
    )
    legacy = await _seed_patient(
        db, office=office, code="P3SB-B", status=INACTIVE, lat=NEAR[0], lng=NEAR[1]
    )
    await _seed_visit(db, patient=known, course=course, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=legacy, course=course, start=time(11, 0), end=time(11, 35))
    await db.commit()

    res = await client.get(
        "/api/v1/schedule/v2/board",
        headers=_bearer(admin),
        params={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "office_id": str(office.id)},
    )
    assert res.status_code == 200, res.text
    cell = _find_cell(res.json(), str(office.id), 0)
    by_patient = {v["patient_id"]: v for v in cell["courses"][0]["visits"]}
    assert by_patient[str(known.id)]["patient_status_since"] == CHANGED_AT_JST_DATE.isoformat()
    assert by_patient[str(legacy.id)]["patient_status_since"] is None


@pytest.mark.asyncio
async def test_monitor_dto_carries_patient_status_since(db) -> None:
    from app.services.checkin.monitor import build_monitor

    office = await _seed_office(db, name="都賀", code="TSUGA")
    staff = await _seed_staff(db, office=office, name="看護M")
    known = await _seed_patient(
        db,
        office=office,
        code="P3SM-A",
        status=INACTIVE,
        status_changed_at=CHANGED_AT_UTC,
    )
    legacy = await _seed_patient(db, office=office, code="P3SM-B", status=INACTIVE)
    await _seed_visit(db, patient=known, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=legacy, staff=staff, start=time(11, 0), end=time(11, 35))
    await db.commit()

    resp = await build_monitor(db, WEEK_MONDAY)
    seen = {str(mv.patient_id): mv for row in resp.staff for mv in row.visits}
    assert seen[str(known.id)].patient_status_since == CHANGED_AT_JST_DATE
    assert seen[str(legacy.id)].patient_status_since is None


def test_status_since_date_converts_to_jst_and_tolerates_naive() -> None:
    """``status_since_date`` は UTC → JST の **日付**。naive は UTC とみなす。"""
    from app.services.patient_status_sync import status_since_date

    class _P:
        def __init__(self, value: datetime | None) -> None:
            self.status_changed_at = value

    assert status_since_date(_P(CHANGED_AT_UTC)) == CHANGED_AT_JST_DATE
    # naive (SQLite が返す形) も UTC とみなして JST へ寄せる。
    assert status_since_date(_P(CHANGED_AT_UTC.replace(tzinfo=None))) == CHANGED_AT_JST_DATE
    assert status_since_date(_P(None)) is None


# ---------------------------------------------------------------------------
# B. engine は非稼働患者の予定を「占有」として **残す** (レビュー BLOCKER)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_slots_buckets_keep_inactive_patient_visit(db) -> None:
    """非稼働患者の planned も枠を占有する。落とすと二重予約を許す。"""
    from app.services.scheduling.propose_slots_service import load_week_course_buckets

    office = await _seed_office(db, name="稲毛P", code="INAGEP")
    staff = await _seed_staff(db, office=office, name="看護P")
    course = await _seed_course(db, office=office, staff=staff)
    active = await _seed_patient(db, office=office, code="P3P-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3P-B", status=INACTIVE)
    await _seed_visit(db, patient=active, course=course, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=admitted, course=course, start=time(11, 0), end=time(11, 35))
    await db.commit()

    buckets, _names, _codes = await load_week_course_buckets(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    pids = {v.patient_id for b in buckets.values() for v in b.visits}
    assert active.id in pids
    assert admitted.id in pids


@pytest.mark.asyncio
async def test_feasibility_items_keep_inactive_patient_visit(db) -> None:
    from app.services.scheduling.feasibility_check import load_week_items

    office = await _seed_office(db, name="稲毛F", code="INAGEF")
    staff = await _seed_staff(db, office=office, name="看護F")
    active = await _seed_patient(db, office=office, code="P3F-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3F-B", status=INACTIVE)
    await _seed_visit(db, patient=active, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=admitted, staff=staff, start=time(11, 0), end=time(11, 35))
    await db.commit()

    items, _staff_names = await load_week_items(
        db, week_start=WEEK_MONDAY, week_end=WEEK_MONDAY + timedelta(days=6)
    )
    pids = {it.patient_id for day_items in items.values() for it in day_items}
    assert active.id in pids
    assert admitted.id in pids


@pytest.mark.asyncio
async def test_schedule_health_buckets_keep_inactive_patient_visit(db) -> None:
    from app.services.scheduling.schedule_health import _load_health_buckets

    office = await _seed_office(db, name="稲毛H", code="INAGEH")
    staff = await _seed_staff(db, office=office, name="看護H")
    active = await _seed_patient(db, office=office, code="P3H-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3H-B", status=INACTIVE)
    await _seed_visit(db, patient=active, course=None, staff=staff)
    course = await _seed_course(db, office=office, staff=staff)
    await _seed_visit(db, patient=active, course=course, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=admitted, course=course, start=time(11, 0), end=time(11, 35))
    await db.commit()

    buckets, _names = await _load_health_buckets(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    pids = {v.patient_id for b in buckets.values() for v in b.visits}
    assert active.id in pids
    assert admitted.id in pids


@pytest.mark.asyncio
async def test_substitute_day_rows_keep_inactive_patient_visit(db) -> None:
    from app.services.scheduling.substitute_candidates import load_day_rows

    office = await _seed_office(db, name="稲毛S", code="INAGES")
    staff = await _seed_staff(db, office=office, name="看護S")
    active = await _seed_patient(db, office=office, code="P3S-A", status="active")
    admitted = await _seed_patient(db, office=office, code="P3S-B", status=INACTIVE)
    await _seed_visit(db, patient=active, staff=staff, start=time(9, 0), end=time(9, 35))
    await _seed_visit(db, patient=admitted, staff=staff, start=time(11, 0), end=time(11, 35))
    await db.commit()

    rows = await load_day_rows(db, WEEK_MONDAY)
    pids = {p.id for _v, _c, p in rows}
    assert active.id in pids
    assert admitted.id in pids


@pytest.mark.asyncio
async def test_engines_still_drop_soft_deleted_patient(db) -> None:
    """soft-delete 患者の扱いは Phase 3 で **一切変えていない** (従来どおり除外)。"""
    from app.services.scheduling.propose_slots_service import load_week_course_buckets
    from app.services.scheduling.substitute_candidates import load_day_rows

    office = await _seed_office(db, name="稲毛D", code="INAGED")
    staff = await _seed_staff(db, office=office, name="看護D")
    course = await _seed_course(db, office=office, staff=staff)
    gone = await _seed_patient(db, office=office, code="P3D-X", status="active", deleted=True)
    await _seed_visit(db, patient=gone, course=course, staff=staff)
    await db.commit()

    buckets, _n, _c = await load_week_course_buckets(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_ids=[office.id]
    )
    assert gone.id not in {v.patient_id for b in buckets.values() for v in b.visits}
    assert gone.id not in {p.id for _v, _c2, p in await load_day_rows(db, WEEK_MONDAY)}


@pytest.mark.asyncio
async def test_pool_bulk_excludes_inactive_with_reason(db) -> None:
    """新規投入の **候補** からは外す。ただし黙って落とさず理由を残す。"""
    from app.services.scheduling.config import DEFAULT_SCHEDULING_CONFIG
    from app.services.scheduling.pool_bulk_inserter import simulate_pool_bulk_insert

    office = await _seed_office(db, name="稲毛BK", code="INAGEBK")
    await _seed_staff(db, office=office, name="看護BK")
    admitted = await _seed_patient(db, office=office, code="P3BK-B", status=INACTIVE)
    active = await _seed_patient(db, office=office, code="P3BK-A", status="active")
    await db.commit()

    result = await simulate_pool_bulk_insert(
        db,
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        office_id=office.id,
        patient_ids=[admitted.id, active.id],
        config=DEFAULT_SCHEDULING_CONFIG,
        candidate_of=lambda _p: None,
    )
    reasons = {(u.patient_id, u.reason) for u in result.unplaced}
    assert (admitted.id, "patient_not_active") in reasons
    # 稼働中の患者は同じ理由で弾かれない (この理由はステータス専用)。
    assert (active.id, "patient_not_active") not in reasons


# ---------------------------------------------------------------------------
# E. csv_builder は非稼働でも落とさない (実績と ⭐ を守る)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_builder_keeps_inactive_patient_completed_and_star_rows(db) -> None:
    """入院中でも「実績 (completed)」と「⭐ 配置」は送信対象に残す。

    落とすと (a) 過去の実績がカイポケから消え (b) PO が「残す」と決めた ⭐ が
    毎回 delete 差分として提案される。取消 (cancelled) だけが消える。
    """
    from app.services.kaipoke.csv_builder import BuildOptions, resolve_month_rows

    office = await _seed_office(db, name="稲毛C", code="INAGEC")
    staff = await _seed_staff(db, office=office, name="看護C")
    admitted = await _seed_patient(
        db, office=office, code="P3C-B", status=INACTIVE, name="入院　花子"
    )
    # 実績 (completed) と ⭐ 配置 (planned) と 連動取消 (cancelled) を同月に置く。
    await _seed_visit(
        db, patient=admitted, staff=staff, start=time(9, 0), end=time(9, 35), status="completed"
    )
    star_visit = await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=WEEK_MONDAY + timedelta(days=1),
        start=time(11, 0),
        end=time(11, 35),
    )
    await _seed_star_placement(db, patient=admitted, visit=star_visit)
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=WEEK_MONDAY + timedelta(days=2),
        start=time(13, 0),
        end=time(13, 35),
        status="cancelled",
        source="status_cancel",
    )
    await db.commit()

    rows = await resolve_month_rows(
        db, BuildOptions(year=WEEK_MONDAY.year, month=WEEK_MONDAY.month, include_unassigned=True)
    )
    dates = sorted(r.visit_date for r in rows if r.patient_name == "入院　花子")
    assert dates == [WEEK_MONDAY, WEEK_MONDAY + timedelta(days=1)]


# ---------------------------------------------------------------------------
# C. カイポケ突合 (§3-5) — 非稼働 × カイポケのみ × 今日以降 だけが削除候補
# ---------------------------------------------------------------------------


SNAPSHOT_HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,事業所名,日付,曜日,利用者,"
    "業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)


def _snap_line(day: int, patient: str, start: str, end: str, staff1: str, office: str) -> str:
    return (
        f"{staff1},看護師,,,,,,,{office},{day},月,{patient},医療保険,"
        f"精神基本療養費Ⅰ・正看,{start},{end},35,"
    )


def _recon_row(start: str = "09:00", end: str = "09:35", staff1: str = "A"):
    from app.services.kaipoke.reconcile_report_html import ReconRow

    return ReconRow(start=start, end=end, staff1=staff1, staff2="", service="x")


def test_recon_pair_delete_candidate_rule() -> None:
    """削除候補 = カイポケのみ AND 今日以降 AND 非稼働。1 つでも欠けたら従来判定。"""
    from app.services.kaipoke.reconcile_report_html import CATEGORY_INACTIVE, ReconPair

    future = _jst_today() + timedelta(days=3)
    past = _jst_today() - timedelta(days=3)
    row = _recon_row()

    # (1) 3 条件そろう → 削除候補。
    only_future = ReconPair(
        day=future, patient="入院　次郎", local=None, remote=row, patient_status=INACTIVE
    )
    assert only_future.is_delete_candidate is True
    assert only_future.category == CATEGORY_INACTIVE
    assert only_future.structural_category == "カイポケのみ"

    # (2) 過去日 → 実績。従来どおり「カイポケのみ」。
    only_past = ReconPair(
        day=past, patient="入院　次郎", local=None, remote=row, patient_status=INACTIVE
    )
    assert only_past.is_delete_candidate is False
    assert only_past.category == "カイポケのみ"

    # (3) 一致 (らく助にも実体あり) → 消す話ではない。
    matched = ReconPair(
        day=future, patient="入院　次郎", local=row, remote=row, patient_status=INACTIVE
    )
    assert matched.is_delete_candidate is False
    assert matched.category == "一致"

    # (4) 相違も同じ。
    differing = ReconPair(
        day=future,
        patient="入院　次郎",
        local=row,
        remote=_recon_row(staff1="B"),
        diffs=["担当"],
        patient_status=INACTIVE,
    )
    assert differing.is_delete_candidate is False
    assert differing.category == "担当"

    # (5) 稼働中は当然ふつうの判定。
    active_only = ReconPair(
        day=future, patient="稼働　太郎", local=None, remote=row, patient_status="active"
    )
    assert active_only.is_delete_candidate is False
    assert active_only.inactive_patient is False


@pytest.mark.asyncio
async def test_reconcile_report_lists_future_kaipoke_only_rows_as_delete_candidates(
    client, db
) -> None:
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, email="p3-recon@example.com")
    office = await _seed_office(db, name="稲毛R", code="INAGER")
    staff = await _seed_staff(db, office=office, name="看護R")
    week_start = _future_monday()
    admitted = await _seed_patient(
        db, office=office, code="P3R-B", status=INACTIVE, name="入院　次郎"
    )
    # らく助側にも 1 件残っている日 (火) を作る → その日は「一致」で削除候補にしない。
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start + timedelta(days=1),
        start=time(9, 0),
        end=time(9, 35),
    )
    await db.commit()

    csv_text = "\n".join(
        [
            SNAPSHOT_HEADER,
            # 月曜: カイポケにだけある未来の行 → 削除候補。
            _snap_line(week_start.day, "入院　次郎", "09:00", "09:35", "看護R", "稲毛R"),
            # 火曜: らく助にもある → 一致 (削除候補にしない)。
            _snap_line(
                (week_start + timedelta(days=1)).day,
                "入院　次郎",
                "09:00",
                "09:35",
                "看護R",
                "稲毛R",
            ),
        ]
    )
    await save_snapshot(
        db,
        office_id=None,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=None,
        csv_text=csv_text,
        source_op="test",
    )
    await db.commit()

    res = await client.get(
        "/api/v1/integrations/reconcile-report",
        params={"weekStart": week_start.isoformat(), "days": 7},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    counts = body["counts"]
    assert counts["非稼働患者"] == 1  # 月曜のカイポケのみ行だけ
    assert counts["カイポケのみ"] == 1  # 既存 4 キーの意味は不変 (内数)
    assert counts["一致"] == 1  # 火曜は一致のまま = 削除候補にしない
    assert "非稼働患者の行（削除候補）" in body["html"]
    assert "今日以降のカイポケのみの行" in body["html"]
    assert INACTIVE_LABEL in body["html"]


@pytest.mark.asyncio
async def test_reconcile_report_keeps_past_completed_row_as_match(client, db) -> None:
    """過去の実績 (completed) は入院中でも「一致」のまま = 削除候補にしない。"""
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, email="p3-recon-past@example.com")
    office = await _seed_office(db, name="稲毛RP", code="INAGERP")
    staff = await _seed_staff(db, office=office, name="看護RP")
    today = _jst_today()
    week_start = today - timedelta(days=today.weekday() + 7)  # 先週の月曜 (過去)
    if week_start.month != (week_start + timedelta(days=6)).month:
        week_start -= timedelta(days=7)
    admitted = await _seed_patient(
        db, office=office, code="P3RP-B", status=INACTIVE, name="入院　三代"
    )
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start,
        start=time(9, 0),
        end=time(9, 35),
        status="completed",
    )
    await db.commit()

    csv_text = "\n".join(
        [
            SNAPSHOT_HEADER,
            _snap_line(week_start.day, "入院　三代", "09:00", "09:35", "看護RP", "稲毛RP"),
        ]
    )
    await save_snapshot(
        db,
        office_id=None,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=None,
        csv_text=csv_text,
        source_op="test",
    )
    await db.commit()

    res = await client.get(
        "/api/v1/integrations/reconcile-report",
        params={"weekStart": week_start.isoformat(), "days": 7},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    counts = res.json()["counts"]
    assert counts["一致"] == 1
    assert counts["非稼働患者"] == 0
    assert "非稼働患者の行（削除候補）" not in res.json()["html"]


@pytest.mark.asyncio
async def test_reconcile_name_collision_prefers_active(client, db) -> None:
    """正規化後の同名が居たら「稼働中が 1 人でも居れば稼働中」に倒す (保守側)。"""
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, email="p3-recon-dup@example.com")
    office = await _seed_office(db, name="稲毛RD", code="INAGERD")
    await _seed_staff(db, office=office, name="看護RD")
    week_start = _future_monday()
    # 表記ゆれ違い (全角/半角スペース) = 正規化後は同一キー。片方が稼働中。
    await _seed_patient(db, office=office, code="P3RD-1", status=INACTIVE, name="同名　太郎")
    await _seed_patient(db, office=office, code="P3RD-2", status="active", name="同名 太郎")
    await db.commit()

    csv_text = "\n".join(
        [
            SNAPSHOT_HEADER,
            _snap_line(week_start.day, "同名　太郎", "09:00", "09:35", "看護RD", "稲毛RD"),
        ]
    )
    await save_snapshot(
        db,
        office_id=None,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=None,
        csv_text=csv_text,
        source_op="test",
    )
    await db.commit()

    res = await client.get(
        "/api/v1/integrations/reconcile-report",
        params={"weekStart": week_start.isoformat(), "days": 7},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    counts = res.json()["counts"]
    assert counts["非稼働患者"] == 0  # 稼働中が居るので削除候補にしない
    assert counts["カイポケのみ"] == 1


# ---------------------------------------------------------------------------
# C-2. 取込シートの inactive_patient (add / delete 双方向)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correction_items_flag_inactive_add_and_delete_rows(db) -> None:
    from app.api.v1.integrations import _read_correction_items
    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    office = await _seed_office(db, name="稲毛I", code="INAGEI")
    admitted = await _seed_patient(db, office=office, code="P3I-B", status=INACTIVE)
    sheet = CorrectionSheet(
        target_month=f"{WEEK_MONDAY.year:04d}-{WEEK_MONDAY.month:02d}",
        status="ready",
        direction="inbound",
        origin="test",
        week_start=WEEK_MONDAY,
        week_end=WEEK_MONDAY + timedelta(days=6),
    )
    db.add(sheet)
    await db.flush()
    rows = [
        CorrectionSheetItem(
            sheet_id=sheet.id, patient_id=admitted.id, action=action, before={}, after={}
        )
        for action in ("add", "delete", "edit")
    ]
    db.add_all(rows)
    await db.commit()

    reads = await _read_correction_items(db, rows)
    flags = {r.action: r.inactive_patient for r in reads}
    assert flags["add"] is True
    assert flags["delete"] is True
    # 「残骸を直す」行は印を付けない (人の判断を迷わせない)。
    assert flags["edit"] is False
    assert all(r.patient_status == INACTIVE for r in reads)


@pytest.mark.asyncio
async def test_inbound_sheet_items_endpoint_flags_delete_row(client, db) -> None:
    """到達する読み出し経路 (GET items) で inbound の delete 行に印が付く。

    inbound の delete = 「らく助にだけ残っている非稼働患者の行」= らく助側を取消す
    べき残骸。add (復活させてはいけない行) と同じバッジで見えるが行動は逆。
    """
    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_user(db, email="p3-inbound@example.com")
    office = await _seed_office(db, name="稲毛IB", code="INAGEIB")
    admitted = await _seed_patient(
        db, office=office, code="P3IB-B", status=INACTIVE, name="入院　伍郎"
    )
    active = await _seed_patient(db, office=office, code="P3IB-A", status="active")
    sheet = CorrectionSheet(
        target_month=f"{WEEK_MONDAY.year:04d}-{WEEK_MONDAY.month:02d}",
        status="ready",
        direction="inbound",
        origin="test",
        week_start=WEEK_MONDAY,
        week_end=WEEK_MONDAY + timedelta(days=6),
    )
    db.add(sheet)
    await db.flush()
    db.add_all(
        [
            CorrectionSheetItem(
                sheet_id=sheet.id,
                patient_id=admitted.id,
                action="delete",
                before={},
                after={},
                include=True,
            ),
            CorrectionSheetItem(
                sheet_id=sheet.id,
                patient_id=active.id,
                action="delete",
                before={},
                after={},
                include=True,
            ),
        ]
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/items",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    by_patient = {it["patient_id"]: it for it in res.json()["items"]}
    assert by_patient[str(admitted.id)]["inactive_patient"] is True
    assert by_patient[str(admitted.id)]["patient_status"] == INACTIVE
    # 稼働中の患者の delete 行には印を付けない。
    assert by_patient[str(active.id)]["inactive_patient"] is False


@pytest.mark.asyncio
async def test_unsent_summary_items_carry_same_inactive_flag(client, db) -> None:
    """同期バーの行とシート一覧の行で印がズレない (同じ規則で載せる)。"""
    from app.services.kaipoke.csv_builder import HEADER
    from app.services.kaipoke.csv_snapshot import save_snapshot

    admin = await _make_user(db, email="p3-unsent-flag@example.com")
    office = await _seed_office(db, name="稲毛IF", code="INAGEIF")
    staff = await _seed_staff(db, office=office, name="看護IF")
    week_start = _future_monday()
    admitted = await _seed_patient(
        db, office=office, code="P3IF-B", status=INACTIVE, name="入院　伍郎"
    )
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start,
        start=time(10, 0),
        end=time(10, 35),
    )
    await save_snapshot(
        db,
        office_id=None,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=None,
        csv_text=",".join(HEADER) + "\r\n",
        source_op="test",
    )
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    adds = [it for it in res.json()["items"] if it["action"] == "add"]
    assert len(adds) == 1
    assert adds[0]["patient_status"] == INACTIVE
    assert adds[0]["inactive_patient"] is True


# ---------------------------------------------------------------------------
# D. ●未送信サマリ (§3-5)
# ---------------------------------------------------------------------------


async def _snapshot_from_current_visits(db, week_start: date, *, office_id=None) -> None:
    """今の visits をそのままカイポケ現況として保存する (= 差分ゼロの出発点)。"""
    from app.services.kaipoke.csv_builder import BuildOptions, build_month_csv
    from app.services.kaipoke.csv_snapshot import save_snapshot

    raw = await build_month_csv(
        db, BuildOptions(year=week_start.year, month=week_start.month, include_unassigned=True)
    )
    await save_snapshot(
        db,
        office_id=office_id,
        month=f"{week_start.year:04d}-{week_start.month:02d}",
        week_start=None,
        csv_text=raw.decode("cp932"),
        source_op="test",
    )


@pytest.mark.asyncio
async def test_unsent_summary_groups_inactive_cancellations(client, db) -> None:
    """入院中の患者ぶんの delete が患者単位にまとまり、送れる件数が別に出る。"""
    admin = await _make_user(db, email="p3-unsent@example.com")
    office = await _seed_office(db, name="稲毛U", code="INAGEU")
    staff = await _seed_staff(db, office=office, name="看護U")
    today = _jst_today()
    week_start = today - timedelta(days=today.weekday())  # 今週 = 過去日と未来日が混ざる
    if week_start.month != (week_start + timedelta(days=6)).month:
        week_start -= timedelta(days=7)
    patient = await _seed_patient(
        db, office=office, code="P3U-B", status="active", name="入院　三郎"
    )
    # 過去日 1 件 + 未来日 2 件 (未来が無い週になったら未来分は 0 になるだけ)。
    days = [week_start, week_start + timedelta(days=5), week_start + timedelta(days=6)]
    for i, d in enumerate(days):
        await _seed_visit(
            db,
            patient=patient,
            staff=staff,
            visit_date=d,
            start=time(10 + i, 0),
            end=time(10 + i, 35),
        )
    await db.commit()

    # カイポケ側の現況 = まだ稼働中だった頃に送った行。
    await _snapshot_from_current_visits(db, week_start)
    # ここで入院 → らく助側から消す (取消) と、カイポケ側は delete 差分になる。
    patient.status = INACTIVE
    for v in (await db.scalars(_planned_visits_stmt(patient.id))).all():
        v.status = "cancelled"
        v.source = "status_cancel"
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert {it["action"] for it in body["items"]} == {"delete"}
    assert len(body["inactive_groups"]) == 1
    group = body["inactive_groups"][0]
    assert group["patient_id"] == str(patient.id)
    assert group["patient_name"] == "入院　三郎"
    assert group["status"] == INACTIVE
    assert group["status_label"] == INACTIVE_LABEL
    assert group["count"] == len(days)  # 過去日を含む総数
    expected_sendable = sum(1 for d in days if d > _jst_today())
    assert group["sendable_count"] == expected_sendable
    assert group["sendable_count"] < group["count"]  # 過去日は送れない


def _planned_visits_stmt(patient_id):
    from sqlalchemy import select

    return select(Visit).where(Visit.patient_id == patient_id, Visit.status == VISIT_STATUS_PLANNED)


@pytest.mark.asyncio
async def test_unsent_summary_residue_excludes_star_and_scopes_office(client, db) -> None:
    """残骸 = planned × 非稼働 × ⭐以外 ×(拠点スコープ)。週全体が未来なので日付は不問。"""
    admin = await _make_user(db, email="p3-residue@example.com")
    office = await _seed_office(db, name="稲毛RS", code="INAGERS")
    other = await _seed_office(db, name="都賀RS", code="TSUGARS")
    staff = await _seed_staff(db, office=office, name="看護RS")
    week_start = _future_monday()  # 週まるごと未来 (月曜 = weekday 0)

    admitted = await _seed_patient(db, office=office, code="P3RS-B", status=INACTIVE)
    # (1) 素の残骸 (planned・⭐ 無し) → 数える。
    await _seed_visit(
        db, patient=admitted, staff=staff, visit_date=week_start, start=time(9, 0), end=time(9, 35)
    )
    # (2) ⭐ 配置 → PO が「残す」と決めたもの。数えない。
    star_visit = await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start,
        start=time(14, 0),
        end=time(14, 35),
    )
    await _seed_star_placement(db, patient=admitted, visit=star_visit)
    # (3) 連動取消済み (cancelled) → 残骸ではない。数えない。
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start,
        start=time(15, 0),
        end=time(15, 35),
        status="cancelled",
        source="status_cancel",
    )
    # (4) 別拠点の非稼働患者 → 拠点スコープ時は数えない。
    other_admitted = await _seed_patient(db, office=other, code="P3RS-O", status=INACTIVE)
    await _seed_visit(
        db,
        patient=other_admitted,
        staff=staff,
        visit_date=week_start,
        start=time(16, 0),
        end=time(16, 35),
    )
    await db.commit()

    # 拠点スコープなしのスナップショット → 両拠点ぶん数える (1) + (4) = 2。
    await _snapshot_from_current_visits(db, week_start)
    await db.commit()
    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["inactive_residue"] == 2

    # 拠点スコープありのスナップショット → 稲毛RS の (1) だけ = 1。
    await _snapshot_from_current_visits(db, week_start, office_id=office.id)
    await db.commit()
    res2 = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res2.status_code == 200, res2.text
    assert res2.json()["inactive_residue"] == 1


@pytest.mark.asyncio
async def test_unsent_summary_residue_ignores_past_days(client, db) -> None:
    """過去日の planned は実績側。残骸に数えない (永久に鳴るアラームにしない)。"""
    admin = await _make_user(db, email="p3-residue-past@example.com")
    office = await _seed_office(db, name="稲毛RSP", code="INAGERSP")
    staff = await _seed_staff(db, office=office, name="看護RSP")
    today = _jst_today()
    week_start = today - timedelta(days=today.weekday() + 7)  # 先週 = 週まるごと過去
    if week_start.month != (week_start + timedelta(days=6)).month:
        week_start -= timedelta(days=7)
    admitted = await _seed_patient(db, office=office, code="P3RSP-B", status=INACTIVE)
    await _seed_visit(
        db, patient=admitted, staff=staff, visit_date=week_start, start=time(9, 0), end=time(9, 35)
    )
    await db.commit()
    await _snapshot_from_current_visits(db, week_start)
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["inactive_residue"] == 0


@pytest.mark.asyncio
async def test_unsent_summary_without_inactive_patients_is_empty(client, db) -> None:
    admin = await _make_user(db, email="p3-unsent-ok@example.com")
    office = await _seed_office(db, name="稲毛U2", code="INAGEU2")
    staff = await _seed_staff(db, office=office, name="看護U2")
    week_start = _future_monday()
    patient = await _seed_patient(
        db, office=office, code="P3U-A", status="active", name="稼働　四郎"
    )
    await _seed_visit(
        db,
        patient=patient,
        staff=staff,
        visit_date=week_start,
        start=time(10, 0),
        end=time(10, 35),
    )
    await db.commit()
    await _snapshot_from_current_visits(db, week_start)
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inactive_groups"] == []
    assert body["inactive_residue"] == 0


@pytest.mark.asyncio
async def test_unsent_summary_without_snapshot_reports_zero_residue(client, db) -> None:
    """現況CSVが無ければ残骸も 0 (数字だけ独り歩きさせない)。"""
    admin = await _make_user(db, email="p3-unsent-nosnap@example.com")
    office = await _seed_office(db, name="稲毛U3", code="INAGEU3")
    staff = await _seed_staff(db, office=office, name="看護U3")
    week_start = _future_monday()
    admitted = await _seed_patient(db, office=office, code="P3U3-B", status=INACTIVE)
    await _seed_visit(
        db,
        patient=admitted,
        staff=staff,
        visit_date=week_start,
        start=time(10, 0),
        end=time(10, 35),
    )
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/unsent-summary",
        json={"week_start": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot"] is None
    assert body["inactive_residue"] == 0
    assert body["inactive_groups"] == []
