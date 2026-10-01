"""週のコピー — レビュー指摘 (H-1 / M-1〜M-6 / L-2 / L-7 / ロック / 件数ズレ) の検証.

正典 = docs/plans/copy-week-design-2026-09-30.md §8。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import Course
from app.models.inbound_snapshot import InboundSnapshot
from app.models.patient_ng_staff import PatientNgStaff
from app.models.visit_staff_assignment import VisitStaffAssignment
from app.services.kaipoke.inbound_snapshot import restore_snapshot
from tests.test_copy_week import URL, World, _bearer, _body, _live, _user, _weeks


async def _vsa(db, visit_id) -> list:
    rows = await db.scalars(
        select(VisitStaffAssignment.staff_id).where(VisitStaffAssignment.visit_id == visit_id)
    )
    return list(rows.all())


async def _staffed_target(db, w: World, weekday: int, staff, *, other_start: time):
    """写す先の週に担当の決まったコース + そこに残す訪問 (取込) を 1 件置く."""
    course = await w.course(w.target, weekday, staff=staff)
    other = await w.patient(f"Q{uuid.uuid4().hex[:4]}")
    await w.visit(
        other, w.target, weekday, start=other_start, course=course, staff=staff, source="import"
    )
    return course


# ---------------------------------------------------------------------------
# H-1: 担当の決まったコースへ入れる前の検査
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotation_same_staff_does_not_join_course(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0 = w.staff[0]
    p = await w.patient("ROT", pfv=(0,))
    scourse = await w.course(source, 0, staff=s0)
    await w.visit(p, source, 0, course=scourse, staff=s0)
    await _staffed_target(db, w, 0, s0, other_start=time(14, 0))
    await db.commit()

    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert len(pv["needs_manual_staff"]) == 1
    assert "前の週と同じ担当（職員0）" in pv["needs_manual_staff"][0]["reason"]

    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    copied = next(v for v in await _live(db, target) if v.patient_id == p.id)
    assert copied.course_id is None
    assert copied.primary_staff_id is None
    assert await _vsa(db, copied.id) == []
    assert len(res.json()["needs_manual_staff"]) == 1


@pytest.mark.asyncio
async def test_ng_staff_does_not_join_course(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0, s1, _ = w.staff
    p = await w.patient("NG", pfv=(0,))
    scourse = await w.course(source, 0, staff=s0)
    await w.visit(p, source, 0, course=scourse, staff=s0)
    await _staffed_target(db, w, 0, s1, other_start=time(14, 0))
    db.add(PatientNgStaff(patient_id=p.id, staff_id=s1.id, note="相性"))
    await db.commit()

    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert len(pv["needs_manual_staff"]) == 1
    assert "NG スタッフ" in pv["needs_manual_staff"][0]["reason"]


@pytest.mark.asyncio
async def test_time_overlap_blocks_and_clean_visit_joins_with_three_places(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s0, s1, _ = w.staff
    clash = await w.patient("OV1", pfv=(0,))
    fine = await w.patient("OV2", pfv=(0,))
    scourse = await w.course(source, 0, staff=s0)
    await w.visit(clash, source, 0, start=time(9, 0), course=scourse, staff=s0)
    await w.visit(fine, source, 0, start=time(11, 0), course=scourse, staff=s0)
    tcourse = await _staffed_target(db, w, 0, s1, other_start=time(9, 30))
    await db.commit()

    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    manual = res.json()["needs_manual_staff"]
    assert [m["patient_name"] for m in manual] == ["患者OV1"]
    assert "時間が重なる" in manual[0]["reason"]
    live = {v.patient_id: v for v in await _live(db, target)}
    assert live[clash.id].course_id is None
    joined = live[fine.id]
    assert joined.course_id == tcourse.id
    assert joined.primary_staff_id == s1.id
    assert await _vsa(db, joined.id) == [s1.id]
    assert (await db.get(Course, tcourse.id, populate_existing=True)).assigned_staff_id == s1.id


# ---------------------------------------------------------------------------
# M-1 / M-2 / M-3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_clears_every_course_without_kept_visits(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("CL", pfv=(0,))
    await w.visit(p, source, 0)
    ghost = await w.course(target, 3, staff=w.staff[2])  # 置き換えに触れない担当つきの空コース
    await db.commit()
    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    assert res.status_code == 200, res.text
    assert res.json()["courses_cleared"] == 1
    assert (await db.get(Course, ghost.id, populate_existing=True)).assigned_staff_id is None


@pytest.mark.asyncio
async def test_fill_from_fixed_goes_through_same_checks(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    s1 = w.staff[1]
    p = await w.patient("FL1", pfv=(0,))
    await w.visit(p, source, 0, start=time(15, 0))
    fresh = await w.patient("FL2", pfv=(2,))  # 固定訪問 水 9:00 (コース A 指定)
    await _staffed_target(db, w, 2, s1, other_start=time(9, 30))  # 担当 s1 が 9:30 に別訪問
    await db.commit()

    body = _body(source, target, fill_from_fixed=True, assign_staff=False, confirm=True)
    res = await client.post(URL, headers=_bearer(admin), json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["filled"] == 1
    manual = out["needs_manual_staff"]
    assert [(m["origin"], m["patient_name"]) for m in manual] == [("fill", "患者FL2")]
    filled = next(v for v in await _live(db, target) if v.patient_id == fresh.id)
    assert filled.course_id is None and filled.primary_staff_id is None
    assert await _vsa(db, filled.id) == []


@pytest.mark.asyncio
async def test_pair_with_cancelled_member_is_skipped_as_a_whole(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("PR", pfv=(0,))
    gid = uuid.uuid4()
    await w.visit(p, source, 0, visit_group_id=gid, required_staff_count=2)
    await w.visit(p, source, 0, visit_group_id=gid, required_staff_count=2, status="cancelled")
    await db.commit()
    pv = (
        await client.post(f"{URL}/preview", headers=_bearer(admin), json=_body(source, target))
    ).json()
    assert pv["copy_count"] == 0
    assert pv["skipped"]["cancelled"] == 1
    assert pv["skipped"]["pair_partner"] == 1


# ---------------------------------------------------------------------------
# L-2: 続けて自動割当が失敗 → コピーも残らない・読める文言
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_failure_rolls_back_copy(client, db, monkeypatch) -> None:
    from app.api.v1 import copy_week as api

    async def boom(_payload, session):
        await session.rollback()
        raise HTTPException(status_code=422, detail={"message": "割当の条件が足りません"})

    monkeypatch.setattr(api, "_assign_staff_only_impl", boom)
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("AF", pfv=(0,))
    await w.visit(p, source, 0)
    await db.commit()

    res = await client.post(URL, headers=_bearer(admin), json=_body(source, target, confirm=True))
    assert res.status_code == 422
    assert res.json()["detail"] == (
        "自動スタッフ割当に失敗したため、コピーも取り消しました: 割当の条件が足りません"
    )
    assert await _live(db, target) == []
    assert (await db.scalars(select(InboundSnapshot))).all() == []


# ---------------------------------------------------------------------------
# M-5: 「コピー前に戻す」を出してよいかの材料 / L-7: 旧形式の保存の復元
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_list_flags_and_restored_mark(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("SN", pfv=(0,))
    await w.visit(p, source, 0)
    await db.commit()
    res = await client.post(
        URL, headers=_bearer(admin), json=_body(source, target, assign_staff=False, confirm=True)
    )
    snap_id = res.json()["snapshot_id"]
    list_url = f"/api/v1/integrations/inbound-snapshots?weekStart={target.isoformat()}"
    body = (await client.get(list_url, headers=_bearer(admin))).json()
    assert body["hasCheckins"] is False
    assert body["snapshots"][0]["kind"] == "copy_week"
    assert body["snapshots"][0]["copyMode"] == "replace"
    assert body["snapshots"][0]["restoredAt"] is None

    r = await client.post(
        f"/api/v1/integrations/inbound-snapshots/{snap_id}/restore", headers=_bearer(admin)
    )
    assert r.status_code == 200, r.text
    body = (await client.get(list_url, headers=_bearer(admin))).json()
    assert body["snapshots"][0]["restoredAt"] is not None


@pytest.mark.asyncio
async def test_restore_old_format_snapshot_without_new_fields(db) -> None:
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("OLD")
    old_payload = {
        "visits": [
            {
                "patient_id": str(p.id),
                "primary_staff_id": None,
                "secondary_staff_id": None,
                "mentor_staff_id": None,
                "visit_date": target.isoformat(),
                "start_time": "09:00:00",
                "end_time": "10:00:00",
                "type": "regular",
                "status": "planned",
                "source": "auto",
                "week_pinned": False,
                "note": None,
                "kaipoke_id": None,
                "course_id": None,
                "required_staff_count": 1,
                "visit_group_id": None,
                "manual_staff_override": False,
            }
        ],
        "assignments": [],
        "accompaniments": [],
        "courses": [],
    }
    snap = InboundSnapshot(week_start=target, kind="smart", payload=old_payload, visits_count=1)
    db.add(snap)
    await db.flush()
    result = await restore_snapshot(db, snap, now=datetime.now(UTC))
    await db.commit()
    assert result.restored == 1
    restored = (await _live(db, target))[0]
    assert restored.kaipoke_service_override is None
    assert restored.is_unplanned is False
    assert "restored_at" in snap.payload


# ---------------------------------------------------------------------------
# ロック / 件数ズレ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_week_only_shares_week_lock(client, db) -> None:
    from app.api.v1.schedule import _get_assign_staff_only_lock

    admin = await _user(db)
    _, target = _weeks()
    iso = target.isocalendar()
    async with _get_assign_staff_only_lock(iso.year, iso.week):
        res = await client.post(
            "/api/v1/schedule/generate-week-only",
            headers=_bearer(admin),
            json={"iso_year": iso.year, "iso_week": iso.week},
        )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_result_flags_difference_from_preview(client, db) -> None:
    admin = await _user(db)
    source, target = _weeks()
    w = await World(db, source, target).setup()
    p = await w.patient("DF", pfv=(0,))
    await w.visit(p, source, 0)
    await db.commit()
    stale = {"copy_count": 5, "fill_count": 0, "replace_count": 0, "needs_manual_count": 0}
    res = await client.post(
        URL,
        headers=_bearer(admin),
        json=_body(source, target, assign_staff=False, confirm=True, expected_counts=stale),
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["differs_from_preview"] is True
    assert out["expected_counts"]["copy_count"] == 5
    assert out["actual_counts"]["copy_count"] == 1
