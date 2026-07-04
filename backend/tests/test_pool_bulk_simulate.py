"""W-1 テスト: POST /api/v1/schedule/v2/pool-bulk-simulate.

プール患者列を逐次シミュレーションで 1 人ずつ決定論的に積む read-only endpoint.
候補生成・delta は個別提案 (propose-slots) / pool-overview と完全に同一
(``compute_all_proposed_slots`` 再利用).

検証内容:
    1. 決定性: 同一入力 2 回で完全一致.
    2. 調停: 先行患者が枠を埋めたら後続患者が同一バケットの容量超過を避ける.
    3. 複数曜日カバレッジ: frequency_per_week=2 の患者が 2 曜日に投入される.
    4. 部分投入: 1 曜日しか入らない場合 partial に載る.
    5. 順序: 候補 1 つ (投入可能曜日 1) の患者が先に確定する.
    6. 50 件上限で 422.
    7. pool-overview との整合: 1 人目の患者の投入先が pool-overview の best と一致.
    8. state_token が返る / visits が変わると token が変わる.

ローカル SQLite のみ (本番 DB 禁止). fixture は test_pool_overview_p2.py と整合.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, User
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.pool_bulk_inserter import compute_bulk_state_token

ISO_YEAR = 2026
ISO_WEEK = 21
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)  # Mon

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)


# ---------------------------------------------------------------------------
# Helpers (test_pool_overview_p2.py と同一パターン)
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
    """propose-slots が受ける weekly_pattern サマリ形式 (既定: Mon 終日 30分 週1回)."""
    wp: dict[str, Any] = {
        "service_minutes": 30,
        "time_type": "終日",
        "preferred_weekdays": ["Mon"],
        "frequency_per_week": 1,
    }
    wp.update(overrides)
    return wp


def _simulate_payload(office: Office, patient_ids: list[str]) -> dict[str, Any]:
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(office.id),
        "patient_ids": patient_ids,
    }


# ---------------------------------------------------------------------------
# 1. 決定性 (同一入力 2 回で完全一致)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_deterministic(client, db) -> None:
    admin = await _make_user(db, email="pb-admin1@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=2, code="A", anchor_xy=NEAR)
    wp = _pool_wp(preferred_weekdays=["Mon", "Wed"], frequency_per_week=2)
    p1 = await _seed_patient(
        db, office=office, code="D1", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    p2 = await _seed_patient(
        db, office=office, code="D2", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    await db.commit()

    ids = [str(p1.id), str(p2.id)]
    r1 = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, ids),
    )
    r2 = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, ids),
    )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json() == r2.json(), "同一入力で出力が一致しない (非決定性)"


# ---------------------------------------------------------------------------
# 2. 調停 (先行患者が枠を埋めたら後続が容量超過を避ける)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_arbitration_avoids_overcapacity(client, db) -> None:
    """コース定員 6. 既存 5 名の満杯間近コースに、同住所の候補 2 名を投入 →
    1 枠しか空いていないので 1 名のみ入り、もう 1 名は別枠が無ければ投入不能になる."""
    admin = await _make_user(db, email="pb-admin2@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    # 既存 5 名 (異住所で埋める. 6 人目=残 1 枠).
    starts = [time(9, 0), time(9, 40), time(10, 20), time(13, 0), time(13, 40)]
    for i, st in enumerate(starts):
        lat = BASE[0] + 0.02 * (i + 1)
        lng = BASE[1] + 0.02 * (i + 1)
        fp = await _seed_patient(db, office=office, code=f"EX{i}", lat=lat, lng=lng)
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=fp, course=course, start=st, end=end)
    # プール 2 名 (同住所 NEAR, Mon 週1). 残 1 枠を奪い合う.
    wp = _pool_wp()
    pa = await _seed_patient(
        db, office=office, code="PA", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    pb = await _seed_patient(
        db, office=office, code="PB", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(pa.id), str(pb.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 同一 (Mon, A) バケットに 2 名は入らない: placements の (weekday, course) 重複ゼロを確認.
    keys = [(pl["weekday"], pl["course_code"]) for pl in body["placements"]]
    assert len(keys) == len(set(keys)), f"同一バケットに複数投入 (調停失敗): {keys}"
    # 少なくとも 1 名は投入され、片方は投入不能 (or 部分) になる (残 1 枠のため).
    placed_pids = {pl["patient_id"] for pl in body["placements"]}
    assert len(placed_pids) == 1, body
    other = {str(pa.id), str(pb.id)} - placed_pids
    unplaced_pids = {u["patient_id"] for u in body["unplaced"]}
    assert other <= unplaced_pids, body


# ---------------------------------------------------------------------------
# 3. 複数曜日カバレッジ (frequency_per_week=2 → 2 曜日投入)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_multi_weekday_coverage(client, db) -> None:
    admin = await _make_user(db, email="pb-admin3@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=2, code="A", anchor_xy=NEAR)
    wp = _pool_wp(preferred_weekdays=["Mon", "Wed"], frequency_per_week=2)
    p = await _seed_patient(
        db, office=office, code="MC", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    my = [pl for pl in body["placements"] if pl["patient_id"] == str(p.id)]
    assert len(my) == 2, body
    assert {pl["weekday"] for pl in my} == {0, 2}, my
    assert body["partial"] == []
    assert body["unplaced"] == []


# ---------------------------------------------------------------------------
# 4. 部分投入 (1 曜日しか入らない → partial)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_partial_placement(client, db) -> None:
    """希望 Mon/Wed 週2 だが Wed は開講コース無し → Mon のみ入り partial に載る."""
    admin = await _make_user(db, email="pb-admin4@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    # Mon のみ開講 (Wed は course 無し → course_closed).
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    wp = _pool_wp(preferred_weekdays=["Mon", "Wed"], frequency_per_week=2)
    p = await _seed_patient(
        db, office=office, code="PP", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    my = [pl for pl in body["placements"] if pl["patient_id"] == str(p.id)]
    assert len(my) == 1 and my[0]["weekday"] == 0, body
    partial = [pt for pt in body["partial"] if pt["patient_id"] == str(p.id)]
    assert len(partial) == 1, body
    assert partial[0]["placed_days"] == 1
    assert partial[0]["missing_days"] == 1
    # 未投入曜日 Wed(=2) の理由が付く (course_closed).
    assert partial[0]["unplaced_reasons"].get("2") == "course_closed", partial[0]


# ---------------------------------------------------------------------------
# 5. 順序 (候補 1 つの患者が先に確定する)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_ordering_single_option_first(client, db) -> None:
    """投入可能曜日 1 の患者 (第1群) が、投入可能曜日 2 の患者 (第2群) より先の seq を得る."""
    admin = await _make_user(db, email="pb-admin5@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=2, code="A", anchor_xy=NEAR)
    # single: Mon のみ希望 (投入可能曜日 1). multi: Mon/Wed 希望 (投入可能曜日 2).
    wp_single = _pool_wp(preferred_weekdays=["Mon"], frequency_per_week=1)
    wp_multi = _pool_wp(preferred_weekdays=["Mon", "Wed"], frequency_per_week=2)
    p_multi = await _seed_patient(
        db, office=office, code="MULTI", lat=BASE[0], lng=BASE[1], weekly_pattern=wp_multi
    )
    p_single = await _seed_patient(
        db, office=office, code="SINGLE", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp_single
    )
    await db.commit()

    # 入力順は multi を先に置いても、single が先に確定するべき.
    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p_multi.id), str(p_single.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    seq_by_pid: dict[str, int] = {}
    for pl in body["placements"]:
        seq_by_pid.setdefault(pl["patient_id"], pl["seq"])
    assert seq_by_pid[str(p_single.id)] < seq_by_pid[str(p_multi.id)], body


# ---------------------------------------------------------------------------
# 6. サイズガード (51 件で 422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_size_guard_422(client, db) -> None:
    admin = await _make_user(db, email="pb-admin6@example.com", role="admin")
    office, _staff = await _seed_office_staff(db)
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(uuid4()) for _ in range(51)]),
    )
    assert res.status_code == 422, res.text
    assert "50" in res.text


# ---------------------------------------------------------------------------
# 7. pool-overview との整合 (1 人目の投入先が best と一致)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_first_patient_matches_pool_overview(client, db) -> None:
    admin = await _make_user(db, email="pb-admin7@example.com", role="admin")
    office, staff = await _seed_office_staff(db)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR)
    await _seed_anchor_course(db, office=office, staff=staff, weekday=0, code="B", anchor_xy=FAR)
    wp = _pool_wp()  # Mon 週1.
    p = await _seed_patient(
        db, office=office, code="SOLO", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    await db.commit()

    ov = await client.post(
        "/api/v1/schedule/v2/pool-overview",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert ov.status_code == 200, ov.text
    best = ov.json()["items"][0]["best_slot"]
    assert best is not None

    sim = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert sim.status_code == 200, sim.text
    placements = sim.json()["placements"]
    assert len(placements) == 1, sim.json()
    pl = placements[0]
    # 一括の 1 人目の投入先が pool-overview の best と一致 (曜日/コース/拠点/開始時刻).
    assert pl["weekday"] == best["weekday"]
    assert pl["course_code"] == best["course_code"]
    assert pl["office_id"] == best["office_id"]
    assert pl["start_time"] == best["start_time"]


# ---------------------------------------------------------------------------
# 8. state_token — ユニットテスト (同一 db セッション内で直接呼び出し)
#    API 経由では in-memory SQLite のセッション分離で変化が見えない場合があるため、
#    compute_bulk_state_token を同セッション内で flush → 再計算する方式に変更.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_state_token_changes_with_visits(db) -> None:
    """visits を追加 (flush) すると state_token が変わることをユニットテストで検証."""
    office, staff = await _seed_office_staff(db, name="稲ST", code="STOFF")
    course = await _seed_anchor_course(
        db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR
    )
    await _seed_patient(db, office=office, code="ST8", lat=BASE[0], lng=BASE[1])
    await db.flush()

    token1 = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    assert isinstance(token1, str) and len(token1) == 64, token1  # SHA-256 hex

    # 同セッション内で visit を追加 → flush だけで同セッションから見える.
    extra = await _seed_patient(db, office=office, code="EXTRA8", lat=FAR[0], lng=FAR[1])
    await _seed_visit(db, patient=extra, course=course, start=time(14, 0), end=time(14, 30))
    await db.flush()

    token2 = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    assert token1 != token2, "visits が変わったのに state_token が変わらない"


@pytest.mark.asyncio
async def test_bulk_state_token_changes_with_pfv(db) -> None:
    """PFV (固定訪問パターン) を追加 (flush) すると state_token が変わることを検証."""
    office, _staff = await _seed_office_staff(db, name="稲PFV", code="PFVOFF")
    p = await _seed_patient(db, office=office, code="PFV9", lat=BASE[0], lng=BASE[1])
    await db.flush()

    token1 = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )

    # 同セッション内で PFV を追加 → flush だけで同セッションから見える.
    pfv = PatientFixedVisit(
        patient_id=p.id,
        mode="normal",
        weekday=0,
        start_time=time(9, 0),
        duration_min=30,
    )
    db.add(pfv)
    await db.flush()

    token2 = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office.id
    )
    assert token1 != token2, "PFV が変わったのに state_token が変わらない"


# ---------------------------------------------------------------------------
# 9. RBAC (staff 403 / no-auth 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_rejects_staff_role(client, db) -> None:
    staff_user = await _make_user(db, email="pb-staff@example.com", role="staff")
    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(staff_user),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(uuid4()),
            "patient_ids": [],
        },
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_bulk_rejects_no_auth(client, db) -> None:
    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(uuid4()),
            "patient_ids": [],
        },
    )
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 10. overcapacity_available_count (A 案・方式b 橋渡し)
#     一括の先行患者がコース定員を埋めた「最終 sim 状態」基準で +1 判定すること.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_overcapacity_count_when_filled_by_earlier(client, db) -> None:
    """A 案: 一括の先行患者がコース定員 (6) を埋めた結果 capacity_full になった後続患者に
    overcapacity_available_count>=1 (定員 +1 なら入る候補) が付く.

    初期 DB では空き (既存 5 名) があるが、一括の 1 人目が 6 枠目を埋める →
    2 人目は capacity_full → 「最終 sim 状態」基準で +1 判定 → 同住所で時間は空くため count>=1.
    (= 「一括で先に埋まった枠」を考慮した最終状態基準であることの検証).
    """
    admin = await _make_user(db, email="pb-overcap1@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲OC", code="OCOFF")
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    # 既存 5 名 (同住所 NEAR で co-locate → 時間はブロッカーにならず、定員のみが効く).
    starts = [time(9, 0), time(9, 30), time(10, 0), time(10, 30), time(11, 0)]
    for i, st in enumerate(starts):
        ex = await _seed_patient(db, office=office, code=f"OCEX{i}", lat=NEAR[0], lng=NEAR[1])
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=ex, course=course, start=st, end=end)
    # プール 2 名 (同住所 NEAR, Mon 週1). 1 人目が 6 枠目 → 2 人目 capacity_full.
    wp = _pool_wp()
    pa = await _seed_patient(
        db, office=office, code="OCPA", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    pb = await _seed_patient(
        db, office=office, code="OCPB", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(pa.id), str(pb.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 1 人だけ入り、もう 1 人は capacity_full で投入不能.
    assert len(body["placements"]) == 1, body
    cap_unplaced = [u for u in body["unplaced"] if u["reason"] == "capacity_full"]
    assert len(cap_unplaced) == 1, body
    # 最終 sim 状態 (6 枠埋まり) に対する +1 判定で「定員 +1 なら入る候補」が 1 件以上ある.
    assert cap_unplaced[0]["overcapacity_available_count"] >= 1, body


@pytest.mark.asyncio
async def test_bulk_overcapacity_count_zero_when_no_time(client, db) -> None:
    """capacity_full だが「定員 +1 でも時間が物理的に無い」患者は count=0 のまま.

    コース定員 6 を既存 6 名で満杯にし、投入不能患者は超長時間 (service 400 分) 希望 →
    +1 で定員を緩めても course の残時間に収まらず候補ゼロ = overcapacity_available_count==0.
    """
    admin = await _make_user(db, email="pb-overcap2@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲OC0", code="OC0OFF")
    course = await _seed_course(db, office=office, staff=staff, weekday=0, code="A")
    # 既存 6 名 (同住所 NEAR, 定員ちょうど満杯).
    starts = [time(9, 0), time(9, 30), time(10, 0), time(10, 30), time(11, 0), time(11, 30)]
    for i, st in enumerate(starts):
        ex = await _seed_patient(db, office=office, code=f"OC0EX{i}", lat=NEAR[0], lng=NEAR[1])
        end = time(st.hour, st.minute + 30) if st.minute < 30 else time(st.hour + 1, st.minute - 30)
        await _seed_visit(db, patient=ex, course=course, start=st, end=end)
    # プール 1 名: 同住所だが service 400 分 (course 残時間に +1 でも収まらない).
    wp = _pool_wp(service_minutes=400)
    p = await _seed_patient(
        db, office=office, code="OC0P", lat=NEAR[0], lng=NEAR[1], weekly_pattern=wp
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    mine = [u for u in body["unplaced"] if u["patient_id"] == str(p.id)]
    assert len(mine) == 1, body
    assert mine[0]["reason"] == "capacity_full", body
    # +1 でも時間が無い → 候補ゼロ → count は既定 0 のまま.
    assert mine[0]["overcapacity_available_count"] == 0, body


# ---------------------------------------------------------------------------
# 11. 同曜日二重投入防止 (既存 visit がある曜日への再挿入を防ぐ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_no_double_weekday_insertion(client, db) -> None:
    """月曜に既存 visit がある frequency=2・希望[月,水] の患者を投入すると
    月ではなく水に入る (同曜日二重投入防止)."""
    admin = await _make_user(db, email="pb-admin-ddw@example.com", role="admin")
    office, staff = await _seed_office_staff(db, name="稲DDW", code="DDWOFF")
    # 月・水の両曜日にコースを作る.
    mon_course = await _seed_anchor_course(
        db, office=office, staff=staff, weekday=0, code="A", anchor_xy=NEAR
    )
    await _seed_anchor_course(db, office=office, staff=staff, weekday=2, code="A", anchor_xy=NEAR)
    # 対象患者: frequency=2、月/水 希望.
    wp = _pool_wp(preferred_weekdays=["Mon", "Wed"], frequency_per_week=2)
    p = await _seed_patient(
        db, office=office, code="DDW", lat=BASE[0], lng=BASE[1], weekly_pattern=wp
    )
    # 月曜コースに既存 visit を追加 (DB に入る → sim ロード時に current_placed=1).
    await _seed_visit(db, patient=p, course=mon_course, start=time(11, 0), end=time(11, 30))
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/v2/pool-bulk-simulate",
        headers=_bearer(admin),
        json=_simulate_payload(office, [str(p.id)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    my = [pl for pl in body["placements"] if pl["patient_id"] == str(p.id)]
    # shortage=1 (frequency=2, current=1) → 水曜に 1 枠のみ入る.
    assert len(my) == 1, body
    assert my[0]["weekday"] == 2, f"月曜への二重投入が発生: {body}"
    # 不足なく 1 枠入るので partial も unplaced も空.
    assert body["partial"] == []
    assert body["unplaced"] == []
