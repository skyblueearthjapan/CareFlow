"""主担当 NULL × コース担当フォールバックの可視性 (B-2 / 2026-09-16).

正典 = ``docs/plans/mobile-staff-schedule-design-2026-09-16.md`` §2 B-2。
調査 = ``docs/plans/mobile-staff-schedule-mismatch-investigation-2026-09-16.md`` §6-§7。

背景: 週生成 → コース担当の後付け の順で作られた訪問は ``visits.primary_staff_id``
が NULL のまま残る。PC の職員スケジュールタブは primary → コース担当 の順で帰属を
決めるのに、スマホ (``/api/v1/visits``) は primary 系しか見ていなかったため、本人の
「今日 / 今週」から丸ごと消えていた (本番 22 件)。

ここで縛るのは 2 つ:
  1. 「主担当 NULL かつコース担当 = 自分」の訪問が一覧 / 詳細 / 打刻で通ること
  2. **他人の訪問が見える方向には一切緩まない** こと (コース担当が他人なら 404)
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.course import Course
from app.models.office import Office

JST = ZoneInfo("Asia/Tokyo")

# 2026-W36 水曜 (テスト週)。一覧の週フィルタに使うだけで当日判定には使わない。
WEEK_MONDAY = date(2026, 8, 31)
VISIT_DAY = date(2026, 9, 2)


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff_user(db, email: str, name: str) -> tuple[Staff, User]:
    staff = Staff(name=name)
    db.add(staff)
    await db.flush()
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role="staff",
        staff_id=staff.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(staff)
    await db.refresh(user)
    return staff, user


async def _make_course(db, *, office: Office, assigned_staff_id, weekday: int, code: str) -> Course:
    course = Course(
        iso_year=2026,
        iso_week=36,
        weekday=weekday,
        code=code,
        office_id=office.id,
        assigned_staff_id=assigned_staff_id,
        course_status="staff_assigned",
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return course


async def _make_visit(db, *, patient: Patient, course: Course, visit_date=VISIT_DAY) -> Visit:
    """主担当 NULL・コース所属のみの訪問 (= 本番で 22 件出たかたち)."""
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=None,
        course_id=course.id,
        visit_date=visit_date,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


async def _make_patient(db, code: str) -> Patient:
    p = Patient(code=code, name="利用者")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_office(db, name: str) -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


# ---------------------------------------------------------------------------
# 出る方向
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_visits_includes_course_assigned_visit_for_staff(client, db) -> None:
    """staff ロール本人の一覧に「主担当 NULL・コース担当 = 自分」の訪問が出る."""
    staff, user = await _make_staff_user(db, "cf-list@example.com", "看護フォール")
    office = await _make_office(db, "事業所CF1")
    patient = await _make_patient(db, "CF-1")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    visit = await _make_visit(db, patient=patient, course=course)

    res = await client.get(
        f"/api/v1/visits?week_start={WEEK_MONDAY}&week_end={VISIT_DAY}",
        headers=_bearer(user),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert [r["id"] for r in rows] == [str(visit.id)]
    # primary_staff_id は NULL のまま返す (DB の値を偽らない)。表示名だけ補う。
    assert rows[0]["primary_staff_id"] is None
    assert rows[0]["staff_name"] == "看護フォール"


@pytest.mark.asyncio
async def test_list_visits_admin_staff_id_filter_uses_same_rule(client, db) -> None:
    """admin が ``?staff_id=`` で絞るとき (スマホの me ビュー) も同じ規則になる."""
    staff, _ = await _make_staff_user(db, "cf-admin-target@example.com", "看護対象")
    admin = User(
        email="cf-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    office = await _make_office(db, "事業所CF2")
    patient = await _make_patient(db, "CF-2")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    visit = await _make_visit(db, patient=patient, course=course)

    res = await client.get(
        f"/api/v1/visits?staff_id={staff.id}&week_start={WEEK_MONDAY}&week_end={VISIT_DAY}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert [r["id"] for r in res.json()] == [str(visit.id)]


@pytest.mark.asyncio
async def test_get_visit_allows_course_assigned_staff(client, db) -> None:
    """一覧に出るのに詳細が 404 になる食い違いを作らない."""
    staff, user = await _make_staff_user(db, "cf-get@example.com", "看護詳細")
    office = await _make_office(db, "事業所CF3")
    patient = await _make_patient(db, "CF-3")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    visit = await _make_visit(db, patient=patient, course=course)

    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["primary_staff_id"] is None
    assert res.json()["staff_name"] == "看護詳細"


@pytest.mark.asyncio
async def test_checkin_allows_course_assigned_staff(client, db) -> None:
    """スマホに出るのに打刻だけ 404、にならない (当日の訪問で検証)."""
    staff, user = await _make_staff_user(db, "cf-checkin@example.com", "看護打刻")
    office = await _make_office(db, "事業所CF4")
    patient = await _make_patient(db, "CF-4")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    today = datetime.now(JST).date()
    visit = await _make_visit(db, patient=patient, course=course, visit_date=today)

    res = await client.post(f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "in_progress"


# ---------------------------------------------------------------------------
# 緩まない方向 (他人の訪問は見えない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_other_staffs_course_visit_stays_invisible(client, db) -> None:
    """コース担当が他人なら 一覧に出ない / 詳細 404 / 打刻 404 (可視性は緩まない)."""
    _owner, _owner_user = await _make_staff_user(db, "cf-owner@example.com", "看護オーナー")
    _other, other_user = await _make_staff_user(db, "cf-other@example.com", "看護他人")
    office = await _make_office(db, "事業所CF5")
    patient = await _make_patient(db, "CF-5")
    course = await _make_course(db, office=office, assigned_staff_id=_owner.id, weekday=2, code="A")
    today = datetime.now(JST).date()
    visit = await _make_visit(db, patient=patient, course=course, visit_date=today)

    lst = await client.get("/api/v1/visits", headers=_bearer(other_user))
    assert lst.status_code == 200, lst.text
    assert lst.json() == []

    got = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(other_user))
    assert got.status_code == 404, got.text

    ci = await client.post(
        f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(other_user), json={}
    )
    assert ci.status_code == 404, ci.text


@pytest.mark.asyncio
async def test_course_fallback_does_not_apply_when_primary_is_set(client, db) -> None:
    """主担当が入っている訪問にはフォールバックが効かない (他人の訪問を引き寄せない)."""
    course_staff, course_user = await _make_staff_user(db, "cf-cs@example.com", "看護コース")
    primary, _ = await _make_staff_user(db, "cf-pri@example.com", "看護主")
    office = await _make_office(db, "事業所CF6")
    patient = await _make_patient(db, "CF-6")
    course = await _make_course(
        db, office=office, assigned_staff_id=course_staff.id, weekday=2, code="A"
    )
    visit = Visit(
        patient_id=patient.id,
        primary_staff_id=primary.id,
        course_id=course.id,
        visit_date=VISIT_DAY,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    lst = await client.get("/api/v1/visits", headers=_bearer(course_user))
    assert lst.status_code == 200, lst.text
    assert lst.json() == []
    got = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(course_user))
    assert got.status_code == 404, got.text


@pytest.mark.asyncio
async def test_manual_staff_override_visit_is_not_falled_back(client, db) -> None:
    """``manual_staff_override=True`` はフォールバックしない (送信 CSV と同じ規則).

    「この訪問だけ担当を外した」意思表示なので、コース担当の一覧に戻してはいけない。
    表示名 (``staff_name``) も補わない (admin から見ても未割当のまま)。
    """
    staff, user = await _make_staff_user(db, "cf-ovr@example.com", "看護上書")
    admin = User(
        email="cf-ovr-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    office = await _make_office(db, "事業所CF8")
    patient = await _make_patient(db, "CF-8")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    visit = await _make_visit(db, patient=patient, course=course)
    visit.manual_staff_override = True
    await db.commit()

    # 本人の一覧に出ない / 詳細 404
    lst = await client.get("/api/v1/visits", headers=_bearer(user))
    assert lst.status_code == 200, lst.text
    assert lst.json() == []
    got = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert got.status_code == 404, got.text

    # admin から見ても staff_name は補われない
    as_admin = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert as_admin.status_code == 200, as_admin.text
    assert as_admin.json()["primary_staff_id"] is None
    assert as_admin.json()["staff_name"] is None


@pytest.mark.asyncio
async def test_retired_course_staff_does_not_grant_visibility(client, db) -> None:
    """退職 (``status != 'active'``) / 削除済みのコース担当ではフォールバックしない.

    「辞めた職員を担当にしない」= 送信 CSV (``csv_builder``) と同じ規則。
    """
    staff, user = await _make_staff_user(db, "cf-ret@example.com", "看護退職")
    admin = User(
        email="cf-ret-admin@example.com",
        password_hash=hash_password("does-not-matter"),
        role="admin",
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    office = await _make_office(db, "事業所CF9")
    patient = await _make_patient(db, "CF-9")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    visit = await _make_visit(db, patient=patient, course=course)
    staff.status = "retired"
    await db.commit()

    lst = await client.get("/api/v1/visits", headers=_bearer(user))
    assert lst.status_code == 200, lst.text
    assert lst.json() == []
    got = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert got.status_code == 404, got.text

    as_admin = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert as_admin.status_code == 200, as_admin.text
    assert as_admin.json()["staff_name"] is None


@pytest.mark.asyncio
async def test_resolve_qr_shows_course_staff_as_planned_name(client, db) -> None:
    """QR 解決の ``planned_staff_name`` も主担当 NULL ならコース担当名を出す (LOW-2).

    出さないと現地で「予定 未割当」に見え、代行打刻を選ばせてしまう。
    """
    staff, user = await _make_staff_user(db, "cf-qr@example.com", "看護QR")
    office = await _make_office(db, "事業所CF10")
    patient = await _make_patient(db, "CF-10")
    patient.qr_token = "cf-qr-token-1"
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    today = datetime.now(JST).date()
    visit = await _make_visit(db, patient=patient, course=course, visit_date=today)

    res = await client.get(f"/api/v1/visits/resolve-qr/{patient.qr_token}", headers=_bearer(user))
    assert res.status_code == 200, res.text
    cands = res.json()["candidates"]
    assert [c["visit_id"] for c in cands] == [str(visit.id)]
    assert cands[0]["planned_staff_name"] == "看護QR"
    assert cands[0]["is_mine"] is True


@pytest.mark.asyncio
async def test_deleted_course_does_not_grant_visibility(client, db) -> None:
    """soft-delete 済みコースの担当では見えない (``deleted_at IS NULL`` 条件)."""
    staff, user = await _make_staff_user(db, "cf-del@example.com", "看護削除")
    office = await _make_office(db, "事業所CF7")
    patient = await _make_patient(db, "CF-7")
    course = await _make_course(db, office=office, assigned_staff_id=staff.id, weekday=2, code="A")
    course.deleted_at = datetime.now(JST)
    await db.commit()
    visit = await _make_visit(db, patient=patient, course=course)

    lst = await client.get("/api/v1/visits", headers=_bearer(user))
    assert lst.status_code == 200, lst.text
    assert lst.json() == []
    got = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert got.status_code == 404, got.text
