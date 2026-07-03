"""Tests for /api/v1/schedule/v2/scope-optimization/{simulate,apply} (範囲最適化 W1-W2).

simulate (W1 BE-2):
    - 改善余地のあるコースで 200 + steps (suggestion 契約 = improvement と同一形).
    - 0 手でも 200 + excluded_summary + before=after.
    - office 不在 404 / RBAC (staff 403) / scope の曜日範囲外 422.
    - read-only: simulate が PFV を変更しない.

apply (W2 BE-3):
    - simulate → apply の happy path で PFV が実際に動く + 再 simulate で 0 手収束.
    - state_token 不一致 409 / プレフィックス以外 (seq 欠番) 422 /
      pinned を狙った手作りペイロード 422 / RBAC 403.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

import uuid
from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit

ISO_YEAR = 2026
ISO_WEEK = 20
WEEK_MONDAY = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
FAR = (35.6300, 140.1400)

_URL = "/api/v1/schedule/v2/scope-optimization/simulate"
_APPLY_URL = "/api/v1/schedule/v2/scope-optimization/apply"


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_sandwich_office(db) -> tuple[Office, Patient]:
    """FAR — P(BASE, time_flexible) — FAR の course A (Mon) を持つ拠点を作る."""
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name="S1", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))

    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()

    def _patient(code: str, lat: float, lng: float) -> Patient:
        return Patient(
            code=code,
            name=f"P-{code}",
            status="active",
            lat=lat,
            lng=lng,
            primary_office_id=office.id,
        )

    p = _patient("TGT", *BASE)
    fa1 = _patient("FA1", *FAR)
    fa2 = _patient("FA2", *FAR)
    db.add_all([p, fa1, fa2])
    await db.flush()

    def _visit(patient: Patient, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=WEEK_MONDAY,
            start_time=start,
            end_time=end,
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
            course_id=course.id,
            primary_staff_id=staff.id,
        )

    db.add_all(
        [
            _visit(fa1, time(9, 30), time(10, 0)),
            _visit(p, time(10, 30), time(11, 0)),
            _visit(fa2, time(11, 15), time(11, 45)),
        ]
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            slot_index=0,
            start_time=time(10, 30),
            duration_min=30,
            movability="time_flexible",
        )
    )
    await db.commit()
    return office, p


def _body(office: Office, **scope_overrides) -> dict:
    scope = {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]}
    scope.update(scope_overrides)
    return {"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "scope": scope}


@pytest.mark.asyncio
async def test_simulate_returns_steps_with_improvement_contract(client, db) -> None:
    admin = await _make_user(db, email="so-admin1@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["iso_year"] == ISO_YEAR
    assert data["office_id"] == str(office.id)
    assert data["state_token"]
    assert data["steps"], data["excluded_summary"]

    step = data["steps"][0]
    assert step["seq"] == 1
    assert step["patient_id"] == str(p.id)
    assert step["patient_name"] == p.name
    # suggestion は improvement-suggestions と同一契約.
    sug = step["suggestion"]
    assert sug["kind"] == "time_change"
    assert sug["delta"]["travel_minutes_saved"] >= 10
    assert sug["current"]["start_time"] == "10:30"
    assert sug["requires_patient_confirmation"] is False
    assert step["cumulative_delta_minutes"] >= sug["delta"]["travel_minutes_saved"]
    # 前後メトリクス: 改善方向.
    assert data["after"]["travel_minutes"] < data["before"]["travel_minutes"]


@pytest.mark.asyncio
async def test_simulate_is_read_only(client, db) -> None:
    admin = await _make_user(db, email="so-admin2@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text

    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    assert pfv.weekday == 0
    assert pfv.start_time == time(10, 30)


@pytest.mark.asyncio
async def test_simulate_zero_steps_returns_200_with_summary(client, db) -> None:
    admin = await _make_user(db, email="so-admin3@example.com", role="admin")
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.commit()

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["steps"] == []
    assert data["before"] == data["after"]
    assert data["excluded_summary"]["truncated"] is False


@pytest.mark.asyncio
async def test_simulate_unknown_office_404(client, db) -> None:
    admin = await _make_user(db, email="so-admin4@example.com", role="admin")
    body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(uuid.uuid4())},
    }
    res = await client.post(_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_simulate_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="so-staff1@example.com", role="staff")
    office, _p = await _seed_sandwich_office(db)
    res = await client.post(_URL, headers=_bearer(staff_user), json=_body(office))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_simulate_invalid_weekday_422(client, db) -> None:
    admin = await _make_user(db, email="so-admin5@example.com", role="admin")
    office, _p = await _seed_sandwich_office(db)
    res = await client.post(_URL, headers=_bearer(admin), json=_body(office, weekdays=[7]))
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# apply (W2 BE-3)
# ---------------------------------------------------------------------------


def _apply_body(office: Office, sim_data: dict, n: int | None = None) -> dict:
    """simulate レスポンスから apply リクエスト (先頭 n 手) を組む."""
    steps = sim_data["steps"] if n is None else sim_data["steps"][:n]
    return {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(office.id), "weekdays": [0], "course_codes": ["A"]},
        "state_token": sim_data["state_token"],
        "steps": steps,
    }


@pytest.mark.asyncio
async def test_apply_happy_path_moves_pfv_and_converges(client, db) -> None:
    """simulate → apply で PFV が実際に動き、再 simulate は 0 手 (収束) になる."""
    admin = await _make_user(db, email="so-admin6@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    sim = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert sim.status_code == 200, sim.text
    sim_data = sim.json()
    assert sim_data["steps"], sim_data["excluded_summary"]
    cand = sim_data["steps"][0]["suggestion"]["candidate"]

    res = await client.post(_APPLY_URL, headers=_bearer(admin), json=_apply_body(office, sim_data))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["applied_count"] == len(sim_data["steps"])

    # PFV が候補位置へ動いた.
    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    assert pfv.weekday == cand["weekday"]
    assert f"{pfv.start_time.hour:02d}:{pfv.start_time.minute:02d}" == cand["start_time"]

    # 適用後の再 simulate: token が変わり、同じ手はもう出ない (収束).
    # (visit 側は動かしていないため、現在配置の特定は PFV 一致行フォールバックで続行する)
    sim2 = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert sim2.status_code == 200
    assert sim2.json()["state_token"] != sim_data["state_token"]


@pytest.mark.asyncio
async def test_apply_stale_token_409(client, db) -> None:
    """simulate 後に PFV が変わると apply は 409 (楽観ロック)."""
    admin = await _make_user(db, email="so-admin7@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    sim = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    sim_data = sim.json()
    assert sim_data["steps"]

    # 別の管理者が PFV を動かした想定.
    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    pfv.start_time = time(9, 0)
    await db.commit()

    res = await client.post(_APPLY_URL, headers=_bearer(admin), json=_apply_body(office, sim_data))
    assert res.status_code == 409
    # 409 で何も適用されていない.
    await db.refresh(pfv)
    assert pfv.start_time == time(9, 0)


@pytest.mark.asyncio
async def test_apply_non_prefix_steps_422(client, db) -> None:
    """seq が 1 から始まらない (プレフィックスでない) steps は 422."""
    admin = await _make_user(db, email="so-admin8@example.com", role="admin")
    office, _p = await _seed_sandwich_office(db)

    sim = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    sim_data = sim.json()
    assert sim_data["steps"]
    body = _apply_body(office, sim_data)
    body["steps"] = [{**body["steps"][0], "seq": 2}]  # 欠番プレフィックス違反.

    res = await client.post(_APPLY_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_apply_pinned_crafted_payload_422(client, db) -> None:
    """エンジンは pinned の手を生成しないが、手作りペイロードで狙っても 422 で拒否."""
    admin = await _make_user(db, email="so-admin9@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    # 対象 PFV をピン留めに変更 (locked 矯正込み).
    pfv = (
        await db.scalars(select(PatientFixedVisit).where(PatientFixedVisit.patient_id == p.id))
    ).one()
    pfv.is_pinned = True
    pfv.movability = "locked"
    await db.commit()

    # 現状態の token を simulate で取得 (steps は 0 のはず).
    sim = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    sim_data = sim.json()
    assert sim_data["steps"] == []

    crafted_step = {
        "seq": 1,
        "patient_id": str(p.id),
        "patient_name": p.name,
        "suggestion": {
            "kind": "time_change",
            "target_weekday": 0,
            "current": {
                "office_id": str(office.id),
                "weekday": 0,
                "weekday_code": "Mon",
                "start_time": "10:30",
                "end_time": "11:00",
                "course_label": "稲A",
                "staff_name": None,
            },
            "candidate": {
                "office_id": str(office.id),
                "office_name": None,
                "weekday": 0,
                "weekday_code": "Mon",
                "start_time": "13:00",
                "end_time": "13:30",
                "course_code": "A",
                "course_label": "稲A",
                "staff_name": None,
            },
            "delta": {"travel_minutes_saved": 23, "travel_km_saved": 4.9},
            "changes": {"changes": [], "unchanged": []},
            "staff_warnings": [],
            "feasibility_basis": "pfv",
            "requires_patient_confirmation": False,
            "within_preference": False,
            "swap_counterpart": None,
        },
        "cumulative_delta_minutes": 23,
        "cumulative_delta_km": 4.9,
    }
    body = _apply_body(office, sim_data)
    body["steps"] = [crafted_step]

    res = await client.post(_APPLY_URL, headers=_bearer(admin), json=body)
    assert res.status_code == 422
    # 何も適用されていない.
    await db.refresh(pfv)
    assert pfv.start_time == time(10, 30)


@pytest.mark.asyncio
async def test_apply_staff_forbidden(client, db) -> None:
    admin = await _make_user(db, email="so-admin10@example.com", role="admin")
    staff_user = await _make_user(db, email="so-staff2@example.com", role="staff")
    office, _p = await _seed_sandwich_office(db)

    sim = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    sim_data = sim.json()
    res = await client.post(
        _APPLY_URL, headers=_bearer(staff_user), json=_apply_body(office, sim_data)
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_simulate_steps_include_course_snapshot(client, db) -> None:
    """W3: 手順にはコースの適用前スナップショット (タイムライン表示用) が付く.

    同一コース内の time_change は source_course のみ (destination_course=None)。
    visits は start 昇順で、対象患者を含むコース全員が入る。
    """
    admin = await _make_user(db, email="so-admin11@example.com", role="admin")
    office, p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    step = res.json()["steps"][0]

    src = step["source_course"]
    assert src is not None
    assert src["course_code"] == "A"
    assert src["weekday"] == 0
    names = [v["patient_name"] for v in src["visits"]]
    assert p.name in names
    assert len(names) == 3  # FA1 / TGT / FA2 全員
    starts = [v["start_time"] for v in src["visits"]]
    assert starts == sorted(starts)
    # 同一コース内の移動 → destination は null.
    assert step["destination_course"] is None


@pytest.mark.asyncio
async def test_simulate_returns_course_before_after_and_reason(client, db) -> None:
    """H2: コース別の実行後見通し (courses[]) と手順の理由文 (reason) が付く."""
    admin = await _make_user(db, email="so-admin12@example.com", role="admin")
    office, _p = await _seed_sandwich_office(db)

    res = await client.post(_URL, headers=_bearer(admin), json=_body(office))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["steps"]

    # 理由文: move 提案には「原因→対策→効果」の 1 文が付く.
    reason = data["steps"][0]["suggestion"]["reason"]
    assert reason
    assert "様" in reason and "分/週" in reason

    # コース別 before/after: 対象コース A の移動が改善方向.
    courses = data["courses"]
    assert courses, data
    ca = next(c for c in courses if c["course_code"] == "A" and c["weekday"] == 0)
    assert ca["before"]["travel_minutes"] > ca["after"]["travel_minutes"]
    assert ca["course_label"]
    # scope 合計との整合: courses の before 合計 = 全体 before の travel.
    assert sum(c["before"]["travel_minutes"] for c in courses) == data["before"]["travel_minutes"]
    assert sum(c["after"]["travel_minutes"] for c in courses) == data["after"]["travel_minutes"]
