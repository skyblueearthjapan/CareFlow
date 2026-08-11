"""残りの **apply 経路** の NG スタッフ / 性別制限 防御 tests (§7-2 面展開 第2弾).

正典設計書: ``docs/plans/patient-ng-staff-design.md`` §7。

背景: 提案系エンジンは NG 枠を生成しなくなった (ba7c1da) が、**提案を作った後に
NG が登録される**と古い提案の apply が素通りする (plan 指紋 / state_token は NG 行を
含まないため検出できない)。よって apply 側にも防御を置く。

検査観点:
  SW1  apply-swap: A 側 NG (a_new のコース = 明示指定) → 422 → acknowledge で 200 + 通知
  SW2  apply-swap: B 側 NG (b_new は FE が course_template_id を省略する = 相手方向)
  SW3  apply-swap: 違反なし → 素通り (通知なし)
  SC1  scope-optimization apply: simulate 後に NG 登録 → 422 (PFV 無傷) → ack で 200 + 通知
  UB1  propose-unblock apply: 探索後に **対象患者** へ NG 登録 → 422。
       退避 (moves) が 1 手も適用されていない = **部分適用が起きない**ことを検証。
       ack で 200 + 通知。
  PB1  pool-bulk-apply: NG placement → 422 (PFV 未作成) → ack で 200 + 通知
  V1   POST /visits/{id}/staff: NG スタッフ追加 → 422 (単純 422・確認フロー無し)
  V2   PATCH /visits/{id}: primary_staff_id 差し替えが NG → 422 / course_id 差し替えが NG → 422
  V3   POST /visits: 作成時の primary_staff_id が NG → 422

Backend で APP_ENV=test ガード済み (conftest.py). 本番 DB 禁止 (ローカル SQLite).
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.course import COURSE_STATUS_STAFF_ASSIGNED, Course
from app.models.course_template import CourseTemplate
from app.models.notification import Notification
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.patient_ng_staff import PatientNgStaff
from app.models.staff import Staff, StaffShift
from app.models.user import User
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.services.scheduling.pool_bulk_inserter import compute_bulk_state_token

# ISO 2026 W22 = 2026-05-25 (Mon) .. (他テストファイルと週をずらす必要はないが独立性のため)
ISO_YEAR = 2026
ISO_WEEK = 22
MON = date.fromisocalendar(ISO_YEAR, ISO_WEEK, 1)

BASE = (35.6000, 140.1000)
NEAR = (35.6010, 140.1010)
FAR = (35.6300, 140.1400)
V_COORD = (35.6040, 140.1000)
FILL = (35.6000, 140.1200)

_SWAP_URL = "/api/v1/schedule/v2/improvement-suggestions/apply-swap"
_SCOPE_SIM_URL = "/api/v1/schedule/v2/scope-optimization/simulate"
_SCOPE_APPLY_URL = "/api/v1/schedule/v2/scope-optimization/apply"
_UNBLOCK_URL = "/api/v1/schedule/v2/propose-unblock"
_UNBLOCK_APPLY_URL = "/api/v1/schedule/v2/propose-unblock/apply"
_POOL_APPLY_URL = "/api/v1/schedule/v2/pool-bulk-apply"
_VISITS_URL = "/api/v1/visits"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _leave_session_clean(db):
    """各テストの終わりに test session の TX を閉じる (aiosqlite 共有コネクション対策).

    conftest の in-memory SQLite は 1 コネクションを app session と共有するため、
    テストが **開いた読み取り TX を残したまま**終わると、次のテスト (別ファイルも含む)
    の commit が ``cannot commit transaction - SQL statements in progress`` で落ちる。
    本ファイルは apply 後の検証で毎回 SELECT して終わるので、明示的に閉じておく
    (``tests/test_visits.py::test_visits_delete_manager_returns_204`` の防御コメント
    と同じ既知事象)。
    """
    yield
    await db.rollback()


async def _make_admin(db, email: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _mk_office(db, *, name: str, code: str) -> Office:
    office = Office(name=name, code=code)
    db.add(office)
    await db.flush()
    return office


async def _mk_staff(db, *, office: Office, name: str, shifts: bool = True) -> Staff:
    staff = Staff(name=name, role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    if shifts:
        for wd in range(5):
            db.add(StaffShift(staff_id=staff.id, weekday=wd, is_on=True))
    return staff


async def _mk_template(db, *, office: Office, label: str) -> CourseTemplate:
    ct = CourseTemplate(office_id=office.id, label=label)
    db.add(ct)
    await db.flush()
    return ct


async def _mk_course(
    db,
    *,
    office: Office,
    staff: Staff | None,
    weekday: int,
    code: str,
    template: CourseTemplate | None = None,
) -> Course:
    course = Course(
        iso_year=ISO_YEAR,
        iso_week=ISO_WEEK,
        weekday=weekday,
        code=code,
        course_status=COURSE_STATUS_STAFF_ASSIGNED,
        assigned_staff_id=staff.id if staff is not None else None,
        office_id=office.id,
        template_id=template.id if template is not None else None,
    )
    db.add(course)
    await db.flush()
    return course


def _mk_patient(
    *, code: str, office: Office, coords: tuple[float, float] = BASE, name: str | None = None
) -> Patient:
    return Patient(
        code=code,
        name=name or f"P-{code}",
        status="active",
        special_week_active=[],
        lat=coords[0],
        lng=coords[1],
        primary_office_id=office.id,
    )


async def _mk_pfv(
    db,
    *,
    patient: Patient,
    weekday: int,
    start: time,
    duration: int = 30,
    template: CourseTemplate | None = None,
    movability: str = "day_flexible",
) -> PatientFixedVisit:
    row = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        slot_index=0,
        start_time=start,
        duration_min=duration,
        movability=movability,
        course_template_id=template.id if template is not None else None,
    )
    db.add(row)
    await db.flush()
    return row


async def _mk_visit(
    db, *, patient: Patient, course: Course, weekday: int, start: time, duration: int = 30
) -> Visit:
    end_min = start.hour * 60 + start.minute + duration
    v = Visit(
        patient_id=patient.id,
        visit_date=MON + timedelta(days=weekday),
        start_time=start,
        end_time=time(end_min // 60, end_min % 60),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto",
        required_staff_count=1,
        course_id=course.id,
        primary_staff_id=course.assigned_staff_id,
    )
    db.add(v)
    await db.flush()
    return v


async def _refresh(db) -> None:
    await db.rollback()
    db.expire_all()


async def _notifications(db) -> list[Notification]:
    await _refresh(db)
    return list(
        (
            await db.scalars(select(Notification).where(Notification.type == "constraint_override"))
        ).all()
    )


async def _pfvs(db, patient_id) -> list[PatientFixedVisit]:
    await _refresh(db)
    return list(
        (
            await db.scalars(
                select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient_id)
            )
        ).all()
    )


def _assert_confirmation(detail: dict, *, patient_id, staff_id, kind: str = "ng_staff") -> None:
    """422 detail が全経路共通の形 (§7-2) で、期待する患者 × スタッフを含むこと."""
    assert detail["code"] == "constraint_confirmation_required"
    assert detail["warnings"]
    for w in detail["warnings"]:
        assert set(w) == {"kind", "patient_id", "patient_name", "staff_id", "staff_name", "note"}
    assert any(
        w["kind"] == kind and w["patient_id"] == str(patient_id) and w["staff_id"] == str(staff_id)
        for w in detail["warnings"]
    ), detail


async def _register_ng(client, headers, *, patient_id, staff_id, note: str) -> None:
    """NG スタッフを **API 経由**で登録する (simulate と apply の間に差し込む用).

    テスト session から直接 INSERT すると、client リクエストと同一の in-memory
    SQLite コネクションを共有している都合で commit が落ちることがある
    (= 不安定なテスト)。実運用と同じ経路 (PUT /patients/{id}/ng-staff/{staff_id})
    で書けば app 側 session が commit するため確実。
    """
    res = await client.put(
        f"/api/v1/patients/{patient_id}/ng-staff/{staff_id}",
        headers=headers,
        json={"note": note},
    )
    assert res.status_code in (200, 201), res.text


# ---------------------------------------------------------------------------
# SW1 / SW2 / SW3: apply-swap (両方向)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sw1_apply_swap_a_side_ng_requires_confirmation(client, db) -> None:
    """A が移る先 (a_new のコース = 明示指定) の担当が A の NG → 422 → ack で 200 + 通知."""
    admin = await _make_admin(db, "cc2-sw1@example.com")
    headers = _bearer(admin)
    office = await _mk_office(db, name="稲", code="SW1")
    ng_staff = await _mk_staff(db, office=office, name="NG担当")
    tpl_a = await _mk_template(db, office=office, label="A")
    await _mk_course(db, office=office, staff=ng_staff, weekday=2, code="A", template=tpl_a)

    pa = _mk_patient(code="SW1A", office=office, coords=BASE)
    pb = _mk_patient(code="SW1B", office=office, coords=FAR)
    db.add_all([pa, pb])
    await db.flush()
    await _mk_pfv(db, patient=pa, weekday=0, start=time(10, 30))
    await _mk_pfv(db, patient=pb, weekday=2, start=time(9, 0))
    db.add(PatientNgStaff(patient_id=pa.id, staff_id=ng_staff.id, note="入替NG"))
    # commit で ORM 属性は expire される (以後のアクセスは同期 lazy load = MissingGreenlet)。
    # 必要な id は commit 前に控える。
    pa_id, pb_id, ng_id, tpl_a_id = pa.id, pb.id, ng_staff.id, tpl_a.id
    await db.commit()

    body = {
        "patient_a_id": str(pa_id),
        "patient_b_id": str(pb_id),
        # A は B の旧枠 (水) へ = 移動先コースは FE が解決できるので明示で来る。
        "a_new": {"weekday": 2, "start_time": "09:00", "course_template_id": str(tpl_a_id)},
        # B は A の旧枠 (月) へ = FE は counterpart のコースを解決できず省略する。
        "b_new": {"weekday": 0, "start_time": "10:30"},
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
    }
    res = await client.post(_SWAP_URL, headers=headers, json=body)
    assert res.status_code == 422, res.text
    _assert_confirmation(res.json()["detail"], patient_id=pa_id, staff_id=ng_id)

    # 書き込み前に弾いている = PFV は元のまま。
    rows_a = await _pfvs(db, pa_id)
    assert len(rows_a) == 1 and rows_a[0].weekday == 0 and rows_a[0].start_time == time(10, 30)
    assert await _notifications(db) == []

    res = await client.post(
        _SWAP_URL,
        headers=headers,
        json={**body, "acknowledge_constraint_warnings": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied"] is True

    rows_a = await _pfvs(db, pa_id)
    assert rows_a[0].weekday == 2 and rows_a[0].start_time == time(9, 0)
    notes = await _notifications(db)
    assert len(notes) == 1
    assert "NGスタッフ割当を承認" in notes[0].title
    assert "メモ: 入替NG" in (notes[0].body or "")


@pytest.mark.asyncio
async def test_sw2_apply_swap_b_side_ng_requires_confirmation(client, db) -> None:
    """相手方向: B の移動先 (省略時 = B 自身のコース × A の旧曜日) の担当が B の NG → 422.

    ``_apply_pfv_move`` の「省略 = 既存コースを保持」(後方互換オプション b) と
    同じソースで移動先を解決していること = 片側だけの検査では見逃す穴を塞げていること。
    """
    admin = await _make_admin(db, "cc2-sw2@example.com")
    headers = _bearer(admin)
    office = await _mk_office(db, name="稲", code="SW2")
    ng_staff = await _mk_staff(db, office=office, name="B側NG担当")
    tpl_b = await _mk_template(db, office=office, label="B")
    # B は自分のコース (B) のまま A の旧曜日 (月) へ移る → 月曜 B コースの担当が相手。
    await _mk_course(db, office=office, staff=ng_staff, weekday=0, code="B", template=tpl_b)

    pa = _mk_patient(code="SW2A", office=office, coords=BASE)
    pb = _mk_patient(code="SW2B", office=office, coords=FAR)
    db.add_all([pa, pb])
    await db.flush()
    await _mk_pfv(db, patient=pa, weekday=0, start=time(10, 30))
    await _mk_pfv(db, patient=pb, weekday=2, start=time(9, 0), template=tpl_b)
    db.add(PatientNgStaff(patient_id=pb.id, staff_id=ng_staff.id, note=None))
    pa_id, pb_id, ng_id = pa.id, pb.id, ng_staff.id
    await db.commit()

    body = {
        "patient_a_id": str(pa_id),
        "patient_b_id": str(pb_id),
        "a_new": {"weekday": 2, "start_time": "09:00"},
        "b_new": {"weekday": 0, "start_time": "10:30"},
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
    }
    res = await client.post(_SWAP_URL, headers=headers, json=body)
    assert res.status_code == 422, res.text
    _assert_confirmation(res.json()["detail"], patient_id=pb_id, staff_id=ng_id)

    rows_b = await _pfvs(db, pb_id)
    assert rows_b[0].weekday == 2

    res = await client.post(
        _SWAP_URL,
        headers=headers,
        json={**body, "acknowledge_constraint_warnings": True},
    )
    assert res.status_code == 200, res.text
    rows_b = await _pfvs(db, pb_id)
    assert rows_b[0].weekday == 0
    assert len(await _notifications(db)) == 1


@pytest.mark.asyncio
async def test_sw3_apply_swap_clean_passes(client, db) -> None:
    """違反なし = 従来と同一挙動 (後方互換・通知なし)."""
    admin = await _make_admin(db, "cc2-sw3@example.com")
    headers = _bearer(admin)
    office = await _mk_office(db, name="稲", code="SW3")
    staff = await _mk_staff(db, office=office, name="通常担当")
    tpl_a = await _mk_template(db, office=office, label="A")
    await _mk_course(db, office=office, staff=staff, weekday=2, code="A", template=tpl_a)

    pa = _mk_patient(code="SW3A", office=office, coords=BASE)
    pb = _mk_patient(code="SW3B", office=office, coords=FAR)
    db.add_all([pa, pb])
    await db.flush()
    await _mk_pfv(db, patient=pa, weekday=0, start=time(10, 30))
    await _mk_pfv(db, patient=pb, weekday=2, start=time(9, 0))
    pa_id, pb_id, tpl_a_id = pa.id, pb.id, tpl_a.id
    await db.commit()

    res = await client.post(
        _SWAP_URL,
        headers=headers,
        json={
            "patient_a_id": str(pa_id),
            "patient_b_id": str(pb_id),
            "a_new": {"weekday": 2, "start_time": "09:00", "course_template_id": str(tpl_a_id)},
            "b_new": {"weekday": 0, "start_time": "10:30"},
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
        },
    )
    assert res.status_code == 200, res.text
    assert (await _pfvs(db, pa_id))[0].weekday == 2
    assert await _notifications(db) == []


# ---------------------------------------------------------------------------
# SC1: scope-optimization apply (simulate 後に NG 登録)
# ---------------------------------------------------------------------------


async def _seed_scope_sandwich(db) -> dict:
    """FAR — P(BASE, time_flexible) — FAR の Mon A コース (テンプレート付き)."""
    office = await _mk_office(db, name="稲", code="SC1")
    staff = await _mk_staff(db, office=office, name="コース担当")
    tpl_a = await _mk_template(db, office=office, label="A")
    course = await _mk_course(db, office=office, staff=staff, weekday=0, code="A", template=tpl_a)

    p = _mk_patient(code="SC1T", office=office, coords=BASE)
    fa1 = _mk_patient(code="SC1F1", office=office, coords=FAR)
    fa2 = _mk_patient(code="SC1F2", office=office, coords=FAR)
    db.add_all([p, fa1, fa2])
    await db.flush()
    await _mk_visit(db, patient=fa1, course=course, weekday=0, start=time(9, 30))
    await _mk_visit(db, patient=p, course=course, weekday=0, start=time(10, 30))
    await _mk_visit(db, patient=fa2, course=course, weekday=0, start=time(11, 15))
    await _mk_pfv(db, patient=p, weekday=0, start=time(10, 30), movability="time_flexible")
    ids = {"office_id": office.id, "staff_id": staff.id, "patient_id": p.id}
    await db.commit()
    return ids


@pytest.mark.asyncio
async def test_sc1_scope_apply_ng_registered_after_simulate(client, db) -> None:
    """simulate 後に NG 登録 → apply は 422 (PFV 無傷) → ack で 200 + 通知."""
    admin = await _make_admin(db, "cc2-sc1@example.com")
    headers = _bearer(admin)
    seed = await _seed_scope_sandwich(db)
    p_id, staff_id = seed["patient_id"], seed["staff_id"]

    scope_body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": {"office_id": str(seed["office_id"]), "weekdays": [0], "course_codes": ["A"]},
    }
    sim = await client.post(_SCOPE_SIM_URL, headers=headers, json=scope_body)
    assert sim.status_code == 200, sim.text
    sim_data = sim.json()
    assert sim_data["steps"], sim_data["excluded_summary"]

    # ここで NG が登録される (提案生成後 = state_token は PFV 指紋なので変わらない)。
    await _register_ng(client, headers, patient_id=p_id, staff_id=staff_id, note="後から登録")

    apply_body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "scope": scope_body["scope"],
        "state_token": sim_data["state_token"],
        "steps": sim_data["steps"],
    }
    res = await client.post(_SCOPE_APPLY_URL, headers=headers, json=apply_body)
    assert res.status_code == 422, res.text
    _assert_confirmation(res.json()["detail"], patient_id=p_id, staff_id=staff_id)

    # 1 手も適用されていない (PFV は 10:30 のまま)。
    rows = await _pfvs(db, p_id)
    assert len(rows) == 1 and rows[0].start_time == time(10, 30)
    assert await _notifications(db) == []

    res = await client.post(
        _SCOPE_APPLY_URL,
        headers=headers,
        json={**apply_body, "acknowledge_constraint_warnings": True},
    )
    assert res.status_code == 200, res.text
    rows = await _pfvs(db, p_id)
    assert rows[0].start_time != time(10, 30)
    notes = await _notifications(db)
    assert len(notes) == 1
    assert "メモ: 後から登録" in (notes[0].body or "")


# ---------------------------------------------------------------------------
# UB1: propose-unblock apply (部分適用が起きないこと)
# ---------------------------------------------------------------------------


async def _seed_unblock(db) -> dict:
    """Mon A の 10:00 を 1 人が塞ぎ、Tue A に退避先がある最小構成."""
    office = await _mk_office(db, name="稲", code="UB1")
    staff = await _mk_staff(db, office=office, name="開通担当")
    tpl_a = await _mk_template(db, office=office, label="A")
    mon_a = await _mk_course(db, office=office, staff=staff, weekday=0, code="A", template=tpl_a)
    tue_a = await _mk_course(db, office=office, staff=staff, weekday=1, code="A", template=tpl_a)

    blocker = _mk_patient(code="UB1B", office=office, coords=V_COORD)
    filler = _mk_patient(code="UB1F", office=office, coords=FILL)
    target = _mk_patient(code="UB1T", office=office, coords=BASE)
    db.add_all([blocker, filler, target])
    await db.flush()

    await _mk_visit(db, patient=blocker, course=mon_a, weekday=0, start=time(10, 0))
    await _mk_pfv(db, patient=blocker, weekday=0, start=time(10, 0))
    # 退避先バケット (Tue A) を実在させる filler (locked = ブロッカー候補にならない).
    await _mk_visit(db, patient=filler, course=tue_a, weekday=1, start=time(9, 30))
    await _mk_pfv(db, patient=filler, weekday=1, start=time(9, 30), movability="locked")
    ids = {
        "office_id": office.id,
        "staff_id": staff.id,
        "target_id": target.id,
        "blocker_id": blocker.id,
    }
    await db.commit()
    return ids


@pytest.mark.asyncio
async def test_ub1_unblock_apply_ng_blocks_without_partial_apply(client, db) -> None:
    """探索後に **対象患者** へ NG 登録 → 422。退避 (moves) も 1 手も適用されない."""
    admin = await _make_admin(db, "cc2-ub1@example.com")
    headers = _bearer(admin)
    seed = await _seed_unblock(db)
    office_id = seed["office_id"]
    target_id, blocker_id, staff_id = seed["target_id"], seed["blocker_id"], seed["staff_id"]

    sim = await client.post(
        _UNBLOCK_URL,
        headers=headers,
        json={
            "service_minutes": 30,
            "time_type": "固定",
            "preferred_start": "10:00",
            "preferred_weekdays": ["Mon"],
            "lat": BASE[0],
            "lng": BASE[1],
            "existing_patient_id": str(target_id),
            "iso_year": ISO_YEAR,
            "iso_week": ISO_WEEK,
            "office_id": str(office_id),
            "limit": 5,
        },
    )
    assert sim.status_code == 200, sim.text
    sim_data = sim.json()
    assert sim_data["plans"], sim_data

    plan = sim_data["plans"][0]
    assert plan["moves"], "退避手が無いと部分適用の検証にならない"

    # 探索後に対象患者 (= 最後に処理される insert 側) へ NG を登録する。
    # 手ごとに 422 を投げる実装だと「退避は済んだが配置は失敗」になる。
    await _register_ng(client, headers, patient_id=target_id, staff_id=staff_id, note="開通NG")

    apply_body = {
        "office_id": str(office_id),
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "plan": plan,
        "state_token": sim_data["state_token"],
        "target_patient_id": plan["plan_id"].split(":", 1)[0],
    }
    res = await client.post(_UNBLOCK_APPLY_URL, headers=headers, json=apply_body)
    assert res.status_code == 422, res.text
    _assert_confirmation(res.json()["detail"], patient_id=target_id, staff_id=staff_id)

    # **部分適用が起きていない**: 退避対象 (blocker) の PFV は月曜のまま。
    blk_rows = await _pfvs(db, blocker_id)
    assert len(blk_rows) == 1 and blk_rows[0].weekday == 0
    # 対象患者の PFV も作られていない。
    assert await _pfvs(db, target_id) == []
    assert await _notifications(db) == []

    res = await client.post(
        _UNBLOCK_APPLY_URL,
        headers=headers,
        json={**apply_body, "acknowledge_constraint_warnings": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["inserted"] is True

    blk_rows = await _pfvs(db, blocker_id)
    assert blk_rows[0].weekday == 1  # 退避が適用された
    assert len(await _pfvs(db, target_id)) == 1
    notes = await _notifications(db)
    assert len(notes) == 1
    assert "メモ: 開通NG" in (notes[0].body or "")


# ---------------------------------------------------------------------------
# PB1: pool-bulk-apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pb1_pool_bulk_apply_ng_placement(client, db) -> None:
    """NG placement → 422 (PFV 未作成) → ack で 200 + 通知."""
    admin = await _make_admin(db, "cc2-pb1@example.com")
    headers = _bearer(admin)
    office = await _mk_office(db, name="稲", code="PB1")
    ng_staff = await _mk_staff(db, office=office, name="一括NG担当")
    course = await _mk_course(db, office=office, staff=ng_staff, weekday=1, code="A")

    anchor = _mk_patient(code="PB1A", office=office, coords=NEAR)
    pooled = _mk_patient(code="PB1P", office=office, coords=BASE, name="プール患者")
    db.add_all([anchor, pooled])
    await db.flush()
    await _mk_visit(db, patient=anchor, course=course, weekday=1, start=time(9, 30))
    db.add(PatientNgStaff(patient_id=pooled.id, staff_id=ng_staff.id, note="一括投入NG"))
    pooled_id, ng_id, office_id = pooled.id, ng_staff.id, office.id
    await db.commit()

    token = await compute_bulk_state_token(
        db, iso_year=ISO_YEAR, iso_week=ISO_WEEK, office_id=office_id
    )
    body = {
        "iso_year": ISO_YEAR,
        "iso_week": ISO_WEEK,
        "office_id": str(office_id),
        "placements": [
            {
                "seq": 1,
                "patient_id": str(pooled_id),
                "patient_name": "プール患者",
                "weekday": 1,
                "course_code": "A",
                "office_id": str(office_id),
                "start_time": "10:30:00",
                "service_minutes": 30,
            }
        ],
        "state_token": token,
    }

    res = await client.post(_POOL_APPLY_URL, headers=headers, json=body)
    assert res.status_code == 422, res.text
    _assert_confirmation(res.json()["detail"], patient_id=pooled_id, staff_id=ng_id)
    assert await _pfvs(db, pooled_id) == []
    assert await _notifications(db) == []

    res = await client.post(
        _POOL_APPLY_URL,
        headers=headers,
        json={**body, "acknowledge_constraint_warnings": True},
    )
    assert res.status_code == 200, res.text
    assert res.json()["applied_slots"] == 1
    assert len(await _pfvs(db, pooled_id)) == 1
    notes = await _notifications(db)
    assert len(notes) == 1
    assert "メモ: 一括投入NG" in (notes[0].body or "")


# ---------------------------------------------------------------------------
# V1 / V2 / V3: visits 直 API (FE 導線なし = 単純 422)
# ---------------------------------------------------------------------------


async def _seed_visit_api(db, *, prefix: str) -> dict:
    office = await _mk_office(db, name="稲", code=prefix)
    ng_staff = await _mk_staff(db, office=office, name=f"{prefix}NG担当", shifts=False)
    ok_staff = await _mk_staff(db, office=office, name=f"{prefix}通常担当", shifts=False)
    course = await _mk_course(db, office=office, staff=ng_staff, weekday=0, code="A")
    patient = _mk_patient(code=f"{prefix}P", office=office)
    db.add(patient)
    await db.flush()
    visit = await _mk_visit(db, patient=patient, course=course, weekday=0, start=time(10, 0))
    visit.course_id = None
    visit.primary_staff_id = None
    db.add(PatientNgStaff(patient_id=patient.id, staff_id=ng_staff.id, note=f"{prefix}直API"))
    await db.commit()
    return {
        "office_id": office.id,
        "ng_staff_id": ng_staff.id,
        "ok_staff_id": ok_staff.id,
        "course_id": course.id,
        "patient_id": patient.id,
        "visit_id": visit.id,
    }


@pytest.mark.asyncio
async def test_v1_add_visit_staff_ng_is_422(client, db) -> None:
    admin = await _make_admin(db, "cc2-v1@example.com")
    headers = _bearer(admin)
    seed = await _seed_visit_api(db, prefix="VA1")

    res = await client.post(
        f"{_VISITS_URL}/{seed['visit_id']}/staff",
        headers=headers,
        json={"staff_id": str(seed["ng_staff_id"])},
    )
    assert res.status_code == 422, res.text
    _assert_confirmation(
        res.json()["detail"], patient_id=seed["patient_id"], staff_id=seed["ng_staff_id"]
    )

    # 違反しないスタッフは従来どおり通る (後方互換)。
    res = await client.post(
        f"{_VISITS_URL}/{seed['visit_id']}/staff",
        headers=headers,
        json={"staff_id": str(seed["ok_staff_id"])},
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_v2_patch_visit_staff_or_course_ng_is_422(client, db) -> None:
    admin = await _make_admin(db, "cc2-v2@example.com")
    headers = _bearer(admin)
    seed = await _seed_visit_api(db, prefix="VA2")
    url = f"{_VISITS_URL}/{seed['visit_id']}"

    # ① 担当の名指し差し替え。
    res = await client.patch(
        url, headers=headers, json={"primary_staff_id": str(seed["ng_staff_id"])}
    )
    assert res.status_code == 422, res.text
    _assert_confirmation(
        res.json()["detail"], patient_id=seed["patient_id"], staff_id=seed["ng_staff_id"]
    )

    # ② コース付け替え (コースの現担当が NG)。
    res = await client.patch(url, headers=headers, json={"course_id": str(seed["course_id"])})
    assert res.status_code == 422, res.text
    _assert_confirmation(
        res.json()["detail"], patient_id=seed["patient_id"], staff_id=seed["ng_staff_id"]
    )

    # ③ 無関係なフィールドの更新は蒸し返さない (後方互換)。
    res = await client.patch(url, headers=headers, json={"note": "メモ更新"})
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_v3_create_visit_with_ng_staff_is_422(client, db) -> None:
    admin = await _make_admin(db, "cc2-v3@example.com")
    headers = _bearer(admin)
    seed = await _seed_visit_api(db, prefix="VA3")

    body = {
        "patient_id": str(seed["patient_id"]),
        "primary_staff_id": str(seed["ng_staff_id"]),
        "visit_date": (MON + timedelta(days=2)).isoformat(),
        "start_time": "14:00:00",
        "end_time": "14:30:00",
        "type": "regular",
        "status": "planned",
        "source": "manual",
    }
    res = await client.post(_VISITS_URL, headers=headers, json=body)
    assert res.status_code == 422, res.text
    _assert_confirmation(
        res.json()["detail"], patient_id=seed["patient_id"], staff_id=seed["ng_staff_id"]
    )

    # course_id 経由でも同様に弾く。
    body_course = {**body}
    del body_course["primary_staff_id"]
    body_course["course_id"] = str(seed["course_id"])
    res = await client.post(_VISITS_URL, headers=headers, json=body_course)
    assert res.status_code == 422, res.text

    # 違反しない担当なら作成できる (後方互換)。
    res = await client.post(
        _VISITS_URL,
        headers=headers,
        json={**body, "primary_staff_id": str(seed["ok_staff_id"])},
    )
    assert res.status_code == 201, res.text
