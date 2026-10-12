"""青ピン廃止 + 「この週を空にする」+ 空の週でだけ生成 (PO 2026-10-12).

設計: docs/plans/blue-pin/blue-pin-abolish-design-2026-10-12.md (BP-1〜BP-6)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Course, Office, Patient, Staff, User, Visit
from app.models.audit_log import AuditLog
from app.models.visit_checkin import VisitCheckin
from app.models.visit_staff_assignment import VisitStaffAssignment
from tests.test_w17_split_endpoints import _seed_office_staff_courses

YEAR, WEEK = 2026, 23  # 月曜 = 2026-06-01
MONDAY = date(2026, 6, 1)
CLEAR = "/api/v1/schedule/v2/weeks/{y}/{w}/clear"
GEN = "/api/v1/schedule/generate-week-only"
EMPTY_MSG = "予定が入っている週では生成できません。先に『この週を空にする』を押してください"


async def _admin(db, email="bp-admin@example.com") -> User:
    u = User(email=email, password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _h(u: User) -> dict[str, str]:
    t = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    return {"Authorization": f"Bearer {t}"}


async def _patient(db, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", status="active", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(db, p: Patient, *, day=0, hour=9, source="auto", status="planned", **kw) -> Visit:
    v = Visit(
        patient_id=p.id,
        visit_date=date(2026, 6, 1 + day),
        start_time=time(hour, 0),
        end_time=time(hour, 45),
        type="regular",
        status=status,
        source=source,
        **kw,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _live(db) -> list[Visit]:
    return list((await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all())


# ---------------------------------------------------------------------------
# 青ピン API は無い / 青ピンはもう何も止めない
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_pin_routes_are_gone(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "BP-1")
    v = await _visit(db, p)
    r1 = await client.patch(
        f"/api/v1/schedule/v2/visits/{v.id}/week-pin", headers=_h(admin), json={"pinned": True}
    )
    r2 = await client.post(
        "/api/v1/schedule/v2/visits/week-pin/bulk",
        headers=_h(admin),
        json={"iso_year": YEAR, "iso_week": WEEK, "pinned": True},
    )
    assert r1.status_code == 404, r1.text
    assert r2.status_code in (404, 405), r2.text


@pytest.mark.asyncio
async def test_former_blue_pinned_row_no_longer_blocks(client, db) -> None:
    """列に true が残っていても (1 リリース残す) 配置変更・削除は止まらない."""
    admin = await _admin(db)
    p = await _patient(db, "BP-2")
    v1 = await _visit(db, p, hour=9, week_pinned=True)
    v2 = await _visit(db, p, day=1, hour=9, week_pinned=True)

    r = await client.patch(
        f"/api/v1/visits/{v1.id}",
        headers=_h(admin),
        json={"start_time": "10:00:00", "end_time": "10:45:00"},
    )
    assert r.status_code == 200, r.text
    r = await client.delete(f"/api/v1/visits/{v2.id}", headers=_h(admin))
    assert r.status_code in (200, 204), r.text
    rd = await client.get(f"/api/v1/visits/{v1.id}", headers=_h(admin))
    assert "week_pinned" not in rd.json()


@pytest.mark.asyncio
async def test_former_blue_pinned_row_does_not_block_cancel_week(client, db) -> None:
    admin = await _admin(db)
    p = await _patient(db, "BP-3")
    v = await _visit(db, p, day=3, week_pinned=True)
    r = await client.post(
        "/api/v1/schedule/v2/visit-cancel-week",
        headers=_h(admin),
        json={"visit_ids": [str(v.id)], "cancel": True},
    )
    # 過去日のガード等で 422 になり得るため、青ピンの文言で止まっていないことだけ確かめる
    assert "青ピン" not in r.text


# ---------------------------------------------------------------------------
# この週を空にする
# ---------------------------------------------------------------------------


async def _board(db):
    """消える 4 件と残る 3 件 (打刻あり・完了・取消の記録)."""
    pa, pb, pc, pd = [await _patient(db, f"CL-{i}") for i in range(4)]
    d_auto = await _visit(db, pa, hour=9, source="auto")
    d_mw = await _visit(db, pb, hour=10, source="manual_week")
    d_imp = await _visit(db, pc, hour=11, source="import", kaipoke_id="K-1")
    d_pin = await _visit(db, pd, hour=12, source="auto", week_pinned=True)
    k_chk = await _visit(db, pa, day=1, hour=9, source="import")
    db.add(
        VisitCheckin(
            visit_id=k_chk.id,
            patient_id=pa.id,
            kind="arrival",
            scanned_at=datetime.now(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    k_done = await _visit(db, pb, day=1, hour=10, status="completed")
    k_cxl = await _visit(db, pc, day=2, hour=9, status="cancelled", source="manual_cancel")
    await db.commit()
    return {"delete": [d_auto, d_mw, d_imp, d_pin], "keep": [k_chk, k_done, k_cxl]}


@pytest.mark.asyncio
async def test_clear_dry_run_counts_and_changes_nothing(client, db) -> None:
    admin = await _admin(db)
    await _board(db)
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to_delete"] == 4
    assert body["kaipoke_linked"] == 1
    assert body["kept_checked_in"] == 2  # 打刻あり + 完了。取消の記録は数えない
    assert body.get("snapshot_id") is None
    assert len(await _live(db)) == 7
    logs = (
        await db.scalars(select(AuditLog).where(AuditLog.action == "schedule_clear_week"))
    ).all()
    assert list(logs) == []


@pytest.mark.asyncio
async def test_clear_deletes_all_but_checked_in_and_cancelled_then_undo(client, db) -> None:
    admin = await _admin(db)
    b = await _board(db)
    staff = Staff(code="CL-S", name="職員", role="staff", status="active")
    db.add(staff)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=b["delete"][0].id, staff_id=staff.id))
    await db.commit()
    keep_ids = {v.id for v in b["keep"]}
    del_ids = {v.id for v in b["delete"]}

    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to_delete"] == 4 and body["kaipoke_linked"] == 1 and body["kept_checked_in"] == 2
    snap_id = body["snapshot_id"]
    assert snap_id
    assert {v.id for v in await _live(db)} == keep_ids
    logs = (
        await db.scalars(select(AuditLog).where(AuditLog.action == "schedule_clear_week"))
    ).all()
    assert len(logs) == 1

    # 元に戻す: 消した 4 件だけが戻り、残した 3 件は二重にならない
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert u.status_code == 200, u.text
    assert u.json()["restored"] == 4
    live = await _live(db)
    assert len(live) == 7
    assert keep_ids <= {v.id for v in live}
    restored = [v for v in live if v.id not in keep_ids]
    assert len(restored) == 4 and not (del_ids & {v.id for v in restored})
    assert {v.source for v in restored} == {"auto", "manual_week", "import"}
    assert any(v.kaipoke_id == "K-1" for v in restored)
    vsa = await db.scalar(
        select(VisitStaffAssignment).where(
            VisitStaffAssignment.visit_id.in_([v.id for v in restored])
        )
    )
    assert vsa is not None
    # 二度は戻せない
    again = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_clear_undo_refused_when_week_changed_after_clear(client, db) -> None:
    admin = await _admin(db)
    await _board(db)
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": False})
    snap_id = r.json()["snapshot_id"]
    await _visit(db, await _patient(db, "CL-NEW"), hour=15)  # 空にした後に予定が入る
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert u.status_code == 409


async def _office_board(db):
    """事業所 A の患者 2 名 (予定 1 件ずつ) と事業所 B の患者 1 名."""
    oa, ob = Office(name="拠点A", lat=35.0, lng=139.0), Office(name="拠点B", lat=35.1, lng=139.1)
    db.add_all([oa, ob])
    await db.flush()
    pa = await _patient(db, "OF-A")
    pb = await _patient(db, "OF-B")
    pa.primary_office_id, pb.primary_office_id = oa.id, ob.id
    await db.commit()
    va = await _visit(db, pa, hour=9)
    vb = await _visit(db, pb, hour=10)
    return oa, ob, pa, pb, va, vb


@pytest.mark.asyncio
async def test_office_limited_clear_can_be_undone_despite_other_office(client, db) -> None:
    admin = await _admin(db)
    oa, ob, pa, pb, va, vb = await _office_board(db)
    r = await client.post(
        CLEAR.format(y=YEAR, w=WEEK),
        headers=_h(admin),
        json={"dry_run": False, "office_id": str(oa.id)},
    )
    assert r.status_code == 200, r.text
    assert {v.id for v in await _live(db)} == {vb.id}
    # 他の事業所に後から予定が入っても止めない
    await _visit(db, pb, hour=15)
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo",
        headers=_h(admin),
        json={"snapshot_id": r.json()["snapshot_id"]},
    )
    assert u.status_code == 200, u.text
    assert u.json()["restored"] == 1


@pytest.mark.asyncio
async def test_office_limited_clear_undo_refused_when_that_office_changed(client, db) -> None:
    admin = await _admin(db)
    oa, ob, pa, pb, va, vb = await _office_board(db)
    r = await client.post(
        CLEAR.format(y=YEAR, w=WEEK),
        headers=_h(admin),
        json={"dry_run": False, "office_id": str(oa.id)},
    )
    assert {v.id for v in await _live(db)} == {vb.id}
    await _visit(db, pa, hour=15)  # 同じ事業所に予定が入る
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo",
        headers=_h(admin),
        json={"snapshot_id": r.json()["snapshot_id"]},
    )
    assert u.status_code == 409


@pytest.mark.asyncio
async def test_clear_is_admin_only(client, db) -> None:
    u = User(email="bp-staff@example.com", password_hash=hash_password("pw"), role="staff")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(u), json={"dry_run": True})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_clear_unassigns_course_staff_and_undo_restores(client, db) -> None:
    admin = await _admin(db)
    seed = await _seed_office_staff_courses(db)
    course = seed["courses"][0]
    course.assigned_staff_id = seed["staff"][0].id
    course.course_status = "staff_assigned"
    assigned_at = datetime(2026, 5, 30, 1, 2, 3, tzinfo=UTC)
    course.staff_assigned_at = assigned_at
    await db.commit()
    cid = course.id
    sid = seed["staff"][0].id
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": False})
    assert r.status_code == 200, r.text
    c1 = await db.get(Course, cid)
    await db.refresh(c1)
    assert c1.assigned_staff_id is None and c1.course_status == "course_fixed"
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo",
        headers=_h(admin),
        json={"snapshot_id": r.json()["snapshot_id"]},
    )
    assert u.status_code == 200, u.text
    c2 = await db.get(Course, cid)
    await db.refresh(c2)
    assert c2.assigned_staff_id == sid and c2.course_status == "staff_assigned"
    assert c2.staff_assigned_at.replace(tzinfo=None) == assigned_at.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 週を生成は空の週でだけ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_refuses_non_empty_week_with_409(client, db) -> None:
    admin = await _admin(db)
    seed = await _seed_office_staff_courses(db)
    await _visit(db, seed["patients"][0], source="manual_week")
    bodies = (
        {"iso_year": YEAR, "iso_week": WEEK},
        {"iso_year": YEAR, "iso_week": WEEK, "dry_run": True},
    )
    for body in bodies:
        r = await client.post(GEN, headers=_h(admin), json=body)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == EMPTY_MSG


@pytest.mark.asyncio
async def test_generate_works_on_empty_and_after_clear(client, db) -> None:
    admin = await _admin(db)
    await _seed_office_staff_courses(db)
    base = {"iso_year": YEAR, "iso_week": WEEK}
    d = await client.post(GEN, headers=_h(admin), json={**base, "dry_run": True})
    assert d.status_code == 200, d.text
    assert d.json() == {"to_add": 4}
    assert await _live(db) == []  # dry_run は何も書かない
    r = await client.post(GEN, headers=_h(admin), json=base)
    assert r.status_code == 200, r.text
    assert r.json()["visits_created"] == 4
    # 2 回目は 409 → 空にする → 生成できる
    assert (await client.post(GEN, headers=_h(admin), json=base)).status_code == 409
    c = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": False})
    assert c.json()["to_delete"] == 4
    again = await client.post(GEN, headers=_h(admin), json=base)
    assert again.status_code == 200 and again.json()["visits_created"] == 4


@pytest.mark.asyncio
async def test_generate_ok_when_only_checked_in_and_cancelled_remain(client, db) -> None:
    admin = await _admin(db)
    seed = await _seed_office_staff_courses(db)
    p = seed["patients"][0]
    await _visit(db, p, day=1, status="completed")
    await _visit(db, p, day=2, status="cancelled", source="manual_cancel")
    r = await client.post(GEN, headers=_h(admin), json={"iso_year": YEAR, "iso_week": WEEK})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 取込プレビュー: らく助で調整した訪問のうち置き換わるもの
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_preview_lists_rakusuke_adjusted_replaced(db) -> None:
    from app.api.v1.integrations import _rakusuke_adjusted_replaced
    from app.services.diff.engine import ScheduleEntry

    p = await _patient(db, "IN-1")
    mw = await _visit(db, p, day=0, hour=9, source="manual_week")
    await _visit(db, p, day=0, hour=13, source="auto")  # 調整ではない
    await _visit(db, p, day=1, hour=9, source="manual_week")  # 対象日ではない
    entry = ScheduleEntry(
        user_name=p.name,
        date="1",
        weekday="月",
        business_type="介護保険",
        service_type="訪問看護",
        start_time="09:00",
        end_time="09:45",
        staff1_name="x",
        staff1_type="看護師",
    )
    got = await _rakusuke_adjusted_replaced(
        db, week_start=MONDAY, target_days={MONDAY}, entries=[entry]
    )
    assert [g.visit_id for g in got] == [mw.id]
    assert got[0].patient_label == f"{p.name}様"
    assert got[0].start_time == "09:00" and got[0].date == MONDAY
    assert got[0].change == "カイポケの内容で作り直されます"
    gone = await _rakusuke_adjusted_replaced(
        db, week_start=MONDAY, target_days={MONDAY}, entries=[]
    )
    assert gone[0].change == "カイポケに無いため消えます"
    # 対象日が無ければ空 (画面は何も出さない)
    none = await _rakusuke_adjusted_replaced(db, week_start=MONDAY, target_days=set(), entries=[])
    assert none == []


@pytest.mark.asyncio
async def test_inbound_preview_excludes_held_back_days(db) -> None:
    """プレビューが見送る日 (対象日から外した日) の訪問は挙げない."""
    from app.api.v1.integrations import _rakusuke_adjusted_replaced

    p = await _patient(db, "IN-2")
    await _visit(db, p, day=0, hour=9, source="manual_week")
    d1 = await _visit(db, p, day=1, hour=9, source="manual_week")
    got = await _rakusuke_adjusted_replaced(
        db, week_start=MONDAY, target_days={MONDAY + timedelta(days=1)}, entries=[]
    )
    assert [g.visit_id for g in got] == [d1.id]
