"""期間で「この週を空にする」「型から週を作る」(エージェントが期間で組む・2026-11-16).

設計: docs/plans/agent-period-build-design-2026-11-16.md §3-1・§5
範囲 (date_from〜date_to・同じ週の中) を渡すと、その日だけを空にする／作る。範囲の外の
日の訪問とコースの担当には触れない。範囲を渡さなければ今までどおり週全体。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import (
    Course,
    CourseTemplate,
    Office,
    Patient,
    PatientFixedVisit,
    Staff,
    User,
    Visit,
)
from app.models.audit_log import AuditLog
from app.models.inbound_snapshot import InboundSnapshot
from app.models.visit_checkin import VisitCheckin
from app.services.leave_calendar import iso_key
from app.services.op_log_service import set_staff_override
from app.services.scheduling.layer1_expander import Layer1Expander
from app.services.staff_leave import settle_leave_unassign, today_jst

YEAR, WEEK = 2026, 23  # 月曜 = 2026-06-01
MONDAY = date(2026, 6, 1)
CLEAR = "/api/v1/schedule/v2/weeks/{y}/{w}/clear"
GEN = "/api/v1/schedule/generate-week-only"


def _d(day: int) -> date:
    return MONDAY + timedelta(days=day)


async def _admin(db) -> User:
    u = User(email="range-admin@example.com", password_hash=hash_password("pw"), role="admin")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _h(u: User) -> dict[str, str]:
    t = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    return {"Authorization": f"Bearer {t}"}


async def _patient(db, code: str, office: Office | None = None) -> Patient:
    p = Patient(
        code=code,
        name=f"テスト患者{code}",
        status="active",
        special_week_active=[],
        primary_office_id=office.id if office else None,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(
    db, p: Patient, *, on: date, hour=9, source="auto", status="planned", **kw
) -> Visit:
    v = Visit(
        patient_id=p.id,
        visit_date=on,
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
    return list(
        (
            await db.scalars(
                select(Visit)
                .where(Visit.deleted_at.is_(None))
                .execution_options(populate_existing=True)
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# 範囲つきの「この週を空にする」
# ---------------------------------------------------------------------------


async def _clear_board(db):
    """範囲 = 火〜金。月・土は範囲の外."""
    office = Office(name="テスト拠点 空にする", lat=35.6, lng=139.7)
    db.add(office)
    await db.flush()
    staff = Staff(code="RG-S", name="テスト職員", role="staff", status="active")
    db.add(staff)
    await db.flush()
    pa, pb, pc = [await _patient(db, f"RG-{i}") for i in range(3)]
    out_mon = await _visit(db, pa, on=_d(0), source="auto")
    out_mon_mw = await _visit(db, pb, on=_d(0), hour=10, source="manual_week")
    out_sat = await _visit(db, pc, on=_d(5), source="import", kaipoke_id="K-SAT")
    in_auto = await _visit(db, pa, on=_d(1), source="auto")
    in_mw = await _visit(db, pb, on=_d(2), hour=10, source="manual_week")
    in_chk = await _visit(db, pc, on=_d(3), source="import")
    db.add(
        VisitCheckin(
            visit_id=in_chk.id,
            patient_id=pc.id,
            kind="arrival",
            scanned_at=datetime.now(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    in_done = await _visit(db, pa, on=_d(4), status="completed")
    in_cxl = await _visit(db, pb, on=_d(4), hour=11, status="cancelled", source="manual_cancel")
    in_unpl = await _visit(db, pc, on=_d(4), hour=13, is_unplanned=True)
    # コース: 月 (範囲の外) と火 (範囲の中・残す訪問なし) と木 (範囲の中・打刻あり)
    courses = {}
    for wd in (0, 1, 3):
        c = Course(
            iso_year=YEAR,
            iso_week=WEEK,
            weekday=wd,
            code="A",
            office_id=office.id,
            course_status="staff_assigned",
            assigned_staff_id=staff.id,
            staff_assigned_at=datetime(2026, 5, 30, 1, 0, tzinfo=UTC),
        )
        db.add(c)
        courses[wd] = c
    await db.flush()
    in_chk.course_id = courses[3].id
    await db.commit()
    return {
        "staff": staff,
        "outside": [out_mon, out_mon_mw, out_sat],
        "delete": [in_auto, in_mw],
        "keep": [in_chk, in_done, in_cxl, in_unpl],
        "courses": courses,
    }


@pytest.mark.asyncio
async def test_ranged_clear_dry_run_counts_only_range(client, db) -> None:
    admin = await _admin(db)
    await _clear_board(db)
    body = {"dry_run": True, "date_from": _d(1).isoformat(), "date_to": _d(4).isoformat()}
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "to_delete": 2,
        "kaipoke_linked": 0,
        "kept_checked_in": 3,  # 打刻あり・完了・予定外 (取消は数えない)
        "snapshot_id": None,
    }
    assert len(await _live(db)) == 9


@pytest.mark.asyncio
async def test_ranged_clear_keeps_outside_and_undo_restores_only_cleared(client, db) -> None:
    admin = await _admin(db)
    b = await _clear_board(db)
    staff_id = b["staff"].id
    outside_ids = {v.id for v in b["outside"]}
    keep_ids = {v.id for v in b["keep"]}
    cids = {wd: c.id for wd, c in b["courses"].items()}

    body = {"dry_run": False, "date_from": _d(1).isoformat(), "date_to": _d(4).isoformat()}
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["to_delete"] == 2 and r.json()["kept_checked_in"] == 3
    snap_id = r.json()["snapshot_id"]

    # 範囲の外の訪問と、範囲の中の残す訪問 (打刻・完了・取消・予定外) は残る
    assert {v.id for v in await _live(db)} == outside_ids | keep_ids
    # コース: 月 (範囲の外) はそのまま・火は外れる・木 (打刻あり) はそのまま
    mon, tue, thu = [await db.get(Course, cids[w]) for w in (0, 1, 3)]
    for c in (mon, tue, thu):
        await db.refresh(c)
    assert mon.assigned_staff_id == staff_id and mon.course_status == "staff_assigned"
    assert tue.assigned_staff_id is None and tue.course_status == "course_fixed"
    assert thu.assigned_staff_id == staff_id

    # スナップショットと監査ログに範囲が残る
    snap = await db.get(InboundSnapshot, UUID(snap_id))
    assert snap.payload["date_from"] == "2026-06-02" and snap.payload["date_to"] == "2026-06-05"
    # 範囲の外の訪問は別の鍵へ (kept_visit_ids は範囲の中の残した訪問だけ)
    assert {UUID(x) for x in snap.payload["outside_visit_ids"]} == outside_ids
    assert {UUID(x) for x in snap.payload["kept_visit_ids"]} == keep_ids
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "schedule_clear_week"))
    assert log.after["date_from"] == "2026-06-02" and log.after["date_to"] == "2026-06-05"
    assert log.after["courses_cleared"] == 1

    # 元に戻す: 消した 2 件だけが戻る (範囲の外の訪問があっても止まらない)
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert u.status_code == 200, u.text
    assert u.json()["restored"] == 2
    live = await _live(db)
    assert len(live) == 9
    restored = [v for v in live if v.id not in outside_ids | keep_ids]
    assert sorted((v.visit_date, v.source) for v in restored) == [
        (_d(1), "auto"),
        (_d(2), "manual_week"),
    ]
    await db.refresh(tue)
    assert tue.assigned_staff_id == staff_id and tue.course_status == "staff_assigned"


@pytest.mark.asyncio
async def test_ranged_clear_undo_refused_when_range_changed(client, db) -> None:
    admin = await _admin(db)
    await _clear_board(db)
    body = {"dry_run": False, "date_from": _d(1).isoformat(), "date_to": _d(4).isoformat()}
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json=body)
    await _visit(db, await _patient(db, "RG-NEW"), on=_d(2), hour=15)  # 範囲の中に予定が入る
    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo",
        headers=_h(admin),
        json={"snapshot_id": r.json()["snapshot_id"]},
    )
    assert u.status_code == 409


async def _ranged_clear(client, admin) -> str:
    body = {"dry_run": False, "date_from": _d(1).isoformat(), "date_to": _d(4).isoformat()}
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json=body)
    assert r.status_code == 200, r.text
    return r.json()["snapshot_id"]


@pytest.mark.asyncio
async def test_ranged_clear_undo_refused_when_outside_visit_moved_into_range(client, db) -> None:
    """範囲の外の訪問を (同じ id のまま) 範囲の中へ動かした後は戻さない (二重を作らない)."""
    admin = await _admin(db)
    b = await _clear_board(db)
    snap_id = await _ranged_clear(client, admin)
    moved = b["outside"][0]
    moved.visit_date = _d(1)  # 月 → 火 (範囲の中・消した訪問と同じ患者・時刻)
    await db.commit()
    before = {v.id for v in await _live(db)}

    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert u.status_code == 409, u.text
    assert {v.id for v in await _live(db)} == before  # 何も戻していない
    snap = await db.get(InboundSnapshot, UUID(snap_id))
    await db.refresh(snap)
    assert "restored_at" not in snap.payload


@pytest.mark.asyncio
async def test_ranged_clear_undo_allowed_after_outside_addition(client, db) -> None:
    """範囲の外に予定が足されても戻せる。戻るのは消した訪問だけ."""
    admin = await _admin(db)
    b = await _clear_board(db)
    snap_id = await _ranged_clear(client, admin)
    added = await _visit(db, await _patient(db, "RG-OUT"), on=_d(5), hour=15)
    before = {v.id for v in await _live(db)}
    assert added.id in before

    u = await client.post(
        CLEAR.format(y=YEAR, w=WEEK) + "/undo", headers=_h(admin), json={"snapshot_id": snap_id}
    )
    assert u.status_code == 200, u.text
    assert u.json()["restored"] == 2
    live = await _live(db)
    restored = [v for v in live if v.id not in before]
    assert sorted((v.visit_date, v.source) for v in restored) == [
        (_d(1), "auto"),
        (_d(2), "manual_week"),
    ]
    assert len(live) == len(before) + 2
    assert {v.id for v in b["outside"]} <= {v.id for v in live}


@pytest.mark.asyncio
async def test_ranged_clear_keeps_course_of_outside_visit(client, db) -> None:
    """範囲の外の訪問が範囲の中の曜日のコースを指していれば、その担当を外さない."""
    admin = await _admin(db)
    b = await _clear_board(db)
    tue = b["courses"][1]
    b["outside"][0].course_id = tue.id  # 月の訪問が火のコースを指す (日付を変えた後の名残)
    await db.commit()
    tue_id, staff_id = tue.id, b["staff"].id

    await _ranged_clear(client, admin)
    tue = await db.get(Course, tue_id)
    await db.refresh(tue)
    assert tue.assigned_staff_id == staff_id and tue.course_status == "staff_assigned"
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "schedule_clear_week"))
    assert log.after["courses_cleared"] == 0


@pytest.mark.asyncio
async def test_ranged_clear_with_office_only_touches_that_office(client, db) -> None:
    """拠点つき・範囲つき: その拠点の範囲の中のコースと訪問だけ."""
    admin = await _admin(db)
    staff = Staff(code="RG-OS", name="テスト職員二", role="staff", status="active")
    o1 = Office(name="テスト拠点 一", lat=35.6, lng=139.7)
    o2 = Office(name="テスト拠点 二", lat=35.6, lng=139.7)
    db.add_all([staff, o1, o2])
    await db.flush()
    p1 = await _patient(db, "OF-1", o1)
    p2 = await _patient(db, "OF-2", o2)
    await _visit(db, p1, on=_d(1))
    v1_out = await _visit(db, p1, on=_d(0))
    v2_in = await _visit(db, p2, on=_d(1), hour=11)
    courses = {}
    for key, office, wd in (("o1_in", o1, 1), ("o1_out", o1, 0), ("o2_in", o2, 1)):
        c = Course(
            iso_year=YEAR,
            iso_week=WEEK,
            weekday=wd,
            code="A",
            office_id=office.id,
            course_status="staff_assigned",
            assigned_staff_id=staff.id,
        )
        db.add(c)
        courses[key] = c
    await db.commit()
    cids = {k: c.id for k, c in courses.items()}
    staff_id = staff.id

    r = await client.post(
        CLEAR.format(y=YEAR, w=WEEK),
        headers=_h(admin),
        json={
            "dry_run": False,
            "office_id": str(o1.id),
            "date_from": _d(1).isoformat(),
            "date_to": _d(4).isoformat(),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["to_delete"] == 1
    assert {v.id for v in await _live(db)} == {v1_out.id, v2_in.id}
    got = {}
    for k, cid in cids.items():
        c = await db.get(Course, cid)
        await db.refresh(c)
        got[k] = c.assigned_staff_id
    assert got == {"o1_in": None, "o1_out": staff_id, "o2_in": staff_id}


@pytest.mark.asyncio
async def test_clear_without_range_unchanged_payload(client, db) -> None:
    """範囲なしは週全体 (スナップショット・監査ログに範囲の鍵を足さない)."""
    admin = await _admin(db)
    await _clear_board(db)
    r = await client.post(CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["to_delete"] == 5  # 範囲の外 3 + 中 2
    snap = await db.get(InboundSnapshot, UUID(r.json()["snapshot_id"]))
    assert "date_from" not in snap.payload
    assert "outside_visit_ids" not in snap.payload
    log = await db.scalar(select(AuditLog).where(AuditLog.action == "schedule_clear_week"))
    assert "date_from" not in log.after


# ---------------------------------------------------------------------------
# 範囲つきの「型から週を作る」
# ---------------------------------------------------------------------------


async def _gen_seed(db) -> tuple[Office, Patient, Patient]:
    """患者 A = 月〜土 9 時、患者 B = 月・水・金 10 時 (固定訪問)."""
    office = Office(name="テスト拠点 期間", lat=35.6, lng=139.7)
    db.add(office)
    await db.flush()
    tpl = CourseTemplate(office_id=office.id, label="A")
    db.add(tpl)
    await db.flush()
    pa = await _patient(db, "GN-A", office)
    pb = await _patient(db, "GN-B", office)
    for p, hour, days in ((pa, 9, range(6)), (pb, 10, (0, 2, 4))):
        for wd in days:
            db.add(
                PatientFixedVisit(
                    patient_id=p.id,
                    mode="normal",
                    weekday=wd,
                    start_time=time(hour, 0),
                    duration_min=60,
                    course_template_id=tpl.id,
                )
            )
    await db.commit()
    return office, pa, pb


@pytest.mark.asyncio
async def test_ranged_generate_keeps_outside_days_and_counts_match(client, db) -> None:
    admin = await _admin(db)
    _office, pa, pb = await _gen_seed(db)
    mon_auto = await _visit(db, pa, on=_d(0), source="auto")
    mon_mw = await _visit(db, pb, on=_d(0), hour=10, source="manual_week")

    full = {"iso_year": YEAR, "iso_week": WEEK}
    ranged = {**full, "date_from": _d(1).isoformat(), "date_to": _d(5).isoformat()}
    # 週全体では 409 (月曜に予定がある)・範囲の中は空なので通る
    r409 = await client.post(GEN, headers=_h(admin), json=full)
    assert r409.status_code == 409, r409.text

    d = await client.post(GEN, headers=_h(admin), json={**ranged, "dry_run": True})
    assert d.status_code == 200, d.text
    assert d.json() == {"to_add": 7}  # A 火〜土 5 + B 水・金 2
    assert {v.id for v in await _live(db)} == {mon_auto.id, mon_mw.id}  # dry_run は書かない

    r = await client.post(GEN, headers=_h(admin), json=ranged)
    assert r.status_code == 200, r.text
    assert r.json()["visits_created"] == 7

    live = await _live(db)
    mondays = [v for v in live if v.visit_date == _d(0)]
    assert {v.id for v in mondays} == {mon_auto.id, mon_mw.id}  # 消えない・増えない
    assert not [v for v in live if v.visit_date == _d(6)]
    by_day = sorted((v.visit_date - MONDAY).days for v in live if v.visit_date != _d(0))
    assert by_day == [1, 2, 2, 3, 4, 4, 5]
    # 範囲の外の曜日のコースは作らない (月曜のコースは作られない)
    mon_courses = (
        await db.scalars(
            select(Course).where(
                Course.iso_year == YEAR,
                Course.iso_week == WEEK,
                Course.weekday == 0,
            )
        )
    ).all()
    assert [c.code for c in mon_courses if not c.code.startswith("M")] == []

    # 同じ範囲は 2 回目 409・範囲を空にしてから作り直せる
    assert (await client.post(GEN, headers=_h(admin), json=ranged)).status_code == 409


@pytest.mark.asyncio
async def test_ranged_generate_count_equals_full_week_for_same_weekdays(client, db) -> None:
    """範囲の件数 = 週全体で作ったときのその曜日の件数 (空の週どうしで比べる)."""
    admin = await _admin(db)
    await _gen_seed(db)
    full = await client.post(GEN, headers=_h(admin), json={"iso_year": YEAR, "iso_week": WEEK})
    assert full.status_code == 200, full.text
    live = await _live(db)
    expected = sum(1 for v in live if _d(1) <= v.visit_date <= _d(5))
    assert full.json()["visits_created"] == 9

    nxt = MONDAY + timedelta(days=7)
    body = {
        "iso_year": YEAR,
        "iso_week": WEEK + 1,
        "date_from": (nxt + timedelta(days=1)).isoformat(),
        "date_to": (nxt + timedelta(days=5)).isoformat(),
    }
    r = await client.post(GEN, headers=_h(admin), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["visits_created"] == expected == 7
    # 前の週 (範囲の外) の訪問は 1 件も変わらない
    assert {v.id for v in live} <= {v.id for v in await _live(db)}


@pytest.mark.asyncio
async def test_ranged_generate_409_when_range_has_visit(client, db) -> None:
    admin = await _admin(db)
    _office, pa, _pb = await _gen_seed(db)
    await _visit(db, pa, on=_d(3), source="manual_week")
    body = {
        "iso_year": YEAR,
        "iso_week": WEEK,
        "date_from": _d(1).isoformat(),
        "date_to": _d(5).isoformat(),
    }
    for extra in ({}, {"dry_run": True}):
        r = await client.post(GEN, headers=_h(admin), json={**body, **extra})
        assert r.status_code == 409, r.text
    # 1 日だけ (月曜〜水曜) なら通る
    one = {**body, "date_from": _d(0).isoformat(), "date_to": _d(2).isoformat()}
    r = await client.post(GEN, headers=_h(admin), json=one)
    assert r.status_code == 200, r.text
    assert r.json()["visits_created"] == 5  # A 月火水 3 + B 月水 2


@pytest.mark.asyncio
async def test_ranged_generate_keeps_inactive_patient_auto_visit_outside_range(client, db) -> None:
    """非稼働の患者の auto 行も、範囲の外の日なら週生成の掃除で消さない."""
    admin = await _admin(db)
    await _gen_seed(db)
    gone = Patient(code="GN-X", name="テスト患者GN-X", status="inactive", special_week_active=[])
    db.add(gone)
    await db.commit()
    out_auto = await _visit(db, gone, on=_d(0), source="auto")
    in_auto = await _visit(db, gone, on=_d(2), source="auto")
    body = {
        "iso_year": YEAR,
        "iso_week": WEEK,
        "date_from": _d(1).isoformat(),
        "date_to": _d(5).isoformat(),
    }
    r = await client.post(GEN, headers=_h(admin), json=body)
    assert r.status_code == 409, r.text  # 範囲の中に予定がある (非稼働でも消す対象)
    # 範囲の中を空にしてから作る
    c = await client.post(
        CLEAR.format(y=YEAR, w=WEEK),
        headers=_h(admin),
        json={"dry_run": False, "date_from": body["date_from"], "date_to": body["date_to"]},
    )
    assert c.status_code == 200, c.text
    r = await client.post(GEN, headers=_h(admin), json=body)
    assert r.status_code == 200, r.text
    live_ids = {v.id for v in await _live(db)}
    assert out_auto.id in live_ids
    assert in_auto.id not in live_ids


@pytest.mark.asyncio
async def test_ranged_clear_and_generate_iso_week_53_across_year(client, db) -> None:
    """2026-W53 (2026-12-28〜2027-01-03) を 12-28〜12-31 だけ作る・空にする."""
    admin = await _admin(db)
    _office, pa, pb = await _gen_seed(db)
    y, w = 2026, 53
    mon = date.fromisocalendar(y, w, 1)
    assert mon == date(2026, 12, 28)
    jan1 = await _visit(db, pa, on=date(2027, 1, 1), source="manual_week")
    jan2 = await _visit(db, pb, on=date(2027, 1, 2), source="auto")
    rng = {"date_from": "2026-12-28", "date_to": "2026-12-31"}

    r = await client.post(GEN, headers=_h(admin), json={"iso_year": y, "iso_week": w, **rng})
    assert r.status_code == 200, r.text
    assert r.json()["visits_created"] == 6  # A 月〜木 4 + B 月・水 2
    live = await _live(db)
    jan = [v for v in live if v.visit_date >= date(2027, 1, 1)]
    assert {v.id for v in jan} == {jan1.id, jan2.id}
    dec = sorted((v.visit_date - mon).days for v in live if v.visit_date < date(2027, 1, 1))
    assert dec == [0, 0, 1, 2, 2, 3]

    c = await client.post(CLEAR.format(y=y, w=w), headers=_h(admin), json={"dry_run": False, **rng})
    assert c.status_code == 200, c.text
    assert c.json()["to_delete"] == 6
    assert {v.id for v in await _live(db)} == {jan1.id, jan2.id}


# ---------------------------------------------------------------------------
# 休みの後始末は範囲の日だけ (範囲の外の担当を変えない)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranged_expand_leave_settle_does_not_touch_outside_days(db) -> None:
    t = today_jst()
    monday = t + timedelta(days=7 - t.weekday() + 7)  # どの日も今日以降
    y, w, _ = iso_key(monday)
    nurse = Staff(code="RG-N", name="テスト看護師", role="staff", status="active")
    db.add(nurse)
    await db.flush()
    p = await _patient(db, "LV-1")
    await set_staff_override(
        db, staff_id=nurse.id, iso_year=y, iso_week=w, weekday=0, override_type="paid_off"
    )
    await db.commit()
    mon = await _visit(db, p, on=monday, source="manual_week", primary_staff_id=nurse.id)

    await Layer1Expander().expand_week(
        db,
        iso_year=y,
        iso_week=w,
        only_dates={monday + timedelta(days=i) for i in range(1, 6)},
    )
    await db.commit()
    await db.refresh(mon)
    assert mon.primary_staff_id == nurse.id  # 範囲の外の担当はそのまま

    # 比べ: 週全体の後始末なら担当なしになる (この試験が意味を持つことの確かめ)
    await settle_leave_unassign(db, iso_week=(y, w), notify=False)
    await db.commit()
    await db.refresh(mon)
    assert mon.primary_staff_id is None


# ---------------------------------------------------------------------------
# 検査 (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rng",
    [
        {"date_from": "2026-06-02"},
        {"date_to": "2026-06-02"},
        {"date_from": "2026-05-31", "date_to": "2026-06-02"},
        {"date_from": "2026-06-02", "date_to": "2026-06-08"},
        {"date_from": "2026-06-04", "date_to": "2026-06-02"},
    ],
)
async def test_range_validation_422(client, db, rng) -> None:
    admin = await _admin(db)
    for dry in (True, False):
        r = await client.post(
            CLEAR.format(y=YEAR, w=WEEK), headers=_h(admin), json={"dry_run": dry, **rng}
        )
        assert r.status_code == 422, r.text
        g = await client.post(
            GEN,
            headers=_h(admin),
            json={"iso_year": YEAR, "iso_week": WEEK, "dry_run": dry, **rng},
        )
        assert g.status_code == 422, g.text
        assert isinstance(g.json()["detail"], str)  # 日本語の理由
