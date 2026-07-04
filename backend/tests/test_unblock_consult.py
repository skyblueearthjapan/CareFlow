"""W-12d テスト: 詰まり解消相談 (propose-unblock / apply).

設計書 docs/plans/unblock-consult-design.md §4:
    - 深さ1成立 (ブロッカー1移動で開通) / 深さ2 (同一バケット2移動)。
    - 各適格性除外 (pinned / locked / 2名体制 / 同住所ペア / 却下記憶 / 希望外) が動かない。
    - 2名体制ターゲットのペア開通 / 決定性 / ランキング順。
    - apply 正常・409・pinned 422 ロールバック・reset 波及・非対象患者不変・unmovable_summary。

ローカル SQLite のみ (本番 DB 禁止). 座標は千葉市近辺 (test_propose_slots 系と整合).
"""

from __future__ import annotations

from datetime import date, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 24
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)  # 対象患者 (候補)
V_COORD = (35.6040, 140.1000)  # 単一ブロッカー (~440m, 別住所)
FILL = (35.6000, 140.1200)  # 退避先バケットを実在させる filler (~1.8km, 別住所)

_URL = "/api/v1/schedule/v2/propose-unblock"
_APPLY_URL = "/api/v1/schedule/v2/propose-unblock/apply"


# ---------------------------------------------------------------------------
# 共通ヘルパ
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


def _patient(
    code: str,
    coords: tuple[float, float],
    office_id: UUID,
    *,
    requires_multiple_staff: bool = False,
    weekly_pattern: dict | None = None,
) -> Patient:
    return Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        special_week_active=[],
        lat=coords[0],
        lng=coords[1],
        primary_office_id=office_id,
        requires_multiple_staff=requires_multiple_staff,
        weekly_pattern=weekly_pattern,
    )


async def _mk_office_staff(db) -> tuple[Office, Staff]:
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="S1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    for wd in range(5):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    return office, staff


async def _mk_template(db, office: Office, label: str) -> CourseTemplate:
    ct = CourseTemplate(office_id=office.id, label=label)
    db.add(ct)
    await db.flush()
    return ct


async def _mk_course(
    db, office: Office, staff: Staff, weekday: int, code: str, template: CourseTemplate | None
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
        template_id=template.id if template is not None else None,
    )
    db.add(course)
    await db.flush()
    return course


async def _add_visit_and_pfv(
    db,
    patient: Patient,
    course: Course,
    staff: Staff,
    *,
    weekday: int,
    start: time,
    duration: int = 30,
    movability: str = "day_flexible",
    is_pinned: bool = False,
    slot_index: int = 0,
) -> None:
    end_min = start.hour * 60 + start.minute + duration
    end = time(end_min // 60, end_min % 60)
    db.add(
        Visit(
            patient_id=patient.id,
            visit_date=WEEK_MONDAY + timedelta(days=weekday),
            start_time=start,
            end_time=end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )
    )
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=weekday,
            slot_index=slot_index,
            start_time=start,
            duration_min=duration,
            movability=movability,
            is_pinned=is_pinned,
        )
    )


def _candidate_body(office: Office, candidate: Patient, **overrides) -> dict:
    body = {
        "service_minutes": 30,
        "time_type": "固定",
        "preferred_start": "10:00",
        "preferred_weekdays": ["Mon"],
        "lat": BASE[0],
        "lng": BASE[1],
        "existing_patient_id": str(candidate.id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(office.id),
        "limit": 5,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# 単一ブロッカー (時刻起因): Mon A の 10:00 を 1 人が塞ぐ / Tue A に退避先
# ---------------------------------------------------------------------------


async def _seed_single_blocker(
    db,
    *,
    movability: str = "day_flexible",
    is_pinned: bool = False,
    requires_multiple_staff: bool = False,
    dismiss_kind: str | None = None,
) -> tuple[Office, Patient, Patient]:
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)
    tue_a = await _mk_course(db, office, staff, 1, "A", ct_a)

    blocker = _patient("BLK", V_COORD, office.id, requires_multiple_staff=requires_multiple_staff)
    fill = _patient("FILL", FILL, office.id)
    candidate = _patient("TGT", BASE, office.id)
    db.add_all([blocker, fill, candidate])
    await db.flush()

    await _add_visit_and_pfv(
        db,
        blocker,
        mon_a,
        staff,
        weekday=0,
        start=time(10, 0),
        movability=movability,
        is_pinned=is_pinned,
    )
    # 退避先バケット (Tue A) を実在させる filler (locked で候補ブロッカーにならない).
    await _add_visit_and_pfv(
        db, fill, tue_a, staff, weekday=1, start=time(9, 30), movability="locked"
    )

    if dismiss_kind is not None:
        db.add(
            SuggestionDismissal(
                patient_id=blocker.id, kind=dismiss_kind, target_weekday=0, reason="other"
            )
        )
    await db.commit()
    return office, candidate, blocker


# ---------------------------------------------------------------------------
# 深さ1成立 / apply / 決定性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth1_unblock_forms_plan(client, db) -> None:
    """ブロッカー 1 移動で対象が入る: 1 手プランが出る (退避先 = Tue A)."""
    admin = await _make_user(db, email="ub1@example.com")
    office, candidate, blocker = await _seed_single_blocker(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["state_token"]
    assert data["plans"], data["unmovable_summary"]
    plan = data["plans"][0]
    assert plan["moved_count"] == 1
    assert len(plan["moves"]) == 1
    mv = plan["moves"][0]
    assert mv["patient_id"] == str(blocker.id)
    assert mv["from"]["weekday"] == 0 and mv["from"]["start_time"] == "10:00"
    assert mv["to"]["weekday"] == 1  # Tue へ退避 (day_flexible).
    assert plan["insert"]["weekday"] == 0 and plan["insert"]["course_code"] == "A"
    assert plan["insert"]["start_time"] == "10:00"
    assert plan["insert"]["partner_course_code"] is None


@pytest.mark.asyncio
async def test_determinism(client, db) -> None:
    """同一入力 2 回で plans (順序 + plan_id) が完全一致する."""
    admin = await _make_user(db, email="ubdet@example.com")
    office, candidate, _blk = await _seed_single_blocker(db)
    body = _candidate_body(office, candidate)
    r1 = await client.post(_URL, headers=_bearer(admin), json=body)
    r2 = await client.post(_URL, headers=_bearer(admin), json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    ids1 = [p["plan_id"] for p in r1.json()["plans"]]
    ids2 = [p["plan_id"] for p in r2.json()["plans"]]
    assert ids1 == ids2 and ids1


# ---------------------------------------------------------------------------
# 適格性除外 (単一ブロッカーの属性を変える)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pinned_blocker_not_moved(client, db) -> None:
    admin = await _make_user(db, email="ubpin@example.com")
    office, candidate, _blk = await _seed_single_blocker(db, movability="locked", is_pinned=True)
    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"] == []
    assert data["unmovable_summary"]["pinned"] >= 1


@pytest.mark.asyncio
async def test_locked_blocker_not_moved(client, db) -> None:
    admin = await _make_user(db, email="ublock@example.com")
    office, candidate, _blk = await _seed_single_blocker(db, movability="locked")
    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"] == []
    assert data["unmovable_summary"]["locked"] >= 1


@pytest.mark.asyncio
async def test_two_staff_blocker_not_moved(client, db) -> None:
    admin = await _make_user(db, email="ub2s@example.com")
    office, candidate, _blk = await _seed_single_blocker(db, requires_multiple_staff=True)
    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"] == []
    assert data["unmovable_summary"]["two_staff"] >= 1


@pytest.mark.asyncio
async def test_dismissed_blocker_not_moved(client, db) -> None:
    """却下記憶がある退避手は出さない → dismissed 計上.

    同一コース (time_change) と曜日跨ぎ (day_change) を両方却下し全退避先を封鎖する。
    同コース時間ずらしが有効になった今、どちらか一方の却下だけでは plans が出るため
    両種を却下することで「退避先なし → dismissed 計上」を保証する。
    """
    admin = await _make_user(db, email="ubdis@example.com")
    office, candidate, blk = await _seed_single_blocker(db, dismiss_kind="day_change")
    # time_change (同コース内時間ずらし) も却下し、全退避先を封鎖する.
    db.add(
        SuggestionDismissal(patient_id=blk.id, kind="time_change", target_weekday=0, reason="other")
    )
    await db.commit()
    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"] == []
    assert data["unmovable_summary"]["dismissed"] >= 1


@pytest.mark.asyncio
async def test_confirmation_required_blocker_not_moved(client, db) -> None:
    """movability=unknown のブロッカーは曜日跨ぎ退避が確認要 → confirmation_required 計上."""
    admin = await _make_user(db, email="ubconf@example.com")
    office, candidate, _blk = await _seed_single_blocker(db, movability="unknown")
    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"] == []
    assert data["unmovable_summary"]["confirmation_required"] >= 1


# ---------------------------------------------------------------------------
# 同住所ペアの片割れ (容量起因 + 同住所 2 名)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_address_pair_half_not_moved(client, db) -> None:
    """容量満杯コースで同住所ペアの片割れは動かさない → pair 計上."""
    admin = await _make_user(db, email="ubpair@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)
    tue_a = await _mk_course(db, office, staff, 1, "A", ct_a)

    # 同住所ペア 2 名 (PAIR 座標) + 定員を埋める 4 名 (別住所). 計 6 = 満杯.
    pair_coord = (35.6100, 140.1000)
    starts = [time(9, 30), time(9, 30), time(11, 0), time(13, 0), time(13, 45), time(14, 30)]
    coords = [pair_coord, pair_coord] + [(35.602 + i * 0.004, 140.100) for i in range(4)]
    fill = _patient("FILL", FILL, office.id)
    candidate = _patient("TGT", BASE, office.id)
    patients = [_patient(f"B{i}", coords[i], office.id) for i in range(6)]
    db.add_all([fill, candidate, *patients])
    await db.flush()
    for i, p in enumerate(patients):
        await _add_visit_and_pfv(db, p, mon_a, staff, weekday=0, start=starts[i])
    await _add_visit_and_pfv(
        db, fill, tue_a, staff, weekday=1, start=time(9, 30), movability="locked"
    )
    await db.commit()

    body = _candidate_body(office, candidate, time_type="終日", preferred_start=None)
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["unmovable_summary"]["pair"] >= 1


# ---------------------------------------------------------------------------
# 深さ2 (同一バケット内 2 訪問を動かして開通)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth2_two_moves_open_slot(client, db) -> None:
    """固定 10:00 を 2 名 (別住所) が塞ぎ、両方ずらさないと入らない → 2 手プラン."""
    admin = await _make_user(db, email="ubd2@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)
    tue_a = await _mk_course(db, office, staff, 1, "A", ct_a)

    # 10:00 と 10:15 に別住所 2 名. 候補 (BASE) 固定 10:00 は、どちらか片方だけ除いても
    # 残る 1 名との移動制約で入らないよう近接配置し、2 名とも除くと開通させる.
    p1 = _patient("D1", (35.6035, 140.1000), office.id)
    p2 = _patient("D2", (35.6070, 140.1000), office.id)
    fill = _patient("FILL", FILL, office.id)
    candidate = _patient("TGT", BASE, office.id)
    db.add_all([p1, p2, fill, candidate])
    await db.flush()
    await _add_visit_and_pfv(db, p1, mon_a, staff, weekday=0, start=time(10, 0))
    await _add_visit_and_pfv(db, p2, mon_a, staff, weekday=0, start=time(10, 15))
    await _add_visit_and_pfv(
        db, fill, tue_a, staff, weekday=1, start=time(9, 30), movability="locked"
    )
    await db.commit()

    res = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    assert res.status_code == 200, res.text
    data = res.json()
    # 片方だけでは入らない (深さ1 不成立) → 深さ2 (2 手) プランが出る.
    assert data["plans"], data["unmovable_summary"]
    assert any(p["moved_count"] == 2 for p in data["plans"]), data["plans"]


# ---------------------------------------------------------------------------
# 2 名体制ターゲット (ペア開通)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_staff_target_pair_unblock(client, db) -> None:
    """2 名体制候補: Mon A の 10:00 を塞ぐ 1 名を退避すると A+B ペアが開通する."""
    admin = await _make_user(db, email="ub2starget@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    ct_b = await _mk_template(db, office, "B")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)
    mon_b = await _mk_course(db, office, staff, 0, "B", ct_b)
    tue_a = await _mk_course(db, office, staff, 1, "A", ct_a)

    blocker = _patient("BLK", V_COORD, office.id)
    b_fill = _patient("BF", FILL, office.id)  # Mon B に room を残しつつ bucket を実在させる
    tue_fill = _patient("TF", FILL, office.id)
    candidate = _patient("TGT", BASE, office.id, requires_multiple_staff=True)
    db.add_all([blocker, b_fill, tue_fill, candidate])
    await db.flush()
    await _add_visit_and_pfv(db, blocker, mon_a, staff, weekday=0, start=time(10, 0))
    await _add_visit_and_pfv(
        db, b_fill, mon_b, staff, weekday=0, start=time(8, 30), movability="locked"
    )
    await _add_visit_and_pfv(
        db, tue_fill, tue_a, staff, weekday=1, start=time(9, 30), movability="locked"
    )
    await db.commit()

    body = _candidate_body(office, candidate, requires_multiple_staff=True)
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"], data["unmovable_summary"]
    # ペア開通なので insert に相方コース (B) が載る.
    assert any(p["insert"]["partner_course_code"] == "B" for p in data["plans"]), data["plans"]


# ---------------------------------------------------------------------------
# ランキング順 (容量満杯: 複数ブロッカーが total_delta 昇順に並ぶ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranking_by_total_delta(client, db) -> None:
    admin = await _make_user(db, email="ubrank@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)
    tue_a = await _mk_course(db, office, staff, 1, "A", ct_a)

    starts = [time(9, 30), time(10, 15), time(11, 0), time(13, 0), time(13, 45), time(14, 30)]
    coords = [(35.602 + i * 0.004, 140.100) for i in range(6)]
    fill = _patient("FILL", FILL, office.id)
    candidate = _patient("TGT", BASE, office.id)
    patients = [_patient(f"B{i}", coords[i], office.id) for i in range(6)]
    db.add_all([fill, candidate, *patients])
    await db.flush()
    for i, p in enumerate(patients):
        await _add_visit_and_pfv(db, p, mon_a, staff, weekday=0, start=starts[i])
    await _add_visit_and_pfv(
        db, fill, tue_a, staff, weekday=1, start=time(9, 30), movability="locked"
    )
    await db.commit()

    body = _candidate_body(office, candidate, time_type="終日", preferred_start=None)
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    plans = res.json()["plans"]
    assert len(plans) >= 2
    # 全て 1 手なので total_delta 昇順に並ぶ (moved_count 同値のとき total_delta 主キー).
    deltas = [p["total_delta_minutes"] for p in plans if p["moved_count"] == 1]
    assert deltas == sorted(deltas)


# ---------------------------------------------------------------------------
# apply (正常・409・pinned 422 ロールバック・reset 波及・非対象患者不変)
# ---------------------------------------------------------------------------


def _apply_body(office: Office, sim_data: dict, candidate: Patient) -> dict:
    """apply リクエスト: plan_id 先頭 UUID から target_patient_id を復元して明示送付."""
    plan = sim_data["plans"][0]
    # plan_id は "{target_uuid}:{hash}" 形式; 先頭部分を target_patient_id として送る.
    target_id_str = plan["plan_id"].split(":", 1)[0]
    return {
        "office_id": str(office.id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "plan": plan,
        "state_token": sim_data["state_token"],
        "target_patient_id": target_id_str,
    }


@pytest.mark.asyncio
async def test_apply_moves_blocker_and_places_target(client, db) -> None:
    """apply: ブロッカー PFV が Tue へ動き、対象 PFV が Mon 10:00 に生成 + 今週 visit 再生成."""
    admin = await _make_user(db, email="ubapply@example.com")
    office, candidate, blocker = await _seed_single_blocker(db)

    sim = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    sim_data = sim.json()
    assert sim_data["plans"]

    res = await client.post(
        _APPLY_URL, headers=_bearer(admin), json=_apply_body(office, sim_data, candidate)
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["applied_moves"] == 1
    assert data["inserted"] is True

    # ブロッカー PFV が Tue (weekday=1) へ動いた.
    blk_pfv = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == blocker.id)
        )
    ).one()
    assert blk_pfv.weekday == 1

    # 対象 PFV が Mon 10:00 に生成された.
    tgt_pfv = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == candidate.id)
        )
    ).all()
    assert len(tgt_pfv) == 1
    assert tgt_pfv[0].weekday == 0 and tgt_pfv[0].start_time == time(10, 0)

    # reset 波及: 対象患者の今週 Mon visit が生成されている.
    tgt_visits = (
        await db.scalars(
            select(Visit).where(Visit.patient_id == candidate.id, Visit.deleted_at.is_(None))
        )
    ).all()
    assert any(v.visit_date == WEEK_MONDAY for v in tgt_visits)


@pytest.mark.asyncio
async def test_apply_stale_token_409(client, db) -> None:
    admin = await _make_user(db, email="ub409@example.com")
    office, candidate, blocker = await _seed_single_blocker(db)
    sim = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    sim_data = sim.json()
    assert sim_data["plans"]

    # 別管理者がブロッカー PFV を動かした想定 (token が変わる).
    blk_pfv = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == blocker.id)
        )
    ).one()
    blk_pfv.start_time = time(9, 0)
    await db.commit()

    res = await client.post(
        _APPLY_URL, headers=_bearer(admin), json=_apply_body(office, sim_data, candidate)
    )
    assert res.status_code == 409
    await db.refresh(blk_pfv)
    assert blk_pfv.start_time == time(9, 0)  # 何も適用されていない.


@pytest.mark.asyncio
async def test_apply_pinned_crafted_422_rollback(client, db) -> None:
    """pinned ブロッカーを狙った手作りペイロードは 422 で全ロールバック."""
    admin = await _make_user(db, email="ub422@example.com")
    office, candidate, blocker = await _seed_single_blocker(db, movability="locked", is_pinned=True)

    # 現状の token を取得 (plans は空).
    sim = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    sim_data = sim.json()
    assert sim_data["plans"] == []

    # plan_id 先頭に対象患者 id を埋め込む (apply が対象を復元し pinned move 検証まで到達する).
    crafted_plan = {
        "plan_id": f"{candidate.id}:crafted",
        "moves": [
            {
                "patient_id": str(blocker.id),
                "patient_name": blocker.name,
                "from": {"weekday": 0, "course_code": "A", "start_time": "10:00"},
                "to": {"weekday": 1, "course_code": "A", "start_time": "13:00"},
                "delta_minutes": 0,
                "within_preference": False,
            }
        ],
        "insert": {
            "weekday": 0,
            "course_code": "A",
            "start_time": "10:00",
            "end_time": "10:30",
            "partner_course_code": None,
        },
        "total_delta_minutes": 0,
        "moved_count": 1,
    }
    body = {
        "office_id": str(office.id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "plan": crafted_plan,
        "state_token": sim_data["state_token"],
        "target_patient_id": str(candidate.id),
    }

    res = await client.post(_APPLY_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 422, res.text
    # ブロッカー PFV は動いていない (全ロールバック).
    blk_pfv = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == blocker.id)
        )
    ).one()
    assert blk_pfv.weekday == 0 and blk_pfv.start_time == time(10, 0)
    # 対象 PFV も生成されていない.
    tgt = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == candidate.id)
        )
    ).all()
    assert tgt == []


@pytest.mark.asyncio
async def test_apply_non_target_patient_unchanged(client, db) -> None:
    """apply は退避対象と対象患者以外の PFV を変えない (Tue filler は不変)."""
    admin = await _make_user(db, email="ubunch@example.com")
    office, candidate, _blk = await _seed_single_blocker(db)
    # Tue filler の PFV を控える.
    fill_before = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.weekday == 1))
    ).all()
    fill_pids = {r.patient_id for r in fill_before}

    sim = await client.post(_URL, headers=_bearer(admin), json=_candidate_body(office, candidate))
    sim_data = sim.json()
    # 退避先が Tue filler と衝突しない前提で apply.
    res = await client.post(
        _APPLY_URL, headers=_bearer(admin), json=_apply_body(office, sim_data, candidate)
    )
    assert res.status_code == 200, res.text
    # filler 患者 (退避対象でも対象患者でもない) の Tue slot0 PFV が残っている.
    for pid in fill_pids:
        rows = (
            await db.scalars(
                select(PatientFixedVisit).where(
                    PatientFixedVisit.patient_id == pid, PatientFixedVisit.slot_index == 0
                )
            )
        ).all()
        assert any(r.weekday == 1 and r.start_time == time(9, 30) for r in rows)


# ---------------------------------------------------------------------------
# 同一バケット退避 (PO 原体験の再現 + 最終状態検証)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_bucket_time_shift_opens_slot(client, db) -> None:
    """同コース内の時間ずらしで開通 (PO 原体験): Mon A 10:00 の 1 名を Mon A 09:30 に退避。

    - BLOCKER: Mon A 10:00-10:30, movability="time_flexible" (同日のみ退避可)。
    - Tue A は存在しない → 他曜日退避先なし → 同一バケット内が唯一の退避先。
    - 対象患者の preferred_start=10:30, Mon A。
      BLOCKER 10:00-10:30 が対象枠 10:30 を間接ブロック (gap=0 < travel_time)。
      BLOCKER が 09:30 に退避すると gap=30min > travel_time → 開通。
    - プランに「同コース・同曜日内の時間ずらし」が含まれることを確認。
    """
    admin = await _make_user(db, email="ubsameshift@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)

    blocker = _patient("BLK", V_COORD, office.id)
    candidate = _patient("TGT", BASE, office.id)
    db.add_all([blocker, candidate])
    await db.flush()

    # Mon A: BLOCKER のみ (Tue A は作らない → day_change 退避先なし).
    await _add_visit_and_pfv(
        db, blocker, mon_a, staff, weekday=0, start=time(10, 0), movability="time_flexible"
    )
    await db.commit()

    # preferred_start="10:30" — BLOCKER(10:00-10:30)終了直後で gap=0 < travel → 初期は入れない。
    body = _candidate_body(office, candidate, preferred_start="10:30")
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["plans"], f"同コース退避プランが出るはず: unmovable={data['unmovable_summary']}"
    plan = data["plans"][0]
    mv = plan["moves"][0]
    # 同コース・同曜日内の時間ずらし.
    assert mv["from"]["weekday"] == mv["to"]["weekday"] == 0, "同一曜日のまま"
    assert mv["from"]["course_code"] == mv["to"]["course_code"] == "A", "同一コースのまま"
    assert mv["from"]["start_time"] == "10:00"
    assert mv["to"]["start_time"] != "10:00", "別の時刻に退避されている"
    assert plan["insert"]["weekday"] == 0
    assert plan["insert"]["course_code"] == "A"
    assert plan["insert"]["start_time"] == "10:30", "対象患者は 10:30 に配置される"


@pytest.mark.asyncio
async def test_same_bucket_retreat_reoccupy_target_slot_rejected(client, db) -> None:
    """同一バケット退避先が対象の挿入枠と衝突するケースは弾かれる (最終状態検証).

    - Mon A: FILL1/2/3 が 08:00-09:30 を locked で埋め、BLOCKER は 10:00 (time_flexible)。
    - ANCHOR が 11:00 で BLOCKER の退避を 09:30-10:24 の窓内に限定する。
    - 最小 delta 退避先 = 09:30 (早い時刻優先)。BLOCKER 09:30-10:00 + TARGET 10:00 は
      移動時間制約で衝突 (travel(V_COORD→BASE) > 0) → 最終状態検証で弾かれる。
    - Tue A は作らない → day_change 退避先もない → plans == [] が期待値。
    """
    admin = await _make_user(db, email="ubsamereoccupy@example.com")
    office, staff = await _mk_office_staff(db)
    ct_a = await _mk_template(db, office, "A")
    mon_a = await _mk_course(db, office, staff, 0, "A", ct_a)

    # Mon A: 08:00-09:30 を locked visits で埋める (V_COORD 座標 = travel ≈ 0).
    fill1 = _patient("F1", V_COORD, office.id)
    fill2 = _patient("F2", V_COORD, office.id)
    fill3 = _patient("F3", V_COORD, office.id)
    anchor = _patient("ANC", FILL, office.id)  # 11:00 で BLOCKER 退避の上限を画する
    blocker = _patient("BLK", V_COORD, office.id)
    candidate = _patient("TGT", BASE, office.id)
    db.add_all([fill1, fill2, fill3, anchor, blocker, candidate])
    await db.flush()

    await _add_visit_and_pfv(
        db, fill1, mon_a, staff, weekday=0, start=time(8, 0), movability="locked"
    )
    await _add_visit_and_pfv(
        db, fill2, mon_a, staff, weekday=0, start=time(8, 30), movability="locked"
    )
    await _add_visit_and_pfv(
        db, fill3, mon_a, staff, weekday=0, start=time(9, 0), movability="locked"
    )
    await _add_visit_and_pfv(
        db, blocker, mon_a, staff, weekday=0, start=time(10, 0), movability="time_flexible"
    )
    await _add_visit_and_pfv(
        db, anchor, mon_a, staff, weekday=0, start=time(11, 0), movability="locked"
    )
    await db.commit()

    body = _candidate_body(office, candidate)  # preferred_start="10:00", Mon
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    # 同一バケット退避先が全て 10:00 を間接ブロック → 最終状態検証で全弾き.
    same_bucket_plans = [
        p
        for p in data["plans"]
        if any(
            m["from"]["weekday"] == m["to"]["weekday"]
            and m["from"]["course_code"] == m["to"]["course_code"]
            for m in p["moves"]
        )
    ]
    assert same_bucket_plans == [], (
        f"退避先が対象枠を再占有するプランは出ないはず: {same_bucket_plans}"
    )


# ---------------------------------------------------------------------------
# RBAC / 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="ubstaff@example.com", role="staff")
    office, candidate, _blk = await _seed_single_blocker(db)
    res = await client.post(
        _URL, headers=_bearer(staff_user), json=_candidate_body(office, candidate)
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_unknown_office_404(client, db) -> None:
    admin = await _make_user(db, email="ub404@example.com")
    body = {
        "service_minutes": 30,
        "time_type": "固定",
        "preferred_start": "10:00",
        "preferred_weekdays": ["Mon"],
        "lat": BASE[0],
        "lng": BASE[1],
        "existing_patient_id": str(uuid4()),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(uuid4()),
    }
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 404
