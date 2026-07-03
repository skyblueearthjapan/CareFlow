"""Wave N-1: Layer 3 不可避連続の自動確定 / l3_fix_primary_staff フラグ化テスト.

設計書 R-1 / R-2 の回帰テスト:

1. 1名拠点 (スタッフ1名) で連続 → review に出ず自動確定 + notice(single_staff)
2. 代替候補あり (recent でない untaken 適格者が存在) → avoidable = _detect 空 dict
3. 適格者複数だが全員が原因患者の直近担当者 → unavoidable = all_recent
4a. フラグ未設定拠点では primary staff 固定割当が発動しない
4b. フラグON拠点では primary staff が A コースに固定される
5. migration 0051 up/down 冪等性 (SQLite で可)
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, StaffShift, User, Visit
from app.models.course import COURSE_STATUS_COURSE_FIXED, COURSE_STATUS_STAFF_ASSIGNED
from app.models.office_feature_flag import OfficeFeatureFlag
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.visit import VISIT_STATUS_PLANNED
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.scheduling.layer3_assignment import (
    L3_FIX_PRIMARY_STAFF_FEATURE_KEY,
    CourseAssignmentTarget,
    Layer3Assigner,
    Layer3Result,
    StaffAssignment,
    StaffInfo,
)

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 25
TEST_WEEK_MONDAY = date(2026, 6, 15)

PREV_ISO_YEAR = 2026
PREV_ISO_WEEK = 24
PREV_WEEK_MONDAY = date(2026, 6, 8)

_MIGRATION_FILENAME = "0051_l3_fix_primary_staff_flag_seed.py"
_MIGRATION_REVISION = "0051_l3_fix_primary_staff_flag_seed"


# ---------------------------------------------------------------------------
# DB テスト用ヘルパー
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_prev_assignment(
    db,
    *,
    patient_id,
    staff_id,
    office_id,
    weekday: int = 0,
    course_code: str = "B",
) -> None:
    """前週 staff が patient を担当した履歴 (course_status='staff_assigned' + VSA) を作成する."""
    prev_course = Course(
        iso_year=PREV_ISO_YEAR,
        iso_week=PREV_ISO_WEEK,
        weekday=weekday,
        code=course_code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff_id,
        office_id=office_id,
    )
    db.add(prev_course)
    await db.flush()
    prev_date = date.fromordinal(PREV_WEEK_MONDAY.toordinal() + weekday)
    prev_visit = Visit(
        patient_id=patient_id,
        course_id=prev_course.id,
        visit_date=prev_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        primary_staff_id=staff_id,
    )
    db.add(prev_visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=prev_visit.id, staff_id=staff_id))
    await db.flush()


# ---------------------------------------------------------------------------
# テスト 1: 1名拠点 + 連続 → 自動確定 + notice(single_staff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unavoidable_single_staff_auto_committed(client, db) -> None:
    """1名拠点 (スタッフ1名) で連続 → review に出さず自動確定 + notice(single_staff).

    設計書 R-2: 代替候補 0 名 (= 不可避) の連続は auto_committed_notices として
    返し、 courses_assigned に含める. review_items には出さない.
    """
    admin = await _make_user(db, "n1-t1@example.com")
    office = Office(name="N1-T1 拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="N1-T1-S1",
        name="N1 唯一スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    db.add(StaffShift(staff_id=staff.id, weekday=0, is_on=True))

    patient = Patient(
        code="N1-T1-P",
        name="N1 患者",
        status="active",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=60,
        )
    )
    await db.flush()

    # 前週: staff が patient を担当した履歴 → patient_recent_staff[p] = [staff]
    await _seed_prev_assignment(db, patient_id=patient.id, staff_id=staff.id, office_id=office.id)

    # 当該週のコース (PROPOSED)
    course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status="proposed",
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=course.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(10, 0),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()
    course_id = course.id

    res = await client.post(
        "/api/v1/schedule/assign-staff-only",
        headers=_bearer(admin),
        json={"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # 自動確定: courses_assigned == 1
    assert body["courses_assigned"] == 1, (
        f"不可避連続は自動確定されるはず: courses_assigned={body['courses_assigned']}"
    )
    # review_items には出ない
    assert body["review_items"] == [], f"不可避連続は review_items に出ない: {body['review_items']}"
    # auto_committed_notices に 1 件 (single_staff)
    notices = body.get("auto_committed_notices", [])
    assert len(notices) == 1, f"notice 1 件想定: {notices}"
    n = notices[0]
    assert n["course_id"] == str(course_id)
    assert n["course_code"] == "A"
    assert n["weekday"] == 0
    assert n["reason_kind"] == "single_staff"
    assert n["staff_name"] == "N1 唯一スタッフ"
    assert "N1 患者" in n["cause_patient_names"]
    assert n["reason_text"]


# ---------------------------------------------------------------------------
# テスト 2: 代替候補あり → _detect_unavoidable_consecutive が空を返す (avoidable)
# ---------------------------------------------------------------------------


def test_avoidable_consecutive_detect_returns_empty() -> None:
    """代替候補が存在する連続は _detect_unavoidable_consecutive が {} を返す.

    設計書 R-2: 回避可能 (代替候補 >= 1名) なら unavoidable dict は空.
    代替候補 = untaken かつ not in cause_recent かつハード制約 OK.
    """
    pid = uuid4()
    s1_id = uuid4()
    s2_id = uuid4()
    cid = uuid4()

    # s1 が c1 に割当済み (consecutive: s1 は p1 の直近担当者)
    # s2 は untaken + cause_recent に含まれない → 代替候補
    s1 = StaffInfo(
        staff_id=s1_id,
        name="N1 連続スタッフ",
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(range(7)),
    )
    s2 = StaffInfo(
        staff_id=s2_id,
        name="N1 代替スタッフ",
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(range(7)),
    )
    course = CourseAssignmentTarget(
        course_id=cid,
        weekday=0,
        course_code="A",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        patient_ids=[pid],
        office_id=None,  # None → 拠点ハード制約 skip (後方互換)
    )
    assignment = StaffAssignment(weekday=0, course_code="A", course_id=cid, staff_id=s1_id)
    result = Layer3Result(assignments=[assignment])

    assigner = Layer3Assigner()
    unavoidable = assigner._detect_unavoidable_consecutive(
        consecutive_cause_patients={cid: {pid}},
        targets_by_id={cid: course},
        result=result,
        staff_pool=[s1, s2],
        assigned_staff_by_course={cid: s1_id},
        events_by_staff={},
        week_monday=TEST_WEEK_MONDAY,
        history=[],
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        patient_recent_staff={pid: [s1_id]},  # s1 は recent, s2 は recent でない
    )

    # s2 が代替候補 (untaken + not recent for p1 + 全ハード OK) → avoidable → {}
    assert unavoidable == {}, f"代替候補あり連続は avoidable (empty dict) のはず: {unavoidable}"


# ---------------------------------------------------------------------------
# テスト 3: 全適格者が recent → _detect が all_recent を返す (unavoidable)
# ---------------------------------------------------------------------------


def test_all_recent_detect_returns_unavoidable() -> None:
    """全適格者が原因患者の直近担当者 → unavoidable dict に all_recent が入る.

    設計書 R-2: 適格者複数だが全員 recent → reason_kind='all_recent'.
    """
    pid = uuid4()
    s1_id = uuid4()
    s2_id = uuid4()
    cid = uuid4()

    s1 = StaffInfo(
        staff_id=s1_id,
        name="N1 スタッフ甲",
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(range(7)),
    )
    s2 = StaffInfo(
        staff_id=s2_id,
        name="N1 スタッフ乙",
        sex=None,
        role="staff",
        primary_office_lat=None,
        primary_office_lng=None,
        work_days=frozenset(range(7)),
    )
    course = CourseAssignmentTarget(
        course_id=cid,
        weekday=0,
        course_code="A",
        centroid_lat=None,
        centroid_lng=None,
        gender_restrictions=frozenset(),
        patient_ids=[pid],
        office_id=None,
    )
    assignment = StaffAssignment(weekday=0, course_code="A", course_id=cid, staff_id=s1_id)
    result = Layer3Result(assignments=[assignment])

    assigner = Layer3Assigner()
    unavoidable = assigner._detect_unavoidable_consecutive(
        consecutive_cause_patients={cid: {pid}},
        targets_by_id={cid: course},
        result=result,
        staff_pool=[s1, s2],
        assigned_staff_by_course={cid: s1_id},
        events_by_staff={},
        week_monday=TEST_WEEK_MONDAY,
        history=[],
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        # s1 も s2 も p1 の direct recent (cause_recent に両者含まれる)
        patient_recent_staff={pid: [s1_id, s2_id]},
    )

    # 適格者 = {s1, s2} (両者とも eligible), len=2 > 1, 代替なし → all_recent
    assert unavoidable == {cid: "all_recent"}, f"全員 recent なら all_recent のはず: {unavoidable}"


# ---------------------------------------------------------------------------
# テスト 4a: フラグ未設定拠点では primary staff 固定割当が発動しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_off_no_primary_staff_fixed(db) -> None:
    """フラグ未設定拠点では primary staff 固定割当が発動しない."""
    office = Office(name="N1-T4a フラグなし拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()

    staff = Staff(
        code="N1-T4a-S1",
        name="N1-T4a スタッフ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(staff)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))

    # A コース (course_fixed) + visit (G-28 対策: visit=0 なら固定対象除外)
    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(a_course)
    await db.flush()

    patient = Patient(code="N1-T4a-P", name="N1-T4a 患者", status="active")
    db.add(patient)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=a_course.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    # フラグなし → A コースは固定割当対象にならない
    assert a_course.id not in fixed, f"フラグなし拠点の A コースが固定割当に入っている: {fixed}"


# ---------------------------------------------------------------------------
# テスト 4b: フラグON拠点では primary staff が A コースに固定される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flag_on_primary_staff_fixed(db) -> None:
    """フラグON拠点では primary staff (code 昇順先頭) が A コースに固定割当される."""
    office = Office(name="N1-T4b フラグON拠点", lat=35.65, lng=140.0)
    db.add(office)
    await db.flush()
    # フラグ有効化
    db.add(
        OfficeFeatureFlag(
            office_id=office.id,
            feature_key=L3_FIX_PRIMARY_STAFF_FEATURE_KEY,
            enabled_at=datetime.now(tz=UTC),
        )
    )
    await db.flush()

    # primary = role='staff' + code 昇順先頭
    primary = Staff(
        code="N1-T4b-AA",
        name="N1-T4b プライマリ",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(primary)
    await db.flush()
    for wd in range(7):
        db.add(StaffShift(staff_id=primary.id, weekday=wd, is_on=True))

    a_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="A",
        course_status=COURSE_STATUS_COURSE_FIXED,
        office_id=office.id,
    )
    db.add(a_course)
    await db.flush()

    patient = Patient(code="N1-T4b-P", name="N1-T4b 患者", status="active")
    db.add(patient)
    await db.flush()
    db.add(
        Visit(
            patient_id=patient.id,
            course_id=a_course.id,
            visit_date=TEST_WEEK_MONDAY,
            start_time=time(9, 0),
            end_time=time(9, 30),
            type="regular",
            status=VISIT_STATUS_PLANNED,
            source="auto",
            required_staff_count=1,
        )
    )
    await db.commit()

    assigner = Layer3Assigner()
    fixed = await assigner._build_fixed_assignments(
        db, iso_year=TEST_ISO_YEAR, iso_week=TEST_ISO_WEEK, office_id=office.id
    )
    # フラグON → A コースが primary staff (code 昇順先頭) で固定される
    assert fixed.get(a_course.id) == primary.id, (
        f"フラグON拠点の A コースが primary staff で固定されていない: {fixed}"
    )


# ---------------------------------------------------------------------------
# テスト 5: migration 0051 up/down 冪等性 (SQLite)
# ---------------------------------------------------------------------------


def _load_migration_module():
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / _MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("migration_0051", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0051"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prepare_migration_0051_schema(conn: sa.engine.Connection) -> None:  # type: ignore[type-arg]
    """migration 0051 が依存する最小スキーマ (offices + office_feature_flags)."""
    conn.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS offices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                deleted_at TEXT
            )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS office_feature_flags (
                id TEXT PRIMARY KEY,
                office_id TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                enabled_at TEXT,
                enabled_by_user_id TEXT,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    )


def test_migration_0051_up_down_idempotent(tmp_path: Path) -> None:
    """migration 0051 の upgrade/downgrade が冪等であること (SQLite).

    1. upgrade (1回目): 都賀 office にフラグ INSERT, 非都賀 office は無視.
    2. upgrade (2回目, 冪等): 重複 INSERT されない.
    3. downgrade: note 一致の行のみ削除.
    4. downgrade (2回目): エラーなし, 0 件のまま.
    """
    db_path = tmp_path / "migration_0051_test.db"
    engine = create_engine(f"sqlite:///{db_path}")

    migration_mod = _load_migration_module()
    tsuga_id = str(uuid4())
    other_id = str(uuid4())

    with engine.begin() as conn:
        _prepare_migration_0051_schema(conn)
        conn.execute(
            sa.text("INSERT INTO offices (id, name, deleted_at) VALUES (:id, :name, NULL)"),
            {"id": tsuga_id, "name": "テスト 都賀事業所"},
        )
        conn.execute(
            sa.text("INSERT INTO offices (id, name, deleted_at) VALUES (:id, :name, NULL)"),
            {"id": other_id, "name": "テスト 稲毛事業所"},
        )

    def _count_flags(conn, oid: str) -> int:
        return conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM office_feature_flags "
                "WHERE office_id = :oid AND feature_key = :fk"
            ),
            {"oid": oid, "fk": L3_FIX_PRIMARY_STAFF_FEATURE_KEY},
        ).scalar()

    # ----- upgrade 1回目 -----
    with engine.begin() as conn:
        migration_mod._upgrade_with_bind(conn)  # type: ignore[attr-defined]
        assert _count_flags(conn, tsuga_id) == 1, "upgrade後に都賀フラグ1件"
        assert _count_flags(conn, other_id) == 0, "非都賀はフラグなし"

    # ----- upgrade 2回目 (冪等) -----
    with engine.begin() as conn:
        migration_mod._upgrade_with_bind(conn)  # type: ignore[attr-defined]
        assert _count_flags(conn, tsuga_id) == 1, "2回upgrade後も都賀フラグは1件"

    # ----- downgrade -----
    with engine.begin() as conn:
        migration_mod._downgrade_with_bind(conn)  # type: ignore[attr-defined]
        assert _count_flags(conn, tsuga_id) == 0, "downgrade後にフラグが削除"

    # ----- downgrade 2回目 (エラーなし) -----
    with engine.begin() as conn:
        migration_mod._downgrade_with_bind(conn)  # type: ignore[attr-defined]
        assert _count_flags(conn, tsuga_id) == 0, "2回downgrade後も0件"

    engine.dispose()
