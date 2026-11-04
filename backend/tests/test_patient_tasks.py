"""事務所からのやること (patient-tasks-design-2026-11-01) の試験.

1. 出す訪問の決め方: 日付以降の最初・取消/削除は飛ばす・訪問が無い・持ち越し。
2. 訪問ごとの「出るやること」と件数 API。
3. 権限: 職員は他人の訪問のやることを読めない・完了できない・他人の完了を戻せない。
4. 完了・戻す・取消、2 名訪問 (片方の完了で完了)、同行者の完了。
5. 通知 (出す訪問の担当のアカウントへ・1 回の登録で 1 人 1 通)。
6. 項目の CRUD (管理者だけ変更・使わないは職員に見えない・作成に使えない)。
7. migration 0103 の chain と SQLite の upgrade/downgrade。
8. 期日と何回目の訪問待ち (patient-tasks-due-design-2026-11-04 §4): 期日を過ぎたは期日で
   決まる・何回目は終わった訪問の数 + 1・訪問の詳細はその訪問から見た回数・期日の変更。
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, select

from app.core.security import create_access_token, hash_password
from app.models import Accompaniment, Notification, Patient, PatientTask, Staff, User, Visit
from app.models.audit_log import AuditLog
from app.services.patient_tasks import JST, NOTIFY_TASK_ASSIGNED, jst_today, visit_rounds_now

T = jst_today()


async def _user(db, name: str, role: str, staff_id=None) -> User:
    u = User(
        email=f"{name}@example.com",
        password_hash=hash_password("x"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _h(u: User) -> dict[str, str]:
    token = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _staff(db, name: str) -> Staff:
    s = Staff(name=name, role="staff", status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _patient(db, code: str, name: str = "患者") -> Patient:
    p = Patient(code=code, name=name)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _visit(
    db,
    patient: Patient,
    d,
    *,
    hour: int = 10,
    primary: Staff | None = None,
    secondary: Staff | None = None,
    status: str = "planned",
    deleted: bool = False,
) -> Visit:
    v = Visit(
        patient_id=patient.id,
        visit_date=d,
        start_time=time(hour, 0),
        end_time=time(hour, 30),
        type="visit",
        status=status,
        primary_staff_id=primary.id if primary else None,
        secondary_staff_id=secondary.id if secondary else None,
        required_staff_count=2 if secondary else 1,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _item(client, admin, name: str) -> str:
    res = await client.post("/api/v1/task-item-types", json={"name": name}, headers=_h(admin))
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _create(client, admin, patient, start, item_ids=(), memo=None, due=None):
    res = await client.post(
        "/api/v1/tasks",
        json={
            "patient_id": str(patient.id),
            "start_date": start.isoformat(),
            "item_type_ids": list(item_ids),
            "memo": memo,
            "due_date": due.isoformat() if due else None,
        },
        headers=_h(admin),
    )
    assert res.status_code == 201, res.text
    return res.json()


def _uuid(body):
    from uuid import UUID

    return UUID(body["tasks"][0]["id"])


# ---------------------------------------------------------------------------
# 出す訪問の決め方
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_visit_is_first_on_or_after_start_skipping_cancelled(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    p = await _patient(db, "P1")
    await _visit(db, p, T + timedelta(days=1), primary=a)  # 日付より前
    await _visit(db, p, T + timedelta(days=3), primary=a, status="cancelled")
    await _visit(db, p, T + timedelta(days=3), hour=11, primary=a, deleted=True)
    target = await _visit(db, p, T + timedelta(days=4), primary=b)
    await _visit(db, p, T + timedelta(days=6), primary=a)

    body = await _create(client, admin, p, T + timedelta(days=2), memo="資料を渡す")
    assert body["next_visit"]["visit_id"] == str(target.id)
    assert body["next_visit"]["staff"][0]["name"] == "鈴木"
    assert body["tasks"][0]["is_message"] is True
    assert body["tasks"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_no_visit_then_visit_added_later(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    p = await _patient(db, "P1")
    body = await _create(client, admin, p, T, memo="伝言")
    assert body["next_visit"] is None
    assert body["notified_user_count"] == 0

    s = await _staff(db, "佐藤")
    v = await _visit(db, p, T + timedelta(days=2), primary=s)
    res = await client.get("/api/v1/tasks", headers=_h(admin))
    assert res.status_code == 200, res.text
    (row,) = res.json()
    assert row["next_visit"]["visit_id"] == str(v.id)


@pytest.mark.asyncio
async def test_carries_over_to_today_or_later_without_overdue(client, db) -> None:
    """日付が過ぎた未完了は今日以降の訪問に持ち越す。期日がなければ期日を過ぎたにしない."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    past = await _visit(db, p, T - timedelta(days=3), primary=s)
    # 明日の訪問 (今日の遅い時刻にすると、夜に流したとき終わった扱いになって揺れる)。
    today_v = await _visit(db, p, T + timedelta(days=1), primary=s)
    await _create(client, admin, p, T - timedelta(days=5), memo="持ち越し")

    (row,) = (await client.get("/api/v1/tasks", headers=_h(admin))).json()
    assert row["overdue"] is False
    assert row["due_date"] is None
    assert row["visit_round"] == 2  # 3 日前の訪問が終わった
    assert row["next_visit"]["visit_id"] == str(today_v.id)
    assert (await client.get("/api/v1/tasks?status=overdue", headers=_h(admin))).json() == []

    # 過去の訪問 (日付以降の最初) では持ち越しでない・今日の訪問では持ち越し。
    r_past = (await client.get(f"/api/v1/visits/{past.id}/tasks", headers=_h(admin))).json()
    r_today = (await client.get(f"/api/v1/visits/{today_v.id}/tasks", headers=_h(admin))).json()
    assert (r_past[0]["carried_over"], r_past[0]["visit_round"]) == (False, 1)
    assert (r_today[0]["carried_over"], r_today[0]["visit_round"]) == (True, 2)


@pytest.mark.asyncio
async def test_overdue_only_by_due_date(client, db) -> None:
    """期日を過ぎた = 未完了で期日が今日より前。期日の当日は過ぎていない・期日なしは出ない."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    await _visit(db, p, T - timedelta(days=1), primary=s)  # 終わった訪問があっても
    no_due = await _create(client, admin, p, T - timedelta(days=3), memo="期日なし")
    due_today = await _create(client, admin, p, T - timedelta(days=3), memo="今日まで", due=T)
    due_past = await _create(
        client, admin, p, T - timedelta(days=3), memo="昨日まで", due=T - timedelta(days=1)
    )
    assert no_due["tasks"][0]["overdue"] is False
    assert due_today["tasks"][0]["overdue"] is False
    assert due_today["tasks"][0]["due_date"] == T.isoformat()
    assert due_past["tasks"][0]["overdue"] is True

    rows = {r["memo"]: r for r in (await client.get("/api/v1/tasks", headers=_h(admin))).json()}
    assert {m: r["overdue"] for m, r in rows.items()} == {
        "期日なし": False,
        "今日まで": False,
        "昨日まで": True,
    }
    over = (await client.get("/api/v1/tasks?status=overdue", headers=_h(admin))).json()
    assert [r["memo"] for r in over] == ["昨日まで"]


@pytest.mark.asyncio
async def test_overdue_flag_on_visit_tasks_reopen_and_done_or_cancelled(client, db) -> None:
    """訪問の詳細・戻す (1 件の読み出し) でも同じ判定。完了・取消は期日を過ぎたにしない."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    past = await _visit(db, p, T - timedelta(days=1), primary=s)
    yesterday = T - timedelta(days=1)
    body = await _create(client, admin, p, T - timedelta(days=3), memo="過ぎた", due=yesterday)
    tid = body["tasks"][0]["id"]
    assert body["tasks"][0]["overdue"] is True

    (row,) = (await client.get(f"/api/v1/visits/{past.id}/tasks", headers=_h(admin))).json()
    assert row["overdue"] is True

    res = await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["overdue"] is False
    res = await client.post(f"/api/v1/tasks/{tid}/reopen", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["overdue"] is True
    assert res.json()["visit_round"] == 2

    other = await _create(client, admin, p, T - timedelta(days=3), memo="取消", due=yesterday)
    res = await client.post(f"/api/v1/tasks/{other['tasks'][0]['id']}/cancel", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["overdue"] is False
    over = (await client.get("/api/v1/tasks?status=overdue", headers=_h(admin))).json()
    assert [r["id"] for r in over] == [tid]


@pytest.mark.asyncio
async def test_same_day_second_visit_carries_over_round_two_not_overdue(client, db) -> None:
    """同じ日の 2 回: 午後の訪問の詳細では 2 回目 (持ち越し)。期日がなければ過ぎたにしない."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    morning = await _visit(db, p, T, hour=9, primary=s)
    later = await _visit(db, p, T, hour=14, primary=s)
    await _create(client, admin, p, T, memo="朝の分")

    (row,) = (await client.get(f"/api/v1/visits/{later.id}/tasks", headers=_h(admin))).json()
    assert row["carried_over"] is True
    assert row["visit_round"] == 2
    assert row["overdue"] is False
    (row,) = (await client.get(f"/api/v1/visits/{morning.id}/tasks", headers=_h(admin))).json()
    assert (row["carried_over"], row["visit_round"]) == (False, 1)


@pytest.mark.asyncio
async def test_visit_round_now_counts_each_ended_visit_same_day(client, db) -> None:
    """いまの時刻から見た回数: 朝の訪問の終わり (9:30) を過ぎたら 2、午後の後で 3."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    await _visit(db, p, T, hour=9, primary=s)
    await _visit(db, p, T, hour=14, primary=s)
    body = await _create(client, admin, p, T, memo="朝の分")
    task = await db.scalar(select(PatientTask).where(PatientTask.id == _uuid(body)))

    def at(h: int, m: int) -> datetime:
        return datetime.combine(T, time(h, m), tzinfo=JST).astimezone(UTC)

    assert (await visit_rounds_now(db, [task], at(9, 29)))[task.id] == 1
    assert (await visit_rounds_now(db, [task], at(9, 31)))[task.id] == 2
    assert (await visit_rounds_now(db, [task], at(14, 31)))[task.id] == 3


@pytest.mark.asyncio
async def test_visit_round_skips_cancelled_deleted_and_before_start(client, db) -> None:
    """何回目: 日付より前・取消・削除の訪問は数えない。終わった訪問ごとに 1 つ増える."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    q = await _patient(db, "P2")
    await _visit(db, p, T - timedelta(days=6), primary=s)  # 日付より前
    first = await _visit(db, p, T - timedelta(days=4), primary=s)
    await _visit(db, p, T - timedelta(days=3), primary=s, status="cancelled")
    await _visit(db, p, T - timedelta(days=2), primary=s, deleted=True)
    nxt = await _visit(db, p, T + timedelta(days=1), primary=s)
    await _visit(db, q, T + timedelta(days=1), primary=s)
    await _create(client, admin, p, T - timedelta(days=5), memo="P1")
    await _create(client, admin, q, T - timedelta(days=5), memo="P2")

    def rounds(rows):
        return {r["memo"]: r["visit_round"] for r in rows}

    rows = (await client.get("/api/v1/tasks", headers=_h(admin))).json()
    assert rounds(rows) == {"P1": 2, "P2": 1}
    w2 = (await client.get("/api/v1/tasks?status=waiting2plus", headers=_h(admin))).json()
    assert [r["memo"] for r in w2] == ["P1"]

    await _visit(db, p, T - timedelta(days=1), primary=s)
    rows = (await client.get("/api/v1/tasks", headers=_h(admin))).json()
    assert rounds(rows) == {"P1": 3, "P2": 1}
    # 訪問の詳細はその訪問から見た回数
    (r_first,) = (await client.get(f"/api/v1/visits/{first.id}/tasks", headers=_h(admin))).json()
    (r_next,) = (await client.get(f"/api/v1/visits/{nxt.id}/tasks", headers=_h(admin))).json()
    assert (r_first["visit_round"], r_next["visit_round"]) == (1, 3)

    # 完了したものは 2 回目以降の訪問待ちに出ない
    tid = next(r["id"] for r in rows if r["memo"] == "P1")
    await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(admin))
    assert (await client.get("/api/v1/tasks?status=waiting2plus", headers=_h(admin))).json() == []


@pytest.mark.asyncio
async def test_create_with_due_date_validation(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    p = await _patient(db, "P1")
    base = {"patient_id": str(p.id), "start_date": T.isoformat(), "memo": "x"}
    res = await client.post(
        "/api/v1/tasks",
        json={**base, "due_date": (T - timedelta(days=1)).isoformat()},
        headers=_h(admin),
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "期日は日付（この日から）と同じ日か、後の日にしてください"
    body = await _create(client, admin, p, T, memo="当日", due=T)
    assert body["tasks"][0]["due_date"] == T.isoformat()
    body = await _create(client, admin, p, T, memo="なし")
    assert body["tasks"][0]["due_date"] is None


@pytest.mark.asyncio
async def test_change_due_admin_only_open_only_and_audited(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    p = await _patient(db, "P1")
    body = await _create(client, admin, p, T - timedelta(days=2), memo="x")
    tid = body["tasks"][0]["id"]
    url = f"/api/v1/tasks/{tid}/due"
    yesterday = (T - timedelta(days=1)).isoformat()

    res = await client.patch(url, json={"due_date": T.isoformat()}, headers=_h(us))
    assert res.status_code == 403
    res = await client.patch(
        url, json={"due_date": (T - timedelta(days=3)).isoformat()}, headers=_h(admin)
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "期日は日付（この日から）と同じ日か、後の日にしてください"

    res = await client.patch(url, json={"due_date": yesterday}, headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["due_date"] == yesterday
    assert res.json()["overdue"] is True
    assert res.json()["can_reopen"] is False
    res = await client.patch(url, json={"due_date": None}, headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["due_date"] is None
    assert res.json()["overdue"] is False
    assert (await client.patch(url, json={}, headers=_h(admin))).status_code == 422

    logs = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action == "task_due_change", AuditLog.target_id == str(_uuid(body)))
            .order_by(AuditLog.id)
        )
    ).all()
    assert [(a.before, a.after) for a in logs] == [
        ({"due_date": None}, {"due_date": yesterday}),
        ({"due_date": yesterday}, {"due_date": None}),
    ]

    await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(admin))
    res = await client.patch(url, json={"due_date": T.isoformat()}, headers=_h(admin))
    assert res.status_code == 422
    other = await _create(client, admin, p, T, memo="取消")
    oid = other["tasks"][0]["id"]
    await client.post(f"/api/v1/tasks/{oid}/cancel", headers=_h(admin))
    res = await client.patch(
        f"/api/v1/tasks/{oid}/due", json={"due_date": T.isoformat()}, headers=_h(admin)
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_visit_tasks_not_on_visits_before_start_or_cancelled(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    before = await _visit(db, p, T, primary=s)
    cancelled = await _visit(db, p, T + timedelta(days=2), primary=s, status="cancelled")
    after = await _visit(db, p, T + timedelta(days=2), hour=13, primary=s)
    await _create(client, admin, p, T + timedelta(days=1), memo="伝言")

    for v, n in ((before, 0), (cancelled, 0), (after, 1)):
        res = await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(admin))
        assert res.status_code == 200, res.text
        assert len(res.json()) == n


# ---------------------------------------------------------------------------
# 権限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_cannot_read_or_complete_other_visits(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    mine = await _staff(db, "佐藤")
    other = await _staff(db, "鈴木")
    me = await _user(db, "me", "staff", staff_id=mine.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=other)
    body = await _create(client, admin, p, T, memo="伝言")
    tid = body["tasks"][0]["id"]

    res = await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(me))
    assert res.status_code == 404
    res = await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(me))
    assert res.status_code == 403
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(v.id)}, headers=_h(me)
    )
    assert res.status_code == 403
    # 管理者だけの API
    assert (await client.get("/api/v1/tasks", headers=_h(me))).status_code == 403
    res = await client.post(
        "/api/v1/tasks",
        json={"patient_id": str(p.id), "start_date": T.isoformat(), "memo": "x"},
        headers=_h(me),
    )
    assert res.status_code == 403
    assert (await client.post(f"/api/v1/tasks/{tid}/cancel", headers=_h(me))).status_code == 403
    # 件数も自分の訪問だけ
    res = await client.get(f"/api/v1/tasks/counts?date={T.isoformat()}", headers=_h(me))
    assert res.json()["counts"] == {}


@pytest.mark.asyncio
async def test_staff_complete_and_reopen_own_only(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    ub = await _user(db, "ub", "staff", staff_id=b.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=a, secondary=b)  # 2 名訪問
    item = await _item(client, admin, "項目A")
    body = await _create(client, admin, p, T, item_ids=[item])
    tid = body["tasks"][0]["id"]

    # 片方 (2 人目) が visit_id なしで完了 → 今日の自分の訪問が使われる
    res = await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(ub))
    assert res.status_code == 200, res.text
    done = res.json()
    assert done["status"] == "done"
    assert done["done_visit_id"] == str(v.id)
    assert done["done_by_name"] == "鈴木"
    assert done["can_reopen"] is True

    # もう片方から見ても完了・戻せない
    rows = (await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(ua))).json()
    assert rows[0]["status"] == "done"
    assert rows[0]["can_reopen"] is False
    res = await client.post(f"/api/v1/tasks/{tid}/reopen", headers=_h(ua))
    assert res.status_code == 403
    # 二重の完了は 409
    res = await client.post(f"/api/v1/tasks/{tid}/complete", json={}, headers=_h(ua))
    assert res.status_code == 409

    res = await client.post(f"/api/v1/tasks/{tid}/reopen", headers=_h(ub))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "open"
    assert res.json()["done_visit_id"] is None


@pytest.mark.asyncio
async def test_accompanying_staff_can_complete(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    c = await _staff(db, "同行")
    uc = await _user(db, "uc", "staff", staff_id=c.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=a)
    db.add(
        Accompaniment(
            accompanying_staff_id=c.id,
            target_type="visit",
            visit_id=v.id,
            source="manual",
            kind="support",
        )
    )
    await db.commit()
    body = await _create(client, admin, p, T, memo="伝言")
    assert {s["role"] for s in body["next_visit"]["staff"]} == {"primary", "accompaniment"}
    tid = body["tasks"][0]["id"]
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(v.id)}, headers=_h(uc)
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_admin_complete_reopen_cancel(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=s)
    other_p = await _patient(db, "P2")
    other_v = await _visit(db, other_p, T, primary=s)
    body = await _create(client, admin, p, T, memo="伝言")
    tid = body["tasks"][0]["id"]

    # 別の患者の訪問では完了できない
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(other_v.id)}, headers=_h(admin)
    )
    assert res.status_code == 422
    # 訪問を指さずに完了 → 完了した日 (今日) の訪問に「完了」で出る
    res = await client.post(f"/api/v1/tasks/{tid}/complete", headers=_h(admin))
    assert res.status_code == 200, res.text
    assert res.json()["done_visit_id"] is None
    rows = (await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(admin))).json()
    assert rows[0]["status"] == "done"
    done_rows = (await client.get("/api/v1/tasks?status=done", headers=_h(admin))).json()
    assert [r["id"] for r in done_rows] == [tid]
    assert (await client.get("/api/v1/tasks", headers=_h(admin))).json() == []

    assert (await client.post(f"/api/v1/tasks/{tid}/reopen", headers=_h(admin))).status_code == 200
    res = await client.post(f"/api/v1/tasks/{tid}/cancel", headers=_h(admin))
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"
    assert (await client.get("/api/v1/tasks?status=all", headers=_h(admin))).json() == []
    assert (await client.get(f"/api/v1/visits/{v.id}/tasks", headers=_h(admin))).json() == []
    res = await client.post(f"/api/v1/tasks/{tid}/complete", headers=_h(admin))
    assert res.status_code == 409
    assert (await client.post(f"/api/v1/tasks/{tid}/cancel", headers=_h(admin))).status_code == 409


@pytest.mark.asyncio
async def test_list_done_from_filters_by_done_day(client, db) -> None:
    """done_from は完了した日 (JST) で絞る。日付 (start_date) が新しくても古い完了は外れる。"""
    from datetime import UTC, datetime
    from uuid import UUID

    from app.models.patient_task import PatientTask

    admin = await _user(db, "adm", "admin")
    p = await _patient(db, "P1")
    old = (await _create(client, admin, p, T, memo="前に完了"))["tasks"][0]["id"]
    new = (await _create(client, admin, p, T, memo="今日完了"))["tasks"][0]["id"]
    for tid in (old, new):
        res = await client.post(f"/api/v1/tasks/{tid}/complete", headers=_h(admin))
        assert res.status_code == 200, res.text
    # 完了の時刻はサーバーの今に任せず、JST の日の昼に固定する (日付の境で揺れないように)。
    from datetime import time as _time
    from zoneinfo import ZoneInfo

    from app.api.v1.tasks import jst_today

    jst = ZoneInfo("Asia/Tokyo")
    today = jst_today()
    for tid, day in ((old, today - timedelta(days=40)), (new, today)):
        row = await db.get(PatientTask, UUID(tid))
        await db.refresh(row)
        row.done_at = datetime.combine(day, _time(12), tzinfo=jst).astimezone(UTC)
    await db.commit()

    since = (today - timedelta(days=30)).isoformat()
    rows = (
        await client.get(f"/api/v1/tasks?status=done&done_from={since}", headers=_h(admin))
    ).json()
    assert [r["id"] for r in rows] == [new]
    rows = (await client.get("/api/v1/tasks?status=done", headers=_h(admin))).json()
    assert {r["id"] for r in rows} == {old, new}
    # 今日の 0 時 (JST) からなら今日の完了は入る
    rows = (
        await client.get(
            f"/api/v1/tasks?status=done&done_from={today.isoformat()}", headers=_h(admin)
        )
    ).json()
    assert [r["id"] for r in rows] == [new]


@pytest.mark.asyncio
async def test_done_task_shows_only_on_done_visit(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    p = await _patient(db, "P1")
    v1 = await _visit(db, p, T, primary=s)
    v2 = await _visit(db, p, T + timedelta(days=3), primary=s)
    body = await _create(client, admin, p, T, memo="伝言")
    tid = body["tasks"][0]["id"]
    # 今日の訪問がある間は、先の訪問には出さない (日付が過ぎてから次の訪問へ持ち越す)
    assert (await client.get(f"/api/v1/visits/{v2.id}/tasks", headers=_h(us))).json() == []
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(v1.id)}, headers=_h(us)
    )
    assert res.status_code == 200, res.text
    assert len((await client.get(f"/api/v1/visits/{v1.id}/tasks", headers=_h(us))).json()) == 1
    assert (await client.get(f"/api/v1/visits/{v2.id}/tasks", headers=_h(us))).json() == []


@pytest.mark.asyncio
async def test_open_task_not_on_later_future_visits(client, db) -> None:
    """まだ日付が来ていない未完了は、日付以降の最初の訪問にだけ出る (2 件目は完了もできない)."""
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ub = await _user(db, "ub", "staff", staff_id=b.id)
    p = await _patient(db, "P1")
    first = await _visit(db, p, T + timedelta(days=2), primary=a)
    second = await _visit(db, p, T + timedelta(days=5), primary=b)
    body = await _create(client, admin, p, T + timedelta(days=1), memo="伝言")
    tid = body["tasks"][0]["id"]
    assert body["next_visit"]["visit_id"] == str(first.id)

    assert (
        len((await client.get(f"/api/v1/visits/{first.id}/tasks", headers=_h(admin))).json()) == 1
    )
    assert (await client.get(f"/api/v1/visits/{second.id}/tasks", headers=_h(ub))).json() == []
    d2 = (T + timedelta(days=5)).isoformat()
    res = await client.get(f"/api/v1/tasks/counts?date={d2}", headers=_h(ub))
    assert res.json()["counts"] == {}
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(second.id)}, headers=_h(ub)
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_staff_cannot_complete_request_on_future_visit(client, db) -> None:
    """職員は先の日の訪問でお願い (項目あり) を完了できない (422)。伝言は先の日でも読みました.

    管理者は先の日の訪問でもお願いを完了できる (mobile-future-days・PO 2026-11-01)。
    """
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T + timedelta(days=2), primary=s)
    iid = await _item(client, admin, "サイン")
    req = (await _create(client, admin, p, T + timedelta(days=1), item_ids=[iid]))["tasks"][0]
    msg = (await _create(client, admin, p, T + timedelta(days=1), memo="伝言"))["tasks"][0]

    res = await client.post(
        f"/api/v1/tasks/{req['id']}/complete", json={"visit_id": str(v.id)}, headers=_h(us)
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "先の日の訪問では完了できません"

    res = await client.post(
        f"/api/v1/tasks/{msg['id']}/complete", json={"visit_id": str(v.id)}, headers=_h(us)
    )
    assert res.status_code == 200, res.text

    res = await client.post(
        f"/api/v1/tasks/{req['id']}/complete", json={"visit_id": str(v.id)}, headers=_h(admin)
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_staff_completes_request_on_today_visit(client, db) -> None:
    """今日の訪問なら、職員もお願い (項目あり) を完了できる."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=s)
    iid = await _item(client, admin, "サイン")
    req = (await _create(client, admin, p, T, item_ids=[iid]))["tasks"][0]
    res = await client.post(
        f"/api/v1/tasks/{req['id']}/complete", json={"visit_id": str(v.id)}, headers=_h(us)
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_overdue_shows_only_on_next_future_visit(client, db) -> None:
    """日付が過ぎた未完了 (今日の訪問なし) は、明日以降の最初の訪問にだけ持ち越しで出る."""
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    p = await _patient(db, "P1")
    past = await _visit(db, p, T - timedelta(days=3), primary=s)
    nxt = await _visit(db, p, T + timedelta(days=1), primary=s)
    later = await _visit(db, p, T + timedelta(days=3), primary=s)
    await _create(client, admin, p, T - timedelta(days=5), memo="持ち越し")

    r_past = (await client.get(f"/api/v1/visits/{past.id}/tasks", headers=_h(admin))).json()
    r_next = (await client.get(f"/api/v1/visits/{nxt.id}/tasks", headers=_h(admin))).json()
    r_later = (await client.get(f"/api/v1/visits/{later.id}/tasks", headers=_h(admin))).json()
    assert r_past[0]["carried_over"] is False
    assert r_next[0]["carried_over"] is True
    assert r_later == []


@pytest.mark.asyncio
async def test_mentor_is_assigned_staff(client, db) -> None:
    """指導役 (mentor) も担当: 出す訪問の担当・通知・一覧の看護師の絞り込みにそろう."""
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    m = await _staff(db, "指導")
    um = await _user(db, "um", "staff", staff_id=m.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=a)
    v.mentor_staff_id = m.id
    await db.commit()

    body = await _create(client, admin, p, T, memo="伝言")
    roles = {s["staff_id"]: s["role"] for s in body["next_visit"]["staff"]}
    assert roles[str(m.id)] == "accompaniment"
    assert body["notified_user_count"] == 1
    notes = (
        await db.scalars(select(Notification).where(Notification.type == NOTIFY_TASK_ASSIGNED))
    ).all()
    assert {n.user_id for n in notes} == {um.id}
    rows = (await client.get(f"/api/v1/tasks?staff_id={m.id}", headers=_h(admin))).json()
    assert len(rows) == 1
    res = await client.post(
        f"/api/v1/tasks/{body['tasks'][0]['id']}/complete", json={}, headers=_h(um)
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_complete_race_second_is_409(client, db, monkeypatch) -> None:
    """読んだ時点で未完了に見えても、書き換えは未完了の行だけ (後の 1 回は 409・上書きしない)."""
    import app.api.v1.tasks as tasks_api

    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    p = await _patient(db, "P1")
    v = await _visit(db, p, T, primary=s)
    body = await _create(client, admin, p, T, memo="伝言")
    tid = body["tasks"][0]["id"]
    res = await client.post(
        f"/api/v1/tasks/{tid}/complete", json={"visit_id": str(v.id)}, headers=_h(us)
    )
    assert res.status_code == 200, res.text

    # 先の確認をすり抜けた (同時に押された) ときの形を作る
    monkeypatch.setattr(tasks_api, "task_status", lambda _t: "open")
    res = await client.post(f"/api/v1/tasks/{tid}/complete", headers=_h(admin))
    assert res.status_code == 409
    monkeypatch.undo()
    rows = (await client.get("/api/v1/tasks?status=done", headers=_h(admin))).json()
    assert rows[0]["done_by_name"] == "佐藤"
    assert rows[0]["done_visit_id"] == str(v.id)


# ---------------------------------------------------------------------------
# 件数・一覧の絞り込み
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counts_and_staff_filter(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    p1 = await _patient(db, "P1")
    p2 = await _patient(db, "P2")
    p3 = await _patient(db, "P3")
    v1 = await _visit(db, p1, T, primary=a)
    v2 = await _visit(db, p2, T, primary=b)
    await _visit(db, p3, T, primary=a)  # やることなし
    await _visit(db, p1, T - timedelta(days=2), primary=a)
    item1 = await _item(client, admin, "項目1")
    item2 = await _item(client, admin, "項目2")
    await _create(client, admin, p1, T - timedelta(days=2), item_ids=[item1, item2], memo="x")
    body = await _create(client, admin, p2, T, memo="伝言")
    await client.post(f"/api/v1/tasks/{body['tasks'][0]['id']}/complete", headers=_h(admin))

    res = await client.get(f"/api/v1/tasks/counts?date={T.isoformat()}", headers=_h(admin))
    assert res.status_code == 200, res.text
    counts = res.json()["counts"]
    assert set(counts) == {str(v1.id), str(v2.id)}
    assert counts[str(v1.id)] == {
        "open_count": 2,
        "total_count": 2,
        "has_carried_over": True,
        "has_overdue": False,
        "all_done": False,
    }
    assert counts[str(v2.id)]["all_done"] is True
    assert counts[str(v2.id)]["has_overdue"] is False

    # 期日を過ぎたものが 1 件あれば has_overdue
    tid = next(r["id"] for r in (await client.get("/api/v1/tasks", headers=_h(admin))).json())
    res = await client.patch(
        f"/api/v1/tasks/{tid}/due",
        json={"due_date": (T - timedelta(days=1)).isoformat()},
        headers=_h(admin),
    )
    assert res.status_code == 200, res.text
    res = await client.get(f"/api/v1/tasks/counts?date={T.isoformat()}", headers=_h(admin))
    assert res.json()["counts"][str(v1.id)]["has_overdue"] is True

    res = await client.get(f"/api/v1/tasks/counts?date={T.isoformat()}", headers=_h(ua))
    assert set(res.json()["counts"]) == {str(v1.id)}

    rows = (await client.get(f"/api/v1/tasks?staff_id={b.id}", headers=_h(admin))).json()
    assert rows == []
    rows = (await client.get(f"/api/v1/tasks?staff_id={a.id}", headers=_h(admin))).json()
    assert len(rows) == 2
    # 伝言は最初の 1 件に付く
    assert [r["memo"] for r in rows].count("x") == 1

    # 完了の一覧: 件数は DB で絞る・看護師は完了した人で絞る
    rows = (await client.get("/api/v1/tasks?status=all&limit=1", headers=_h(admin))).json()
    assert len(rows) == 1
    rows = (
        await client.get(f"/api/v1/tasks?status=done&staff_id={b.id}", headers=_h(admin))
    ).json()
    assert rows == []


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_assigned_staff_once_per_create(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    a = await _staff(db, "佐藤")
    b = await _staff(db, "鈴木")
    no_account = await _staff(db, "アカウントなし")
    ua = await _user(db, "ua", "staff", staff_id=a.id)
    ub = await _user(db, "ub", "staff", staff_id=b.id)
    p = await _patient(db, "P1", "青木")
    await _visit(db, p, T + timedelta(days=1), primary=a, secondary=b)
    await _visit(db, p, T + timedelta(days=2), primary=no_account)
    i1 = await _item(client, admin, "計画書")
    i2 = await _item(client, admin, "集金")

    body = await _create(client, admin, p, T, item_ids=[i1, i2])
    assert len(body["tasks"]) == 2
    assert body["notified_user_count"] == 2
    rows = (
        await db.scalars(select(Notification).where(Notification.type == NOTIFY_TASK_ASSIGNED))
    ).all()
    assert {n.user_id for n in rows} == {ua.id, ub.id}
    assert all("青木 様" in (n.body or "") and "計画書・集金" in (n.body or "") for n in rows)
    assert rows[0].data["task_ids"] == [t["id"] for t in body["tasks"]]


# ---------------------------------------------------------------------------
# 入力の検査と項目
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_validation(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    p = await _patient(db, "P1")
    base = {"patient_id": str(p.id), "start_date": T.isoformat()}
    res = await client.post("/api/v1/tasks", json={**base, "memo": "   "}, headers=_h(admin))
    assert res.status_code == 422
    item = await _item(client, admin, "古い項目")
    await client.patch(
        f"/api/v1/task-item-types/{item}", json={"is_active": False}, headers=_h(admin)
    )
    res = await client.post(
        "/api/v1/tasks", json={**base, "item_type_ids": [item]}, headers=_h(admin)
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_item_types_crud(client, db) -> None:
    admin = await _user(db, "adm", "admin")
    s = await _staff(db, "佐藤")
    us = await _user(db, "us", "staff", staff_id=s.id)
    first = await _item(client, admin, "項目1")
    second = await _item(client, admin, "項目2")
    res = await client.post("/api/v1/task-item-types", json={"name": "項目1"}, headers=_h(admin))
    assert res.status_code == 409
    res = await client.post("/api/v1/task-item-types", json={"name": "x"}, headers=_h(us))
    assert res.status_code == 403
    res = await client.patch(f"/api/v1/task-item-types/{first}", json={"name": "x"}, headers=_h(us))
    assert res.status_code == 403

    res = await client.patch(
        f"/api/v1/task-item-types/{second}",
        json={"sort_order": -1, "name": "  名前を変えた  "},
        headers=_h(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "名前を変えた"
    rows = (await client.get("/api/v1/task-item-types", headers=_h(us))).json()
    assert [r["id"] for r in rows] == [second, first]

    await client.patch(
        f"/api/v1/task-item-types/{first}", json={"is_active": False}, headers=_h(admin)
    )
    rows = (await client.get("/api/v1/task-item-types", headers=_h(us))).json()
    assert [r["id"] for r in rows] == [second]
    # 職員は include_inactive を付けても使わない項目は見えない
    rows = (
        await client.get("/api/v1/task-item-types?include_inactive=true", headers=_h(us))
    ).json()
    assert [r["id"] for r in rows] == [second]
    rows = (
        await client.get("/api/v1/task-item-types?include_inactive=true", headers=_h(admin))
    ).json()
    assert {r["id"] for r in rows} == {first, second}


# ---------------------------------------------------------------------------
# migration 0103
# ---------------------------------------------------------------------------

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REVISION = "0103_patient_tasks"


def _run_migration(engine: sa.Engine, fn_name: str) -> None:
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0103", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0103"] = mod
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def test_migration_0103_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_REVISION)
    assert rev is not None
    assert rev.down_revision == "0102_pfv_visit_week"
    assert len(_REVISION) <= 32
    # head は 1 つ（後の migration が足されても 0103 がその祖先であること）。
    heads = list(script.get_heads())
    assert len(heads) == 1
    assert _REVISION in {r.revision for r in script.iterate_revisions(heads[0], "base")}


def test_migration_0103_upgrade_downgrade(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0103.db'}")
    _run_migration(engine, "upgrade")
    tables = set(inspect(engine).get_table_names())
    assert {"task_item_types", "patient_tasks"} <= tables
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM task_item_types")).scalar() == 0
    # 項目も伝言も空は CHECK で落ちる
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO patient_tasks (id, patient_id, start_date) "
                "VALUES ('a', 'p', '2026-11-01')"
            )
        )
    _run_migration(engine, "downgrade")
    tables = set(inspect(engine).get_table_names())
    assert not ({"task_item_types", "patient_tasks"} & tables)
