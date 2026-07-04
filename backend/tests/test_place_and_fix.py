"""W15-BE-FIXPATTERN (Phase 2 C-1): POST /api/v1/schedule/place-and-fix tests.

Wave 15 で新設された「ドロップ即固定枠化」フロー専用 endpoint の挙動を検証。

検証観点:
  1. 正常系: place-and-fix で visit + fixed_visit が両方作成される
  2. fix_pattern=False: visit のみ作成、fixed_visit は null
  3. RBAC: staff は 403
  4. RBAC: viewer は 403
  5. patient 不存在 → 404
  6. 同一 patient × weekday × normal を連続 call → fixed_visit が upsert される
  7. ISO 週越境 (例: iso_week=53) も正常動作
  8. special_week_active 該当週は mode='special'
  9. start_time + duration_min が 24:00 を越えると 422
  10. weekday の範囲外 (-1 / 7) は 422
  11. patient_id 空文字列など型不正は 422
  12. 不存在 ISO 週 (例: 2025 / week=53) は 422
"""

from __future__ import annotations

from datetime import date, time
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.course import Course
from app.models.course_template import CourseTemplate
from app.models.office import Office
from app.models.patient_fixed_visit import PatientFixedVisit

# 共通テスト週: ISO 2026-W19 (月曜 = 2026-05-04)
TEST_ISO_YEAR = 2026
TEST_ISO_WEEK = 19
TEST_WEEK_MONDAY = date(2026, 5, 4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(
    db,
    code: str,
    *,
    special_week_active: list | None = None,
    status: str = "active",
) -> Patient:
    p = Patient(
        code=code,
        name=f"患者{code}",
        status=status,
        special_week_active=special_week_active or [],
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_office(db, name: str = "事業所A") -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _make_template(db, office_id, label: str = "A") -> CourseTemplate:
    """W15-codex-fix: place-and-fix は course_template_id 必須なので
    テスト helper でテンプレートを作る."""
    tpl = CourseTemplate(
        office_id=office_id,
        label=label,
        capacity_mon=6,
        capacity_tue=6,
        capacity_wed=6,
        capacity_thu=6,
        capacity_fri=6,
        capacity_sat=6,
        capacity_sun=0,
    )
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)
    return tpl


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _payload(
    patient_id,
    *,
    course_template_id=None,
    iso_year: int = TEST_ISO_YEAR,
    iso_week: int = TEST_ISO_WEEK,
    weekday: int = 0,
    start_time: str = "09:00:00",
    duration_min: int = 60,
    staff_count: int = 1,
    fix_pattern: bool = True,
) -> dict:
    return {
        "patient_id": str(patient_id),
        # W15-codex-fix (1): course_template_id 必須化. 呼出側は明示指定するか、
        # uuid4() を渡す (= BE 側で 404 を期待するケース)。
        "course_template_id": str(course_template_id if course_template_id else uuid4()),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "weekday": weekday,
        "start_time": start_time,
        "duration_min": duration_min,
        "staff_count": staff_count,
        "fix_pattern": fix_pattern,
    }


# ---------------------------------------------------------------------------
# 1. 正常系: visit + fixed_visit が両方作成される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_creates_visit_and_fixed_visit(client, db) -> None:
    """fix_pattern=True (default): visit + fixed_visit が両方作成される."""
    admin = await _make_user(db, "paf-1-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-001")
    office = await _make_office(db, "事業所PAF1")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="10:00:00",
            duration_min=45,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # visit
    assert data["visit"] is not None
    assert data["visit"]["patient_id"] == str(patient.id)
    assert data["visit"]["visit_date"] == TEST_WEEK_MONDAY.isoformat()
    assert data["visit"]["start_time"] == "10:00:00"
    assert data["visit"]["end_time"] == "10:45:00"
    assert data["visit"]["status"] == "planned"
    assert data["visit"]["source"] == "manual"
    assert data["visit"]["required_staff_count"] == 1
    # W15-codex-fix (1): visit.course_id が template から派生した course に紐付く
    assert data["visit"]["course_id"] is not None

    # fixed_visit
    assert data["fixed_visit"] is not None
    assert data["fixed_visit"]["mode"] == "normal"
    assert data["fixed_visit"]["weekday"] == 0
    assert data["fixed_visit"]["duration_min"] == 45

    # DB 確認
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 1
    assert visits[0].course_id is not None

    # courses 行が template から派生して 1 件作成される
    courses = (
        await db.scalars(
            select(Course).where(
                Course.template_id == tpl.id,
                Course.iso_year == TEST_ISO_YEAR,
                Course.iso_week == TEST_ISO_WEEK,
                Course.weekday == 0,
            )
        )
    ).all()
    assert len(courses) == 1
    assert courses[0].office_id == office.id
    assert courses[0].code == "A"
    assert visits[0].course_id == courses[0].id

    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].mode == "normal"
    assert fvs[0].weekday == 0


# ---------------------------------------------------------------------------
# 2. fix_pattern=False: visit のみ作成、fixed_visit は null
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_without_pattern_creates_visit_only(client, db) -> None:
    """fix_pattern=False: visit のみ作成、patient_fixed_visits は触らない."""
    admin = await _make_user(db, "paf-2-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-002")
    office = await _make_office(db, "事業所PAF2")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=1,
            start_time="11:00:00",
            fix_pattern=False,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["visit"] is not None
    assert data["fixed_visit"] is None

    # DB: fixed_visits は 0 件
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 0


# ---------------------------------------------------------------------------
# 3. RBAC: staff → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_staff_forbidden(client, db) -> None:
    """staff ロールは place-and-fix を呼べない (403)."""
    staff = await _make_staff(db, "スタッフPAF3")
    staff_user = await _make_user(db, "paf-3-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(staff_user),
        json=_payload(uuid4()),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 4. RBAC: viewer → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_viewer_forbidden(client, db) -> None:
    viewer = await _make_user(db, "paf-4-viewer@example.com", "viewer")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(viewer),
        json=_payload(uuid4()),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 5. patient 不存在 → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_unknown_patient_returns_404(client, db) -> None:
    admin = await _make_user(db, "paf-5-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(uuid4()),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 6. 同一 patient × weekday の連続 call → fixed_visit が upsert される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_upserts_existing_fixed_visit(client, db) -> None:
    """同一 (patient_id, mode='normal', weekday) を 2 回 call すると fixed_visit
    が DELETE→INSERT で 1 行のまま、最新の値で更新される."""
    admin = await _make_user(db, "paf-6-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-006")
    office = await _make_office(db, "事業所PAF6")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    # 1 回目
    r1 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=2,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["fixed_visit"]["duration_min"] == 30

    # 2 回目: 同じ weekday=2 を別時刻 / 別 duration で再投入
    r2 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=2,
            start_time="14:00:00",
            duration_min=60,
        ),
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["fixed_visit"]["duration_min"] == 60
    assert body2["fixed_visit"]["weekday"] == 2

    # DB: weekday=2 / mode='normal' は 1 行 (= 上書きされた)
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
                PatientFixedVisit.weekday == 2,
            )
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].start_time == time(14, 0)
    assert fvs[0].duration_min == 60

    # visits は 2 行作成される (place-and-fix は visit を毎回作る)
    visits = (await db.scalars(select(Visit).where(Visit.patient_id == patient.id))).all()
    assert len(visits) == 2


# ---------------------------------------------------------------------------
# 7. ISO 週越境: 2026-W53 (= 2026-12-28) でも正常動作
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_iso_week_53(client, db) -> None:
    """2026 は 53 週まで存在する → iso_week=53 でも 200."""
    admin = await _make_user(db, "paf-7-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-007")
    office = await _make_office(db, "事業所PAF7")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            iso_year=2026,
            iso_week=53,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # 2026-W53 月曜 = 2026-12-28
    assert data["visit"]["visit_date"] == "2026-12-28"


# ---------------------------------------------------------------------------
# 8. special_week_active 該当週は mode='special'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_special_mode(client, db) -> None:
    admin = await _make_user(db, "paf-8-admin@example.com", "admin")
    patient = await _make_patient(
        db,
        "PAF-008",
        special_week_active=[{"iso_year": TEST_ISO_YEAR, "iso_week": TEST_ISO_WEEK}],
    )
    office = await _make_office(db, "事業所PAF8")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=3,
            start_time="13:00:00",
            duration_min=30,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["fixed_visit"]["mode"] == "special"

    # DB 確認: normal 側は 0 件
    normal_rows = (
        await db.scalars(
            select(PatientFixedVisit).where(
                PatientFixedVisit.patient_id == patient.id,
                PatientFixedVisit.mode == "normal",
            )
        )
    ).all()
    assert len(normal_rows) == 0


# ---------------------------------------------------------------------------
# 9. start_time + duration_min > 24:00 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_overflow_24h_returns_422(client, db) -> None:
    admin = await _make_user(db, "paf-9-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-009")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=0, start_time="23:30:00", duration_min=60),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 10. weekday 範囲外 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_weekday_out_of_range(client, db) -> None:
    admin = await _make_user(db, "paf-10-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-010")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, weekday=7),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 11. patient_id 空文字列 → 422 (UUID バリデーション)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_empty_patient_id_returns_422(client, db) -> None:
    """patient_id='' は UUID バリデーションで 422."""
    admin = await _make_user(db, "paf-11-admin@example.com", "admin")
    payload = _payload(uuid4())
    payload["patient_id"] = ""
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=payload,
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 12. 不存在 ISO 週 (2025-W53 は存在しない) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_invalid_iso_week_returns_422(client, db) -> None:
    """2025 は 52 週までなので W53 は ValueError → 422."""
    admin = await _make_user(db, "paf-12-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-012")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, iso_year=2025, iso_week=53),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 13. W15-codex-fix (1): 不明な course_template_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_unknown_template_returns_404(client, db) -> None:
    """course_template_id が存在しない場合は 404 (W15-codex-fix)."""
    admin = await _make_user(db, "paf-13-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-013")
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(patient.id, course_template_id=uuid4()),
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 14. W15-codex-fix (1): 同 (template, week, weekday) を 2 回 call → course は 1 行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_reuses_existing_course(client, db) -> None:
    """同じ (template_id, iso_year, iso_week, weekday) で 2 回 call すると、
    courses 行は 1 件のまま再利用され、複数 visit が同じ course_id を持つ
    (W15-codex-fix)."""
    admin = await _make_user(db, "paf-14-admin@example.com", "admin")
    p1 = await _make_patient(db, "PAF-014a")
    p2 = await _make_patient(db, "PAF-014b")
    office = await _make_office(db, "事業所PAF14")
    tpl = await _make_template(db, office.id, label="A")
    p1.primary_office_id = office.id
    p2.primary_office_id = office.id
    await db.commit()

    r1 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            p1.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="09:00:00",
        ),
    )
    assert r1.status_code == 200
    course_id_1 = r1.json()["visit"]["course_id"]

    r2 = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            p2.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="10:00:00",
        ),
    )
    assert r2.status_code == 200
    course_id_2 = r2.json()["visit"]["course_id"]

    # 同じ (template, week, weekday) なので course_id は一致 (再利用)
    assert course_id_1 == course_id_2

    # courses 行は 1 件のみ
    courses = (
        await db.scalars(
            select(Course).where(
                Course.template_id == tpl.id,
                Course.iso_year == TEST_ISO_YEAR,
                Course.iso_week == TEST_ISO_WEEK,
                Course.weekday == 0,
            )
        )
    ).all()
    assert len(courses) == 1


# ---------------------------------------------------------------------------
# 15. W15-codex-fix race condition: savepoint + IntegrityError 回復の直接検証
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_concurrent_course_creation(_engine, db) -> None:
    """_get_or_create_course_for_template_week の savepoint+IntegrityError 回復を
    直接テストする。

    1 つ目の呼び出しで Course を INSERT し commit。その後 2 つ目の呼び出しで
    同一 (template_id, iso_year, iso_week, weekday) を INSERT しようとすると
    IntegrityError が発生し、savepoint rollback → 再 SELECT で既存 Course を
    返すことを確認する (race-safe パターンの検証)。

    注意: SQLite in-memory + aiosqlite は単一接続のため asyncio.gather では
    本当の同時実行にはならない。ここでは savepoint 回復コードパスを直接呼び
    出すことで動作を確実に検証する。
    """
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    from app.api.v1.schedule import _get_or_create_course_for_template_week
    from app.db.session import get_session_factory

    office = await _make_office(db, "事業所PAF15")
    tpl = await _make_template(db, office.id, label="A")

    # 1 回目の呼び出し: 正常に Course を作成
    factory = get_session_factory()
    async with factory() as session1:
        course1 = await _get_or_create_course_for_template_week(
            session1,
            course_template_id=tpl.id,
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=4,
        )
        await session1.commit()

    # 2 回目の呼び出し: begin_nested が IntegrityError を発生させた場合の
    # 回復パスを検証するため、flush() を IntegrityError に差し替えてモック
    async with factory() as session2:
        original_begin_nested = session2.begin_nested

        call_count = {"n": 0}

        class _FakeSavepoint:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def _patched_begin_nested():
            """始めて呼ばれたとき IntegrityError を再現するモック savepoint."""
            call_count["n"] += 1
            if call_count["n"] == 1:
                # 実際の IntegrityError をシミュレート
                raise SAIntegrityError(
                    "mocked",
                    {},
                    Exception("UNIQUE constraint failed: courses"),
                )
            return await original_begin_nested()

        # begin_nested を差し替えて IntegrityError 経路を通す
        with patch.object(session2, "begin_nested", side_effect=_patched_begin_nested):
            course2 = await _get_or_create_course_for_template_week(
                session2,
                course_template_id=tpl.id,
                iso_year=TEST_ISO_YEAR,
                iso_week=TEST_ISO_WEEK,
                weekday=4,
            )

    # IntegrityError 後の再 SELECT で同じ Course が返る
    assert course1.id == course2.id, (
        "savepoint+IntegrityError 回復で 1 回目と同じ Course が返るはず"
    )

    # courses テーブルに 1 行のみ (重複 INSERT なし)
    courses = (
        await db.scalars(
            select(Course).where(
                Course.template_id == tpl.id,
                Course.iso_year == TEST_ISO_YEAR,
                Course.iso_week == TEST_ISO_WEEK,
                Course.weekday == 4,
            )
        )
    ).all()
    assert len(courses) == 1


# ---------------------------------------------------------------------------
# 16. W18 Phase 0-b: 既存の不整合 course (template-E, code='M') と UNIQUE 衝突しない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_recovers_from_w16_code_label_mismatch(client, db) -> None:
    """W18 Phase 0-b 回帰テスト: Wave 16 デプロイ時の "E→M 丸め" バグで残った
    不整合 course (template-E, code='M') がある状態で、本店-M template から
    place-and-fix を呼ぶと UNIQUE 衝突せず 200 を返す。

    本番 (2026-W18) で発生した症状:
      asyncpg.UniqueViolationError: duplicate key value violates unique
      constraint "uq_courses_year_week_weekday_code_office"
      DETAIL: Key (iso_year, iso_week, weekday, code, office_id)=
              (2026, 19, 0, M, <office>) already exists.

    再現シナリオ:
      1. 本店 (office) に course_template tpl_e (label='E') と tpl_m (label='M')
      2. 既に courses に (template_id=tpl_e.id, code='M', y=2026, w=19, wd=0)
         の不整合行が入っている (W16 残骸)
      3. tpl_m を course_template_id に place-and-fix を call すると、
         helper の 1st SELECT (template_id=tpl_m) は miss、 2nd SELECT
         (office, code='M', ...) で既存の不整合行を拾い、
         template_id を tpl_m に書き換えて return する。500 にならない。
    """
    from app.models.course import COURSE_STATUS_PROPOSED

    admin = await _make_user(db, "paf-16-admin@example.com", "admin")
    patient = await _make_patient(db, "PAF-016")
    office = await _make_office(db, "事業所PAF16")
    tpl_e = await _make_template(db, office.id, label="E")
    tpl_m = await _make_template(db, office.id, label="M")
    patient.primary_office_id = office.id
    await db.commit()

    # W16 残骸を直接 INSERT: template-E から派生したが code='M' で保存された不整合行.
    bad_course = Course(
        iso_year=TEST_ISO_YEAR,
        iso_week=TEST_ISO_WEEK,
        weekday=0,
        code="M",
        course_status=COURSE_STATUS_PROPOSED,
        template_id=tpl_e.id,
        office_id=office.id,
    )
    db.add(bad_course)
    await db.commit()
    await db.refresh(bad_course)
    bad_course_id = bad_course.id

    # tpl_m (label='M') で place-and-fix を call. helper は UNIQUE key で既存行を
    # 拾い、template_id を tpl_m に書き換えて return するため 200 を返す。
    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl_m.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=45,
        ),
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["visit"]["course_id"] == str(bad_course_id), (
        "既存の (office, code='M', wd=0) row を再利用するはず"
    )

    # courses 行は依然 1 件のみ (新規 INSERT は発生していない).
    # 別セッションで確認 — API が別 transaction で commit 済みなので、
    # test session の identity-map をバイパスする目的で fresh session を作る.
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as verify_session:
        courses = (
            await verify_session.scalars(
                select(Course).where(
                    Course.office_id == office.id,
                    Course.iso_year == TEST_ISO_YEAR,
                    Course.iso_week == TEST_ISO_WEEK,
                    Course.weekday == 0,
                    Course.code == "M",
                )
            )
        ).all()
    assert len(courses) == 1, f"expected 1 course but got {len(courses)}"
    # template_id が tpl_m に補正されている
    assert courses[0].template_id == tpl_m.id, (
        "不整合 course の template_id は呼び出し側 (tpl_m) に補正されるはず"
    )


# ---------------------------------------------------------------------------
# 17. W18 Phase 0-b: UNIQUE 衝突 fallback で IntegrityError 回路も既存行を拾う
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_helper_falls_back_to_unique_key_on_integrity_error(
    _engine, db
) -> None:
    """Phase 0-b: helper の 2nd SELECT (UNIQUE key 引き) で既存行を拾う経路と、
    IntegrityError catch 後の再 SELECT が UNIQUE key で実行されることを検証.

    シナリオ: 既存の不整合行 (template=tpl_e, code='M') が courses にある状態で
    tpl_m (label='M') から helper を呼ぶ。1st SELECT (template_id=tpl_m) は
    miss、2nd SELECT (office_id, code='M', ...) で既存行が見つかり、
    template_id を tpl_m に補正して return する。
    """
    from app.api.v1.schedule import _get_or_create_course_for_template_week
    from app.db.session import get_session_factory
    from app.models.course import COURSE_STATUS_PROPOSED

    office = await _make_office(db, "事業所PAF17")
    tpl_e = await _make_template(db, office.id, label="E")
    tpl_m = await _make_template(db, office.id, label="M")

    # 既存の不整合行 (template=tpl_e, code='M') を直接 INSERT.
    factory = get_session_factory()
    async with factory() as session_seed:
        bad = Course(
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=2,
            code="M",
            course_status=COURSE_STATUS_PROPOSED,
            template_id=tpl_e.id,
            office_id=office.id,
        )
        session_seed.add(bad)
        await session_seed.commit()
        bad_id = bad.id

    # helper を tpl_m (label='M') で呼ぶ. 2nd SELECT (UNIQUE key) で既存行を拾うはず.
    async with factory() as session1:
        course = await _get_or_create_course_for_template_week(
            session1,
            course_template_id=tpl_m.id,
            iso_year=TEST_ISO_YEAR,
            iso_week=TEST_ISO_WEEK,
            weekday=2,
        )
        await session1.commit()

    assert course.id == bad_id, "2nd SELECT で既存の不整合行が拾えるはず"

    # template_id が tpl_m に補正されている (別セッションで verify)
    async with factory() as verify_session:
        verify_course = await verify_session.get(Course, bad_id)
        assert verify_course is not None
        assert verify_course.template_id == tpl_m.id, (
            "2nd SELECT 経由で template_id が呼出側 (tpl_m) に補正されるはず"
        )

    # courses は (office, code='M', wd=2) について 1 件のみ (新規 INSERT 無し)
    async with factory() as verify_session2:
        courses = (
            await verify_session2.scalars(
                select(Course).where(
                    Course.office_id == office.id,
                    Course.iso_year == TEST_ISO_YEAR,
                    Course.iso_week == TEST_ISO_WEEK,
                    Course.weekday == 2,
                    Course.code == "M",
                )
            )
        ).all()
    assert len(courses) == 1, f"helper が 2nd SELECT で既存行を再利用するはず, got {len(courses)}"


# ---------------------------------------------------------------------------
# W18 Codex-fix 中-4: クロス-office チェック
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_cross_office_returns_422(client, db) -> None:
    """patient.primary_office_id と course_template.office_id が一致しない
    ドロップは 422 を返す (W18 Codex-fix 中-4)."""
    admin = await _make_user(db, "paf-cross-1@example.com", "admin")
    office_a = await _make_office(db, "事業所A-cross")
    office_b = await _make_office(db, "事業所B-cross")

    # patient は office_a に紐付く
    patient = Patient(
        code="PAF-CROSS-1",
        name="患者cross1",
        status="active",
        primary_office_id=office_a.id,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    # template は office_b
    tpl_b = await _make_template(db, office_b.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl_b.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    assert res.status_code == 422, res.text
    assert "primary_office_id" in res.text or "cross-office" in res.text


@pytest.mark.asyncio
async def test_place_and_fix_same_office_succeeds(client, db) -> None:
    """patient.primary_office_id と course_template.office_id が一致するときは
    通常通り 200 を返す (中-4 のリグレッション防止)."""
    admin = await _make_user(db, "paf-cross-2@example.com", "admin")
    office = await _make_office(db, "事業所同一")

    patient = Patient(
        code="PAF-CROSS-2",
        name="患者cross2",
        status="active",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    tpl = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_place_and_fix_null_primary_office_returns_422(client, db) -> None:
    """W-6: patient.primary_office_id が NULL の患者は週次生成から除外されるため、
    place-and-fix で 422 ブロックする (旧: skip して 200 → 新: 明確に拒否)."""
    admin = await _make_user(db, "paf-cross-3@example.com", "admin")
    office = await _make_office(db, "事業所null")

    # primary_office_id 未設定 (NULL)
    patient = Patient(
        code="PAF-CROSS-3",
        name="患者cross3",
        status="active",
    )
    db.add(patient)
    await db.commit()
    await db.refresh(patient)

    tpl = await _make_template(db, office.id, label="A")

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
        ),
    )
    # primary_office_id が None → 422 で明確にブロック
    assert res.status_code == 422, res.text
    assert "主担当拠点が未設定" in res.text


# ---------------------------------------------------------------------------
# W22 Phase A-4: place-and-fix で pfv.course_template_id を保存
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_and_fix_saves_course_template_id_on_pfv(client, db) -> None:
    """fix_pattern=True で place-and-fix を呼ぶと、
    patient_fixed_visits.course_template_id に course_template_id が保存される (W22)."""
    admin = await _make_user(db, "paf-w22-1@example.com", "admin")
    patient = await _make_patient(db, "PAF-W22-1")
    office = await _make_office(db, "事業所W22-1")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=0,
            start_time="09:00:00",
            duration_min=30,
            fix_pattern=True,
        ),
    )
    assert res.status_code == 200, res.text

    # DB: pfv に course_template_id が保存されている
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 1
    assert fvs[0].course_template_id == tpl.id, (
        f"pfv.course_template_id は {tpl.id} のはず, got {fvs[0].course_template_id}"
    )


@pytest.mark.asyncio
async def test_place_and_fix_no_pattern_does_not_save_course_template_id(client, db) -> None:
    """fix_pattern=False では pfv が作られないので course_template_id 保存もない (W22)."""
    admin = await _make_user(db, "paf-w22-2@example.com", "admin")
    patient = await _make_patient(db, "PAF-W22-2")
    office = await _make_office(db, "事業所W22-2")
    tpl = await _make_template(db, office.id, label="A")
    patient.primary_office_id = office.id
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/place-and-fix",
        headers=_bearer(admin),
        json=_payload(
            patient.id,
            course_template_id=tpl.id,
            weekday=1,
            start_time="10:00:00",
            duration_min=30,
            fix_pattern=False,
        ),
    )
    assert res.status_code == 200, res.text

    # pfv は作成されない
    fvs = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(fvs) == 0
