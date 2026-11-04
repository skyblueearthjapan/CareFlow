"""やることを「最初に訪問した看護師が預かる」の試験.

正典 = ``docs/plans/patient-tasks-hold-first-visitor-design-2026-11-04.md`` §5。
終わった訪問は昨日以前、終わっていない訪問は明日以降にする (今日の時刻で揺れないため)。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import Staff, Visit
from app.models.audit_log import AuditLog
from tests.test_patient_tasks import T, _create, _h, _patient, _staff, _user, _visit


async def _hold_item(client, admin, name: str = "GAF", hold: bool = True) -> str:
    res = await client.post(
        "/api/v1/task-item-types",
        json={"name": name, "hold_by_first_visitor": hold},
        headers=_h(admin),
    )
    assert res.status_code == 201, res.text
    assert res.json()["hold_by_first_visitor"] is hold
    return res.json()["id"]


async def _visit_tasks(client, user, v) -> list[dict]:
    res = await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(user))
    assert res.status_code == 200, res.text
    return res.json()


async def _list(client, admin, query: str = "") -> list[dict]:
    res = await client.get(f"/api/v1/tasks{query}", headers=_h(admin))
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_item_without_mark_carries_over_as_before(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    await _visit(db, p, T - timedelta(days=2), primary=a)
    nxt = await _visit(db, p, T + timedelta(days=1), primary=b)
    item = await _hold_item(client, admin, "計画書", hold=False)
    await _create(client, admin, p, T - timedelta(days=3), item_ids=[item])

    (row,) = await _list(client, admin)
    assert row["held_by"] is None
    assert row["next_visit"]["visit_id"] == str(nxt.id)
    assert row["visit_round"] == 2
    (entry,) = await _visit_tasks(client, admin, nxt)
    assert (entry["carried_over"], entry["held_by"]) == (True, None)
    assert await _list(client, admin, "?status=held") == []
    assert len(await _list(client, admin, "?status=waiting2plus")) == 1


@pytest.mark.asyncio
async def test_before_first_visit_ends_it_shows_on_first_visit(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    first = await _visit(db, p, T + timedelta(days=1), primary=a)
    later = await _visit(db, p, T + timedelta(days=3), primary=b)
    item = await _hold_item(client, admin)
    await _create(client, admin, p, T, item_ids=[item])

    (row,) = await _list(client, admin)
    assert row["held_by"] is None
    assert row["next_visit"]["visit_id"] == str(first.id)
    assert row["visit_round"] == 1
    (entry,) = await _visit_tasks(client, admin, first)
    assert entry["held_by"] is None
    assert await _visit_tasks(client, admin, later) == []
    assert await _list(client, admin, "?status=held") == []


@pytest.mark.asyncio
async def test_after_first_visit_ends_primary_holds_and_no_carry_over(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    first = await _visit(db, p, T - timedelta(days=2), primary=a)
    other_past = await _visit(db, p, T - timedelta(days=1), primary=b)
    same_nurse_next = await _visit(db, p, T + timedelta(days=1), primary=a)
    other_next = await _visit(db, p, T + timedelta(days=2), primary=b)
    item = await _hold_item(client, admin)
    await _create(
        client, admin, p, T - timedelta(days=3), item_ids=[item], due=T - timedelta(days=1)
    )

    (row,) = await _list(client, admin)
    assert row["next_visit"] is None
    assert row["held_by"] == {
        "staff_id": str(a.id),
        "staff_name": "佐藤",
        "staff_active": True,
        "visit_id": str(first.id),
        "visit_date": first.visit_date.isoformat(),
        "start_time": "10:00",
    }

    # 預かった訪問にだけ出る (同じ看護師の次の訪問にもほかの人の訪問にも出ない)。
    (entry,) = await _visit_tasks(client, admin, first)
    assert entry["held_by"]["staff_id"] == str(a.id)
    assert entry["carried_over"] is False
    for v in (other_past, same_nurse_next, other_next):
        assert await _visit_tasks(client, admin, v) == []
    res = await client.get(
        f"/api/v1/tasks/counts?date={same_nurse_next.visit_date.isoformat()}", headers=_h(admin)
    )
    assert res.json()["counts"] == {}
    res = await client.get(
        f"/api/v1/tasks/counts?date={first.visit_date.isoformat()}", headers=_h(admin)
    )
    assert res.json()["counts"][str(first.id)]["open_count"] == 1

    # 一覧の状態: held に入る・waiting2plus から外れる・open と overdue には入る。
    assert len(await _list(client, admin, "?status=held")) == 1
    assert await _list(client, admin, "?status=waiting2plus") == []
    assert len(await _list(client, admin, "?status=open")) == 1
    (over,) = await _list(client, admin, "?status=overdue")
    assert over["held_by"]["staff_id"] == str(a.id)
    # 看護師で絞る: 預かる人で決まる (出す訪問の担当ではない)。
    assert len(await _list(client, admin, f"?staff_id={a.id}")) == 1
    assert await _list(client, admin, f"?staff_id={b.id}") == []
    assert len(await _list(client, admin, f"?status=held&staff_id={a.id}")) == 1


@pytest.mark.asyncio
async def test_first_visit_skips_cancelled_deleted_and_no_primary(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    await _visit(db, p, T - timedelta(days=5), primary=a, status="cancelled")
    await _visit(db, p, T - timedelta(days=5), hour=11, primary=a, deleted=True)
    await _visit(db, p, T - timedelta(days=4))  # 主担当なし
    first = await _visit(db, p, T - timedelta(days=3), primary=b)
    await _visit(db, p, T - timedelta(days=2), primary=a)
    item = await _hold_item(client, admin)
    await _create(client, admin, p, T - timedelta(days=6), item_ids=[item])

    (row,) = await _list(client, admin)
    assert row["held_by"]["staff_id"] == str(b.id)
    assert row["held_by"]["visit_id"] == str(first.id)

    # 最初の訪問を取り消すと、次の訪問が最初になる。
    first.status = "cancelled"
    await db.commit()
    (row,) = await _list(client, admin)
    assert row["held_by"]["staff_id"] == str(a.id)


@pytest.mark.asyncio
async def test_changing_primary_changes_holder_and_inactive_staff(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    first = await _visit(db, p, T - timedelta(days=2), primary=a)
    item = await _hold_item(client, admin)
    await _create(client, admin, p, T - timedelta(days=3), item_ids=[item])
    assert (await _list(client, admin))[0]["held_by"]["staff_id"] == str(a.id)

    v = await db.scalar(select(Visit).where(Visit.id == first.id))
    v.primary_staff_id = b.id
    await db.commit()
    (row,) = await _list(client, admin)
    assert row["held_by"]["staff_id"] == str(b.id)
    assert row["held_by"]["staff_active"] is True

    s = await db.scalar(select(Staff).where(Staff.id == b.id))
    s.status = "inactive"
    await db.commit()
    (row,) = await _list(client, admin)
    assert row["held_by"]["staff_id"] == str(b.id)
    assert row["held_by"]["staff_active"] is False


@pytest.mark.asyncio
async def test_holder_completes_without_visit_others_403_admin_ok(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    ub = await _user(db, "ub", "staff", staff_id=b.id)
    p = await _patient(db, "P1")
    first = await _visit(db, p, T - timedelta(days=2), primary=a)
    b_next = await _visit(db, p, T + timedelta(days=1), primary=b)
    item1 = await _hold_item(client, admin, "GAF")
    item2 = await _hold_item(client, admin, "GAF2")
    body = await _create(client, admin, p, T - timedelta(days=3), item_ids=[item1, item2])
    t1, t2 = (t["id"] for t in body["tasks"])

    # ほかの職員は、自分の訪問を指しても訪問なしでも 403。
    res = await client.post(f"/api/v1/tasks/{t1}/complete", json={}, headers=_h(ub))
    assert res.status_code == 403
    assert res.json()["detail"] == "預かっている人だけが完了にできます"
    res = await client.post(
        f"/api/v1/tasks/{t1}/complete", json={"visit_id": str(b_next.id)}, headers=_h(ub)
    )
    assert res.status_code == 403
    # 預かる人でも、最初の訪問でない訪問を指すと 422。
    res = await client.post(
        f"/api/v1/tasks/{t1}/complete", json={"visit_id": str(b_next.id)}, headers=_h(ua)
    )
    assert res.status_code == 422

    # 預かる人は訪問なしで完了でき、完了の訪問は最初の訪問。
    res = await client.post(f"/api/v1/tasks/{t1}/complete", json={}, headers=_h(ua))
    assert res.status_code == 200, res.text
    done = res.json()
    assert done["status"] == "done"
    assert done["done_visit_id"] == str(first.id)
    assert done["held_by"] is None
    assert done["can_reopen"] is True

    # 管理者もできる (訪問を省くと最初の訪問)。
    res = await client.post(f"/api/v1/tasks/{t2}/complete", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["done_visit_id"] == str(first.id)

    # 完了は最初の訪問に出る・ほかには出ない。
    assert {r["id"] for r in await _visit_tasks(client, admin, first)} == {t1, t2}
    assert await _visit_tasks(client, admin, b_next) == []

    # 戻すと預かり中に戻る。
    res = await client.post(f"/api/v1/tasks/{t1}/reopen", headers=_h(ua))
    assert res.status_code == 200, res.text
    assert res.json()["held_by"]["staff_id"] == str(a.id)


@pytest.mark.asyncio
async def test_two_staff_visit_only_primary_holds(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    ub = await _user(db, "ub", "staff", staff_id=b.id)
    p = await _patient(db, "P1")
    first = await _visit(db, p, T - timedelta(days=2), primary=a, secondary=b)
    item = await _hold_item(client, admin)
    body = await _create(client, admin, p, T - timedelta(days=3), item_ids=[item])
    tid = body["tasks"][0]["id"]

    assert (await client.get("/api/v1/tasks/held", headers=_h(ub))).json() == []
    assert [r["id"] for r in (await client.get("/api/v1/tasks/held", headers=_h(ua))).json()] == [
        tid
    ]
    res = await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(ub))
    assert res.status_code == 403
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(first.id)}, headers=_h(ub)
    )
    assert res.status_code == 403
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(first.id)}, headers=_h(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["done_visit_id"] == str(first.id)


@pytest.mark.asyncio
async def test_removing_mark_restores_carry_over(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    await _visit(db, p, T - timedelta(days=2), primary=a)
    nxt = await _visit(db, p, T + timedelta(days=1), primary=b)
    item = await _hold_item(client, admin)
    await _create(client, admin, p, T - timedelta(days=3), item_ids=[item])
    assert (await _list(client, admin))[0]["held_by"] is not None
    assert await _visit_tasks(client, admin, nxt) == []

    res = await client.patch(
        f"/api/v1/task-item-types/{item}", json={"hold_by_first_visitor": False}, headers=_h(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["hold_by_first_visitor"] is False
    (row,) = await _list(client, admin)
    assert row["held_by"] is None
    assert row["next_visit"]["visit_id"] == str(nxt.id)
    (entry,) = await _visit_tasks(client, admin, nxt)
    assert entry["carried_over"] is True
    assert len(await _list(client, admin, "?status=waiting2plus")) == 1


@pytest.mark.asyncio
async def test_get_held_mine_sorted_by_first_visit(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    p1 = await _patient(db, "P1", "青木")
    p2 = await _patient(db, "P2", "井上")
    p3 = await _patient(db, "P3", "上田")
    await _visit(db, p1, T - timedelta(days=1), primary=a)
    await _visit(db, p2, T - timedelta(days=3), primary=a)
    await _visit(db, p3, T - timedelta(days=2), primary=b)
    item = await _hold_item(client, admin)
    plain = await _hold_item(client, admin, "計画書", hold=False)
    t1 = (await _create(client, admin, p1, T - timedelta(days=4), item_ids=[item]))["tasks"]
    t2 = (await _create(client, admin, p2, T - timedelta(days=4), item_ids=[item, plain]))["tasks"]
    await _create(client, admin, p3, T - timedelta(days=4), item_ids=[item])
    await _create(client, admin, p1, T - timedelta(days=4), memo="伝言だけ")

    res = await client.get("/api/v1/tasks/held", headers=_h(ua))
    assert res.status_code == 200, res.text
    rows = res.json()
    # 印のある分だけ・自分が預かる分だけ・最初の訪問の日付順。
    assert [r["id"] for r in rows] == [t2[0]["id"], t1[0]["id"]]
    assert rows[0]["held_by"]["staff_name"] == "佐藤"
    assert rows[0]["patient_name"] == "井上"
    assert rows[0]["can_reopen"] is False

    # 職員の登録がない人は空。
    assert (await client.get("/api/v1/tasks/held", headers=_h(admin))).json() == []


@pytest.mark.asyncio
async def test_item_type_hold_flag_create_update_audited(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    res = await client.post("/api/v1/task-item-types", json={"name": "計画書"}, headers=_h(admin))
    assert res.status_code == 201, res.text
    plain = res.json()
    assert plain["hold_by_first_visitor"] is False
    gaf = await _hold_item(client, admin)

    res = await client.patch(
        f"/api/v1/task-item-types/{plain['id']}",
        json={"hold_by_first_visitor": True},
        headers=_h(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["hold_by_first_visitor"] is True
    rows = (await client.get("/api/v1/task-item-types", headers=_h(admin))).json()
    assert {r["id"]: r["hold_by_first_visitor"] for r in rows} == {plain["id"]: True, gaf: True}

    logs = (
        await db.scalars(
            select(AuditLog).where(AuditLog.target_table == "task_item_types").order_by(AuditLog.id)
        )
    ).all()
    assert [(log.action, log.target_id) for log in logs] == [
        ("task_item_type_create", plain["id"]),
        ("task_item_type_create", gaf),
        ("task_item_type_update", plain["id"]),
    ]
    assert logs[1].after["hold_by_first_visitor"] is True
    assert logs[2].before == {"hold_by_first_visitor": False}
    assert logs[2].after == {"hold_by_first_visitor": True}

    # 何も変わらない変更は残さない。
    await client.patch(
        f"/api/v1/task-item-types/{gaf}", json={"hold_by_first_visitor": True}, headers=_h(admin)
    )
    n = len(
        (await db.scalars(select(AuditLog).where(AuditLog.target_table == "task_item_types"))).all()
    )
    assert n == 3
