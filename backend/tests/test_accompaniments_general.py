"""同行割付の一般化 (mig 0072) BE テスト — docs/plans/general-accompaniment-design.md.

基盤の回帰は ``test_accompaniments_legacy_paths.py`` (旧パス) が守る。本ファイルは
**新パス ``/api/v1/accompaniments`` 系**と、一般化で足した挙動だけを扱う:

- 一般スタッフの PUT が通る + ``kind`` のサーバ自動判定 (trainee / support)
- 本人担当との重複 = ハード 422 (``code='accompaniment_overlap'`` / reason='own_duty')
- 同住所免除が本人担当の側でも効く
- NG スタッフ抵触 → 422 確認 → acknowledge 再送で通過 + 管理者通知 (冪等)
- 逆方向警告 (PATCH /courses・apply-staff-review) が**時間帯交差**のときだけ鳴ること
- 複数同行の決定的順序と ``VisitRead.accompaniments[]`` (単数は先頭要素で互換)
- 複数同行時に 2 人目が「代行」と誤判定されないこと
- 一般スタッフの毎週の既定と、その kind が週リンクへ引き継がれること
- 休職 (status 非 active) = 週リンクのみ削除・既定は温存 / 退職 = 既定ごと削除
- course-guard が **kind でゲートされない** こと (保存前フォーム値で使うため)
- inbound_snapshot の新旧キー両書き + 旧キーのみ payload の復元

**時刻非依存**: 週は固定の ISO 週 (2026-W29) を使い、"今週" に依存する EP
(course-guard / DELETE future / status 非 active 化) だけは実行時の JST 現在週から
日付を組む。深夜 (JST 月曜 00:00-09:00 の窓を含む) に走らせても結果が変わらない。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import (
    Accompaniment,
    AccompanimentDefault,
    Course,
    CourseTemplate,
    Notification,
    Office,
    Patient,
    PatientNgStaff,
    Staff,
    User,
    Visit,
)
from app.models.visit_staff_assignment import VisitStaffAssignment

JST = ZoneInfo("Asia/Tokyo")

# 固定週 (時刻非依存)。過去でも未来でも PUT/GET の挙動は変わらない。
ISO_YEAR = 2026
ISO_WEEK = 29


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(
    db,
    name: str,
    *,
    is_trainee: bool = False,
    sex: str | None = None,
    status: str = "active",
) -> Staff:
    s = Staff(name=name, role="staff", status=status, is_trainee=is_trainee, sex=sex)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_office(db, name: str = "事業所") -> Office:
    o = Office(name=name)
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


async def _make_template(db, office: Office, label: str) -> CourseTemplate:
    t = CourseTemplate(office_id=office.id, label=label)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _make_patient(
    db,
    name: str,
    *,
    lat: float | None = None,
    lng: float | None = None,
    sex_restriction: str | None = None,
) -> Patient:
    p = Patient(
        code=f"P-{uuid.uuid4().hex[:8]}",
        name=name,
        lat=lat,
        lng=lng,
        sex_restriction=sex_restriction,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_course(
    db,
    office: Office,
    *,
    weekday: int,
    code: str,
    template_id=None,
    status: str = "course_fixed",
    assigned_staff_id=None,
    iso_year: int = ISO_YEAR,
    iso_week: int = ISO_WEEK,
) -> Course:
    c = Course(
        iso_year=iso_year,
        iso_week=iso_week,
        weekday=weekday,
        code=code,
        office_id=office.id,
        template_id=template_id,
        course_status=status,
        assigned_staff_id=assigned_staff_id,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_visit(
    db,
    patient: Patient,
    *,
    visit_date: date,
    start: time,
    end: time,
    course_id=None,
    primary_staff_id=None,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=primary_staff_id,
        visit_date=visit_date,
        start_time=start,
        end_time=end,
        type="care",
        status="planned",
        source="auto",
        course_id=course_id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _week_date(weekday: int, iso_year: int = ISO_YEAR, iso_week: int = ISO_WEEK) -> date:
    """ISO 週内の指定曜日 (0=Mon) の日付."""
    return date.fromisocalendar(iso_year, iso_week, weekday + 1)


def _current_week() -> tuple[int, int]:
    """JST の現在 ISO 週 (year, week)。"今週以降" を見る EP 用 (時刻非依存化)。"""
    iso = datetime.now(JST).date().isocalendar()
    return iso[0], iso[1]


async def _put(client, admin, staff, **kw):
    """PUT /api/v1/accompaniments (新パス・staff_id キー) の薄いラッパ."""
    body = {
        "staff_id": str(staff.id),
        "iso_year": kw.pop("iso_year", ISO_YEAR),
        "iso_week": kw.pop("iso_week", ISO_WEEK),
    }
    body.update(kw)
    return await client.put("/api/v1/accompaniments", headers=_bearer(admin), json=body)


# ---------------------------------------------------------------------------
# 一般スタッフの PUT + kind 自動判定
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_general_staff_kind_support(client, db) -> None:
    """一般スタッフ (is_trainee=False) は kind='support' で保存される."""
    admin = await _make_user(db, "acc-g1@example.com", "admin")
    staff = await _make_staff(db, "ベテランA", is_trainee=False)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await _put(client, admin, staff, course_ids=[str(course.id)])
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "support"
    assert items[0]["staff_id"] == str(staff.id)
    assert items[0]["staff_name"] == "ベテランA"

    row = await db.scalar(select(Accompaniment).where(Accompaniment.course_id == course.id))
    assert row is not None
    assert row.kind == "support"
    assert row.accompanying_staff_id == staff.id


@pytest.mark.asyncio
async def test_put_trainee_kind_trainee(client, db) -> None:
    """新人 (is_trainee=True) は従来どおり kind='trainee'."""
    admin = await _make_user(db, "acc-g2@example.com", "admin")
    staff = await _make_staff(db, "新人A", is_trainee=True)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await _put(client, admin, staff, course_ids=[str(course.id)])
    assert res.status_code == 200, res.text
    assert res.json()["items"][0]["kind"] == "trainee"


@pytest.mark.asyncio
async def test_put_kind_is_not_accepted_from_client(client, db) -> None:
    """``kind`` は API 入力では受け取らない (詐称防止・extra=forbid で 422)."""
    admin = await _make_user(db, "acc-g3@example.com", "admin")
    staff = await _make_staff(db, "ベテランB", is_trainee=False)
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await client.put(
        "/api/v1/accompaniments",
        headers=_bearer(admin),
        json={
            "staff_id": str(staff.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
            "kind": "trainee",  # 詐称しようとしても弾く。
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_put_non_active_staff_409(client, db) -> None:
    """新人限定は撤廃したが、**在籍していない**スタッフは同行に指名できない."""
    admin = await _make_user(db, "acc-g4@example.com", "admin")
    staff = await _make_staff(db, "退職者", status="retired")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    res = await _put(client, admin, staff, course_ids=[str(course.id)])
    assert res.status_code == 409, res.text


# ---------------------------------------------------------------------------
# 本人担当との重複 = ハード 422 (決定#1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_own_duty_overlap_422_structured(client, db) -> None:
    """自分が担当している訪問と時間が重なる同行は登録できない (reason='own_duty').

    detail は「◯月◯日(◯) HH:MM は ◯◯様（◯◯コース・ご自身の担当）と重なる」を
    FE が組めるだけの粒度を持つ。
    """
    admin = await _make_user(db, "acc-own1@example.com", "admin")
    staff = await _make_staff(db, "ベテランC")
    office = await _make_office(db)
    tmpl = await _make_template(db, office, "稲毛A")
    # 同行させたいコース。
    target = await _make_course(db, office, weekday=0, code="A")
    # 本人が担当しているコース (別コース・同時刻)。
    own = await _make_course(db, office, weekday=0, code="C", template_id=tmpl.id)

    p_target = await _make_patient(db, "佐藤")
    p_own = await _make_patient(db, "山田 太郎")
    await _make_visit(
        db,
        p_target,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(10, 35),
        course_id=target.id,
    )
    await _make_visit(
        db,
        p_own,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(10, 35),
        course_id=own.id,
        primary_staff_id=staff.id,  # ← 本人担当
    )

    res = await _put(client, admin, staff, course_ids=[str(target.id)])
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "accompaniment_overlap"
    own_conflicts = [c for c in detail["conflicts"] if c["reason"] == "own_duty"]
    assert len(own_conflicts) == 1
    c = own_conflicts[0]
    assert c["patient_name"] == "山田 太郎"
    assert c["course_label"] == "稲毛A"  # テンプレ label 優先。
    assert c["date"] == _week_date(0).isoformat()
    assert c["weekday"] == 0
    assert c["start"] == "10:00"
    assert c["end"] == "10:35"

    # 弾かれた = 1 件も保存されない。
    assert await db.scalar(select(func.count()).select_from(Accompaniment)) == 0


@pytest.mark.asyncio
async def test_put_own_duty_overlap_via_vsa_422(client, db) -> None:
    """本人担当は VSA (v2 の正典) 経由でも検出する (primary だけ見ない)."""
    admin = await _make_user(db, "acc-own2@example.com", "admin")
    staff = await _make_staff(db, "ベテランD")
    office = await _make_office(db)
    target = await _make_course(db, office, weekday=1, code="A")
    own = await _make_course(db, office, weekday=1, code="C")

    p_target = await _make_patient(db, "鈴木")
    p_own = await _make_patient(db, "高橋")
    await _make_visit(
        db,
        p_target,
        visit_date=_week_date(1),
        start=time(9, 0),
        end=time(10, 0),
        course_id=target.id,
    )
    own_visit = await _make_visit(
        db, p_own, visit_date=_week_date(1), start=time(9, 30), end=time(10, 30), course_id=own.id
    )
    db.add(VisitStaffAssignment(visit_id=own_visit.id, staff_id=staff.id))
    await db.commit()

    res = await _put(client, admin, staff, course_ids=[str(target.id)])
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert [c["reason"] for c in detail["conflicts"]] == ["own_duty"]
    assert detail["conflicts"][0]["patient_name"] == "高橋"


@pytest.mark.asyncio
async def test_put_own_duty_same_address_exempt_200(client, db) -> None:
    """同住所×同時刻は 90 分占有ルールの正当な同時刻 → 本人担当でもブロックしない."""
    admin = await _make_user(db, "acc-own3@example.com", "admin")
    staff = await _make_staff(db, "ベテランE")
    office = await _make_office(db)
    target = await _make_course(db, office, weekday=2, code="A")
    own = await _make_course(db, office, weekday=2, code="C")

    # 同一座標 (.3f 量子化で同じバケット) の 2 名。
    p_target = await _make_patient(db, "同住所X", lat=35.6001, lng=140.1001)
    p_own = await _make_patient(db, "同住所Y", lat=35.6002, lng=140.1002)
    await _make_visit(
        db,
        p_target,
        visit_date=_week_date(2),
        start=time(10, 0),
        end=time(11, 0),
        course_id=target.id,
    )
    await _make_visit(
        db,
        p_own,
        visit_date=_week_date(2),
        start=time(10, 0),
        end=time(11, 0),
        course_id=own.id,
        primary_staff_id=staff.id,
    )

    res = await _put(client, admin, staff, course_ids=[str(target.id)])
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1


@pytest.mark.asyncio
async def test_put_own_duty_different_time_ok(client, db) -> None:
    """同じ日でも時間が重ならなければ登録できる (同日というだけでは弾かない)."""
    admin = await _make_user(db, "acc-own4@example.com", "admin")
    staff = await _make_staff(db, "ベテランF")
    office = await _make_office(db)
    target = await _make_course(db, office, weekday=3, code="A")
    own = await _make_course(db, office, weekday=3, code="C")

    p_target = await _make_patient(db, "午前")
    p_own = await _make_patient(db, "午後")
    await _make_visit(
        db,
        p_target,
        visit_date=_week_date(3),
        start=time(9, 0),
        end=time(10, 0),
        course_id=target.id,
    )
    await _make_visit(
        db,
        p_own,
        visit_date=_week_date(3),
        start=time(14, 0),
        end=time(15, 0),
        course_id=own.id,
        primary_staff_id=staff.id,
    )

    res = await _put(client, admin, staff, course_ids=[str(target.id)])
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# NG スタッフ / 性別の 422 確認フロー (決定#4)
# ---------------------------------------------------------------------------


async def _ng_setup(db, email: str):
    """NG 抵触する (admin, staff, course) 一式を作る."""
    admin = await _make_user(db, email, "admin")
    staff = await _make_staff(db, "NG対象スタッフ")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "NG患者")
    await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )
    db.add(PatientNgStaff(patient_id=patient.id, staff_id=staff.id, note="相性NG"))
    await db.commit()
    return admin, staff, course, patient


@pytest.mark.asyncio
async def test_put_ng_staff_requires_confirmation_422(client, db) -> None:
    """NG スタッフを同行に指名 → 確認 422 (他経路と完全同形の detail)."""
    admin, staff, course, patient = await _ng_setup(db, "acc-ng1@example.com")

    res = await _put(client, admin, staff, course_ids=[str(course.id)])
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "constraint_confirmation_required"
    assert len(detail["warnings"]) == 1
    w = detail["warnings"][0]
    assert w["kind"] == "ng_staff"
    assert w["patient_id"] == str(patient.id)
    assert w["staff_id"] == str(staff.id)
    assert w["note"] == "相性NG"

    # 確認前は 1 件も保存しない。
    assert await db.scalar(select(func.count()).select_from(Accompaniment)) == 0


@pytest.mark.asyncio
async def test_put_ng_staff_acknowledge_passes_and_notifies(client, db) -> None:
    """acknowledge 再送で通過し、管理者へお知らせが 1 通作られる."""
    admin, staff, course, _patient = await _ng_setup(db, "acc-ng2@example.com")

    res = await _put(
        client,
        admin,
        staff,
        course_ids=[str(course.id)],
        acknowledge_constraint_warnings=True,
    )
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 1

    notes = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "constraint_override")
            )
        ).all()
    )
    assert len(notes) == 1  # active admin は 1 名。
    assert "NGスタッフ" in notes[0].title


@pytest.mark.asyncio
async def test_put_ng_staff_no_notification_without_violation(client, db) -> None:
    """抵触が無ければ acknowledge を付けても通知は作らない (誤通知の防止)."""
    admin = await _make_user(db, "acc-ng3@example.com", "admin")
    staff = await _make_staff(db, "問題なしスタッフ")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "普通の患者")
    await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )

    res = await _put(
        client,
        admin,
        staff,
        course_ids=[str(course.id)],
        acknowledge_constraint_warnings=True,
    )
    assert res.status_code == 200, res.text
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.asyncio
async def test_put_gender_restriction_requires_confirmation(client, db) -> None:
    """性別制限も同じ確認フローに乗る (kind='gender')."""
    admin = await _make_user(db, "acc-ng4@example.com", "admin")
    staff = await _make_staff(db, "男性スタッフ", sex="male")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "女性希望", sex_restriction="female")
    await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )

    res = await _put(client, admin, staff, course_ids=[str(course.id)])
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "constraint_confirmation_required"
    assert [w["kind"] for w in detail["warnings"]] == ["gender"]


@pytest.mark.asyncio
async def test_own_duty_overlap_beats_ng_confirmation(client, db) -> None:
    """ハードブロック (重複) は override 可能な確認 (NG) より**先**に効く.

    順序が逆だと「acknowledge すれば物理的に不可能な同行が通る」ことになる。
    """
    admin, staff, course, _patient = await _ng_setup(db, "acc-ng5@example.com")
    office = await _make_office(db, "拠点2")
    own = await _make_course(db, office, weekday=0, code="C")
    p_own = await _make_patient(db, "本人担当患者")
    await _make_visit(
        db,
        p_own,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=own.id,
        primary_staff_id=staff.id,
    )

    res = await _put(
        client,
        admin,
        staff,
        course_ids=[str(course.id)],
        acknowledge_constraint_warnings=True,  # NG は承認済みでも…
    )
    assert res.status_code == 422, res.text
    # …重複でブロックされる。
    assert res.json()["detail"]["code"] == "accompaniment_overlap"


# ---------------------------------------------------------------------------
# 複数同行 (決定#5) — 決定的順序 + VisitRead.accompaniments[]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_visit_read_multiple_accompaniments_deterministic_order(client, db) -> None:
    """1 訪問に複数同行 → ``accompaniments[]`` 全件 + 単数は先頭要素で互換.

    決定的順序 = support 優先 → スタッフ名昇順。旧 last-wins の非決定性を解消。
    """
    admin = await _make_user(db, "acc-multi1@example.com", "admin")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "複数同行患者")
    visit = await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )

    trainee = await _make_staff(db, "あ新人", is_trainee=True)
    support_b = await _make_staff(db, "い応援")
    support_a = await _make_staff(db, "あ応援")
    for s in (trainee, support_b, support_a):
        db.add(
            Accompaniment(
                accompanying_staff_id=s.id,
                target_type="course",
                course_id=course.id,
                source="manual",
                kind="trainee" if s.is_trainee else "support",
            )
        )
    await db.commit()

    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    names = [a["staff_name"] for a in body["accompaniments"]]
    # support (名前昇順) → trainee。
    assert names == ["あ応援", "い応援", "あ新人"]
    assert [a["kind"] for a in body["accompaniments"]] == ["support", "support", "trainee"]
    # 単数フィールドは先頭要素 (後方互換)。
    assert body["accompaniment"]["staff_name"] == "あ応援"
    assert body["accompaniment"]["staff_id"] == str(support_a.id)


@pytest.mark.asyncio
async def test_visit_read_direct_link_wins_over_course_link(client, db) -> None:
    """同一スタッフが visit 直リンクと course リンク両方でも 1 エントリ."""
    admin = await _make_user(db, "acc-multi2@example.com", "admin")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "重複リンク患者")
    visit = await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )
    staff = await _make_staff(db, "両掛けスタッフ")
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=course.id,
            source="manual",
            kind="support",
        )
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="visit",
            visit_id=visit.id,
            source="manual",
            kind="support",
        )
    )
    await db.commit()

    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert len(res.json()["accompaniments"]) == 1


@pytest.mark.asyncio
async def test_visit_read_no_accompaniment_is_empty(client, db) -> None:
    """同行が無ければ ``accompaniments=[]`` / ``accompaniment=None`` (非破壊)."""
    admin = await _make_user(db, "acc-multi3@example.com", "admin")
    patient = await _make_patient(db, "同行なし患者")
    visit = await _make_visit(
        db, patient, visit_date=_week_date(0), start=time(10, 0), end=time(11, 0)
    )

    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["accompaniments"] == []
    assert res.json()["accompaniment"] is None


# ---------------------------------------------------------------------------
# 逆方向の警告 (決定#1 後段) — コース担当変更
# ---------------------------------------------------------------------------


async def _reverse_warning_fixture(
    db,
    email: str,
    *,
    acc_start: time,
    acc_end: time,
    duty_start: time,
    duty_end: time,
    acc_weekday: int = 0,
    duty_weekday: int = 0,
):
    """同行あり / 担当を付けようとしているコースあり、の 2 本立てを組む."""
    admin = await _make_user(db, email, "admin")
    staff = await _make_staff(db, "掛け持ちスタッフ")
    office = await _make_office(db)

    acc_course = await _make_course(db, office, weekday=acc_weekday, code="C")
    p_acc = await _make_patient(db, "同行先患者")
    await _make_visit(
        db,
        p_acc,
        visit_date=_week_date(acc_weekday),
        start=acc_start,
        end=acc_end,
        course_id=acc_course.id,
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=acc_course.id,
            source="manual",
            kind="support",
        )
    )
    # 担当を付けようとしているコース (訪問つき = 交差判定の左辺)。
    target = await _make_course(db, office, weekday=duty_weekday, code="A")
    p_duty = await _make_patient(db, "担当先患者")
    await _make_visit(
        db,
        p_duty,
        visit_date=_week_date(duty_weekday),
        start=duty_start,
        end=duty_end,
        course_id=target.id,
    )
    await db.commit()
    return admin, staff, target


@pytest.mark.asyncio
async def test_course_patch_warns_when_accompaniment_overlaps_duty(client, db) -> None:
    """担当する訪問と同行が**時間帯で交差**したら警告 + 管理者通知 (非ブロック)."""
    admin, staff, target = await _reverse_warning_fixture(
        db,
        "acc-rev1@example.com",
        acc_start=time(10, 0),
        acc_end=time(11, 0),
        duty_start=time(10, 30),
        duty_end=time(11, 30),
    )

    res = await client.patch(
        f"/api/v1/courses/{target.id}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(staff.id)},
    )
    # ブロックしない。
    assert res.status_code == 200, res.text
    warns = res.json()["accompaniment_warnings"]
    assert len(warns) == 1
    assert warns[0]["staff_id"] == str(staff.id)
    assert warns[0]["patient_name"] == "同行先患者"
    assert warns[0]["date"] == _week_date(0).isoformat()

    notes = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "accompaniment_conflict")
            )
        ).all()
    )
    assert len(notes) == 1
    assert "掛け持ちスタッフ" in notes[0].title


@pytest.mark.asyncio
async def test_course_patch_no_warning_on_other_day(client, db) -> None:
    """同行が別の曜日なら警告は出ない (誤警告でトーストを鳴らさない)."""
    admin, staff, target = await _reverse_warning_fixture(
        db,
        "acc-rev2@example.com",
        acc_weekday=1,  # 同行は火曜。
        acc_start=time(10, 0),
        acc_end=time(11, 0),
        duty_weekday=0,  # 担当は月曜。
        duty_start=time(10, 0),
        duty_end=time(11, 0),
    )

    res = await client.patch(
        f"/api/v1/courses/{target.id}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(staff.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["accompaniment_warnings"] == []
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


@pytest.mark.asyncio
async def test_course_patch_no_warning_when_times_do_not_overlap(client, db) -> None:
    """**同日でも時間帯が交差しなければ鳴らさない** (レビュー M-4 のノイズ低減).

    午前の同行と午後の担当は実務上まったく問題ない。ここで鳴らすと警告が
    オオカミ少年になり、本当に危ない交差が読み飛ばされる。
    """
    admin, staff, target = await _reverse_warning_fixture(
        db,
        "acc-rev3@example.com",
        acc_start=time(9, 0),
        acc_end=time(10, 0),
        duty_start=time(14, 0),  # 同じ月曜だが午後。
        duty_end=time(15, 0),
    )

    res = await client.patch(
        f"/api/v1/courses/{target.id}",
        headers=_bearer(admin),
        json={"assigned_staff_id": str(staff.id)},
    )
    assert res.status_code == 200, res.text
    assert res.json()["accompaniment_warnings"] == []
    assert await db.scalar(select(func.count()).select_from(Notification)) == 0


# ---------------------------------------------------------------------------
# 一般スタッフの毎週の既定 / course-guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defaults_general_staff_kind_support(client, db) -> None:
    """一般スタッフの既定は kind='support' で保存され、新パスで読める."""
    admin = await _make_user(db, "acc-def1@example.com", "admin")
    staff = await _make_staff(db, "既定ベテラン")
    office = await _make_office(db)
    t = await _make_template(db, office, "B")

    res = await client.put(
        "/api/v1/accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "staff_id": str(staff.id),
            "items": [{"weekday": 2, "course_template_id": str(t.id)}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()[0]["kind"] == "support"

    res = await client.get(
        f"/api/v1/accompaniment-defaults?staff_id={staff.id}", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["weekday"] == 2
    assert body[0]["course_template_label"] == "B"
    assert body[0]["staff_id"] == str(staff.id)


@pytest.mark.asyncio
async def test_course_guard_counts_for_general_staff(client, db) -> None:
    """**保存済み kind でゲートしない** (レビュー HIGH で撤廃した機能回帰の防止).

    このEPは「これから is_trainee を ON にする人」への事前警告で、FE は保存前の
    フォーム値で発火する。呼ばれる時点の DB 上は一般スタッフなので、kind で
    ゲートすると警告が恒久的に出なくなる。一般スタッフでも件数を返すこと。
    """
    admin = await _make_user(db, "acc-cg1@example.com", "admin")
    general = await _make_staff(db, "一般ガード", is_trainee=False)
    office = await _make_office(db)
    cur_year, cur_week = _current_week()
    await _make_course(
        db,
        office,
        weekday=0,
        code="A",
        assigned_staff_id=general.id,
        iso_year=cur_year,
        iso_week=cur_week,
    )

    res = await client.get(
        f"/api/v1/accompaniments/course-guard?staff_id={general.id}", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applicable"] is True  # 常に true (互換のため残しているだけ)。
    assert body["count"] == 1
    assert body["courses"][0]["code"] == "A"


@pytest.mark.asyncio
async def test_course_guard_applicable_for_trainee(client, db) -> None:
    """新人はガードが効き、今週以降の担当コースを返す."""
    admin = await _make_user(db, "acc-cg2@example.com", "admin")
    trainee = await _make_staff(db, "新人ガード", is_trainee=True)
    office = await _make_office(db)
    cur_year, cur_week = _current_week()
    await _make_course(
        db,
        office,
        weekday=0,
        code="A",
        assigned_staff_id=trainee.id,
        iso_year=cur_year,
        iso_week=cur_week,
    )

    res = await client.get(
        f"/api/v1/accompaniments/course-guard?staff_id={trainee.id}", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["applicable"] is True
    assert body["count"] == 1


# ---------------------------------------------------------------------------
# ライフサイクル — status 非 active 化で将来リンク + 既定を削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_deactivation_purges_links_but_keeps_defaults(client, db) -> None:
    """休職 (status 非 active 化) は**週リンクだけ**消し、毎週の既定は温存する.

    レビュー M-2: 休職は復帰前提。既定まで消すと復帰時に人手で組み直しになる。
    週リンクを消せば盤面からは即消え、復帰後の週生成で既定が再展開される。

    「今週」は実行時の JST 現在週から組むため、深夜に走らせても結果は変わらない。
    """
    admin = await _make_user(db, "acc-life1@example.com", "admin")
    staff = await _make_staff(db, "退職予定スタッフ")
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    cur_year, cur_week = _current_week()
    future_course = await _make_course(
        db, office, weekday=0, code="A", iso_year=cur_year, iso_week=cur_week
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=future_course.id,
            source="manual",
            kind="support",
        )
    )
    db.add(
        AccompanimentDefault(
            accompanying_staff_id=staff.id,
            weekday=0,
            course_template_id=t.id,
            kind="support",
        )
    )
    await db.commit()

    res = await client.patch(
        f"/api/v1/staff/{staff.id}", headers=_bearer(admin), json={"status": "retired"}
    )
    assert res.status_code == 200, res.text
    # 非破壊追加した削除件数が返る (FE 表示配線は Phase E)。
    assert res.json()["purged_accompaniment_links"] == 1
    assert res.json()["purged_accompaniment_defaults"] == 0

    links = await db.scalar(
        select(func.count())
        .select_from(Accompaniment)
        .where(Accompaniment.accompanying_staff_id == staff.id)
    )
    defaults = await db.scalar(
        select(func.count())
        .select_from(AccompanimentDefault)
        .where(AccompanimentDefault.accompanying_staff_id == staff.id)
    )
    assert links == 0  # 週リンクは消える。
    assert defaults == 1  # **既定は温存** (復帰時に自動復活する)。


@pytest.mark.asyncio
async def test_staff_update_keeps_links_when_still_active(client, db) -> None:
    """active のままの更新 (氏名変更など) では同行リンクを消さない."""
    admin = await _make_user(db, "acc-life2@example.com", "admin")
    staff = await _make_staff(db, "現役スタッフ")
    office = await _make_office(db)
    cur_year, cur_week = _current_week()
    course = await _make_course(
        db, office, weekday=0, code="A", iso_year=cur_year, iso_week=cur_week
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=course.id,
            source="manual",
            kind="support",
        )
    )
    await db.commit()

    res = await client.patch(
        f"/api/v1/staff/{staff.id}", headers=_bearer(admin), json={"name": "現役スタッフ(改名)"}
    )
    assert res.status_code == 200, res.text

    links = await db.scalar(
        select(func.count())
        .select_from(Accompaniment)
        .where(Accompaniment.accompanying_staff_id == staff.id)
    )
    assert links == 1


@pytest.mark.asyncio
async def test_staff_soft_delete_purges_links_and_defaults(client, db) -> None:
    """論理削除 (退職) は将来リンク**と既定**を消す — 休職 (PATCH) との対比.

    FK CASCADE は soft delete で発火しないのでアプリ層で消す必要がある。
    退職は復帰前提ではないため既定ごと畳む (レビュー M-2 の使い分け)。
    """
    admin = await _make_user(db, "acc-life3@example.com", "admin")
    staff = await _make_staff(db, "削除スタッフ")
    office = await _make_office(db)
    t = await _make_template(db, office, "A")
    cur_year, cur_week = _current_week()
    course = await _make_course(
        db, office, weekday=0, code="A", iso_year=cur_year, iso_week=cur_week
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=course.id,
            source="manual",
            kind="support",
        )
    )
    db.add(
        AccompanimentDefault(
            accompanying_staff_id=staff.id,
            weekday=0,
            course_template_id=t.id,
            kind="support",
        )
    )
    await db.commit()

    res = await client.delete(f"/api/v1/staff/{staff.id}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    links = await db.scalar(
        select(func.count())
        .select_from(Accompaniment)
        .where(Accompaniment.accompanying_staff_id == staff.id)
    )
    defaults = await db.scalar(
        select(func.count())
        .select_from(AccompanimentDefault)
        .where(AccompanimentDefault.accompanying_staff_id == staff.id)
    )
    assert links == 0
    assert defaults == 0  # 退職は既定まで畳む。


# ---------------------------------------------------------------------------
# 旧パス互換 (新旧が同一ハンドラであること)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_and_new_paths_share_handler(client, db) -> None:
    """旧パスで書いたリンクが新パスの GET で読める (= 同じ実装・同じテーブル)."""
    admin = await _make_user(db, "acc-alias1@example.com", "admin")
    staff = await _make_staff(db, "エイリアス確認")
    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")

    # 旧パス + 旧キーで書く。
    res = await client.put(
        "/api/v1/trainee-accompaniments",
        headers=_bearer(admin),
        json={
            "trainee_staff_id": str(staff.id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "course_ids": [str(course.id)],
        },
    )
    assert res.status_code == 200, res.text

    # 新パス + 新キーで読む。
    res = await client.get(
        f"/api/v1/accompaniments?iso_year={ISO_YEAR}&iso_week={ISO_WEEK}&staff_id={staff.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "support"
    assert items[0]["staff_id"] == str(staff.id)
    assert items[0]["trainee_staff_id"] == str(staff.id)  # 旧キー併記。


@pytest.mark.asyncio
async def test_put_requires_staff_id(client, db) -> None:
    """新旧どちらのキーも無ければ 422 (どちらか一方は必須)."""
    admin = await _make_user(db, "acc-alias2@example.com", "admin")
    res = await client.put(
        "/api/v1/accompaniments",
        headers=_bearer(admin),
        json={"iso_year": ISO_YEAR, "iso_week": ISO_WEEK, "course_ids": []},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# apply-staff-review の逆方向警告 (レビュー M-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_staff_review_reports_accompaniment_warnings(client, db) -> None:
    """apply-staff-review も逆方向警告を返し、管理者へ 1 通に集約して通知する.

    スタッフごとに通知すると、同じ op_group_id の 2 通目以降が冪等判定で落ちて
    別スタッフの警告が消える — 集約が正しい形 (レビュー M-4)。
    """
    admin = await _make_user(db, "acc-apply1@example.com", "admin")
    staff = await _make_staff(db, "承認対象スタッフ")
    office = await _make_office(db)

    # 同行しているコース (月 10:00-11:00)。
    acc_course = await _make_course(db, office, weekday=0, code="C")
    p_acc = await _make_patient(db, "同行先患者")
    await _make_visit(
        db,
        p_acc,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=acc_course.id,
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=acc_course.id,
            source="manual",
            kind="support",
        )
    )
    # 承認して担当を付けるコース (月 10:30-11:30 = 交差)。
    target = await _make_course(db, office, weekday=0, code="A")
    p_duty = await _make_patient(db, "担当先患者")
    await _make_visit(
        db,
        p_duty,
        visit_date=_week_date(0),
        start=time(10, 30),
        end=time(11, 30),
        course_id=target.id,
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "items": [{"course_id": str(target.id), "staff_id": str(staff.id)}],
        },
    )
    assert res.status_code == 200, res.text
    warns = res.json()["accompaniment_warnings"]
    assert len(warns) == 1
    assert warns[0]["staff_id"] == str(staff.id)
    assert warns[0]["patient_name"] == "同行先患者"

    notes = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "accompaniment_conflict")
            )
        ).all()
    )
    assert len(notes) == 1
    assert "承認対象スタッフ" in notes[0].title


@pytest.mark.asyncio
async def test_apply_staff_review_no_warning_without_overlap(client, db) -> None:
    """交差しなければ apply-staff-review も鳴らさない."""
    admin = await _make_user(db, "acc-apply2@example.com", "admin")
    staff = await _make_staff(db, "無干渉スタッフ")
    office = await _make_office(db)

    acc_course = await _make_course(db, office, weekday=0, code="C")
    p_acc = await _make_patient(db, "朝の同行先")
    await _make_visit(
        db,
        p_acc,
        visit_date=_week_date(0),
        start=time(9, 0),
        end=time(10, 0),
        course_id=acc_course.id,
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="course",
            course_id=acc_course.id,
            source="manual",
            kind="support",
        )
    )
    target = await _make_course(db, office, weekday=0, code="A")
    p_duty = await _make_patient(db, "午後の担当先")
    await _make_visit(
        db,
        p_duty,
        visit_date=_week_date(0),
        start=time(15, 0),
        end=time(16, 0),
        course_id=target.id,
    )
    await db.commit()

    res = await client.post(
        "/api/v1/schedule/apply-staff-review",
        headers=_bearer(admin),
        json={
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "items": [{"course_id": str(target.id), "staff_id": str(staff.id)}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["accompaniment_warnings"] == []
    assert (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.reference_type == "accompaniment_conflict")
        )
        == 0
    )


# ---------------------------------------------------------------------------
# 複数同行 × 代行判定 (レビュー M-6) — visit_staff_id_set の集合化 回帰
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_accompanying_staff_is_not_flagged_as_substitute(db) -> None:
    """2 人目の同行者の打刻を「代行」と誤判定しないこと.

    担当集合を代表 1 名で組むと、決定的順序で 2 番目に来た同行者が集合から漏れ、
    正規に同行した人の打刻が代行扱いになって管理者へ誤通知が飛ぶ。
    ``visit_staff_id_set`` が同行を**全件**受ける回帰テスト。
    """
    from app.services.accompaniment import resolve_accompaniment_by_visit
    from app.services.checkin.monitor import visit_staff_id_set

    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "複数同行患者")
    primary = await _make_staff(db, "主担当")
    # 決定的順序では「あ応援」が先頭・「い応援」が 2 番目。
    first = await _make_staff(db, "あ応援")
    second = await _make_staff(db, "い応援")
    visit = await _make_visit(
        db,
        patient,
        visit_date=_week_date(0),
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
        primary_staff_id=primary.id,
    )
    for s in (first, second):
        db.add(
            Accompaniment(
                accompanying_staff_id=s.id,
                target_type="course",
                course_id=course.id,
                source="manual",
                kind="support",
            )
        )
    await db.commit()

    entries = (await resolve_accompaniment_by_visit(db, [visit]))[visit.id]
    assert [e.staff_name for e in entries] == ["あ応援", "い応援"]

    assigned = visit_staff_id_set(visit, accompaniment_staff_ids=[e.staff_id for e in entries])
    # 2 人目も担当集合に含まれる = 代行と判定されない。
    assert first.id in assigned
    assert second.id in assigned
    assert primary.id in assigned


# ---------------------------------------------------------------------------
# inbound_snapshot の新旧キー (レビュー M-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_writes_both_keys_and_restores_legacy_only(db) -> None:
    """スナップショットは新旧キーを両方書き、**旧キーのみ**の payload も復元できる.

    mig 0072 以前に取ったスナップショットが本番に残っているため、復元側は
    ``trainee_staff_id`` しか無い payload を読めなければならない。
    """
    from datetime import datetime as _dt

    from app.services.kaipoke.inbound_snapshot import restore_snapshot, snapshot_week

    office = await _make_office(db)
    course = await _make_course(db, office, weekday=0, code="A")
    patient = await _make_patient(db, "スナップ患者")
    staff = await _make_staff(db, "スナップ同行者")
    week_start = _week_date(0)
    visit = await _make_visit(
        db,
        patient,
        visit_date=week_start,
        start=time(10, 0),
        end=time(11, 0),
        course_id=course.id,
    )
    db.add(
        Accompaniment(
            accompanying_staff_id=staff.id,
            target_type="visit",
            visit_id=visit.id,
            source="manual",
            kind="support",
        )
    )
    await db.commit()

    snap = await snapshot_week(db, week_start, kind="test", user_id=None)
    await db.commit()

    # --- 新旧キーが両方書かれている ---
    acc_payload = snap.payload["accompaniments"]
    assert len(acc_payload) == 1
    assert acc_payload[0]["accompanying_staff_id"] == str(staff.id)
    assert acc_payload[0]["trainee_staff_id"] == str(staff.id)
    assert acc_payload[0]["kind"] == "support"

    # --- 旧キーだけの payload (mig 0072 以前のスナップショット) でも復元できる ---
    legacy_entry = {
        "visit_index": acc_payload[0]["visit_index"],
        "trainee_staff_id": str(staff.id),
        "source": "manual",
    }
    snap.payload = {**snap.payload, "accompaniments": [legacy_entry]}
    await db.commit()

    await restore_snapshot(db, snap, now=_dt.now(JST).replace(tzinfo=None))
    await db.commit()

    # 復元は週を白紙化して visit を作り直すため、**生きている visit** に紐づく
    # リンクだけを見る (旧 visit は soft-delete で残り、FK CASCADE は発火しない。
    # 孤立リンクは後続の週生成で _cleanup_orphan_links が掃除する既存仕様)。
    restored = list(
        (
            await db.scalars(
                select(Accompaniment)
                .join(Visit, Accompaniment.visit_id == Visit.id)
                .where(
                    Accompaniment.accompanying_staff_id == staff.id,
                    Visit.deleted_at.is_(None),
                )
            )
        ).all()
    )
    assert len(restored) == 1
    # kind 欠落の旧 payload は既定 'trainee' で復元される。
    assert restored[0].kind == "trainee"


# ---------------------------------------------------------------------------
# 既定の kind が週リンクへ引き継がれること (レビュー M-6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_kind_support_propagates_to_week_link(client, db) -> None:
    """一般スタッフの既定 (kind='support') を展開した週リンクも 'support' になる.

    展開は既定行の kind をそのまま引き継ぐ (再判定しない) 契約の担保。
    """
    from app.services.accompaniment import expand_accompaniment_defaults

    admin = await _make_user(db, "acc-defkind@example.com", "admin")
    staff = await _make_staff(db, "既定サポート", is_trainee=False)
    office = await _make_office(db)
    tmpl = await _make_template(db, office, "A")

    res = await client.put(
        "/api/v1/accompaniment-defaults",
        headers=_bearer(admin),
        json={
            "staff_id": str(staff.id),
            "items": [{"weekday": 0, "course_template_id": str(tmpl.id)}],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()[0]["kind"] == "support"

    # 既定が指すテンプレの週コースを用意して展開する。
    await _make_course(db, office, weekday=0, code="A", template_id=tmpl.id)
    created = await expand_accompaniment_defaults(db, ISO_YEAR, ISO_WEEK)
    await db.commit()
    assert created == 1

    link = await db.scalar(
        select(Accompaniment).where(Accompaniment.accompanying_staff_id == staff.id)
    )
    assert link is not None
    assert link.kind == "support"  # 既定の kind を引き継ぐ。
    assert link.source == "default"
