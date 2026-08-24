"""イベントひな形 (event_templates) のテスト — Phase 2.

正典 = docs/plans/staff-event-history-design.md §2 Phase 2。

検証観点:
  1. CRUD (作成 / 部分更新 / 物理削除) と sort_order の自動採番
  2. 取得スコープ: 共通は常に返る / staff_id 指定でその個人ぶんも返る /
     他人の個人ひな形は返らない / include_inactive
  3. reorder: 正常 (並び反映) / スコープ不一致・未知 ID は 422
  4. バリデーション: 片方だけ時刻 / end <= start / 空タイトル
  5. RBAC: staff ロールは読取 OK・書込 403
  6. history-suggestions: 集約 (回数・直近) / 除外 3 条件 / months 窓 / 降順
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import EventTemplate, Staff, User
from app.models.staff import StaffEvent, StaffEventDefault

BASE = "/api/v1/event-templates"


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str = "ひな形 花子") -> Staff:
    s = Staff(name=name, status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_event(
    db,
    staff: Staff,
    *,
    title: str,
    day: date,
    start: time = time(13, 0),
    end: time = time(14, 0),
    source: str = "manual",
    event_type: str = "event",
) -> StaffEvent:
    row = StaffEvent(
        staff_id=staff.id,
        event_type=event_type,
        starts_at=datetime.combine(day, start),
        ends_at=datetime.combine(day, end),
        title=title,
        source=source,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _payload(**over) -> dict:
    body = {"title": "カンファレンス", "start_time": "13:00", "end_time": "14:00"}
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# 1. CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_shared_template(client, db):
    admin = await _make_user(db, "a1@example.com", "admin")
    res = await client.post(BASE, json=_payload(note="毎月の会議"), headers=_bearer(admin))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["staff_id"] is None
    assert body["is_shared"] is True
    assert body["title"] == "カンファレンス"
    assert body["event_type"] == "event"
    assert body["start_time"] == "13:00"
    assert body["end_time"] == "14:00"
    assert body["sort_order"] == 0
    assert body["is_active"] is True

    listed = await client.get(BASE, headers=_bearer(admin))
    assert listed.status_code == 200
    assert [t["title"] for t in listed.json()] == ["カンファレンス"]


@pytest.mark.asyncio
async def test_create_without_times_is_allowed(client, db):
    admin = await _make_user(db, "a2@example.com", "admin")
    res = await client.post(
        BASE,
        json={"title": "打合せ", "start_time": None, "end_time": None},
        headers=_bearer(admin),
    )
    assert res.status_code == 201, res.text
    assert res.json()["start_time"] is None
    assert res.json()["end_time"] is None


@pytest.mark.asyncio
async def test_sort_order_auto_increments_per_scope(client, db):
    admin = await _make_user(db, "a3@example.com", "admin")
    staff = await _make_staff(db)
    h = _bearer(admin)

    first = await client.post(BASE, json=_payload(title="共通1"), headers=h)
    second = await client.post(BASE, json=_payload(title="共通2"), headers=h)
    personal = await client.post(
        BASE, json=_payload(title="個人1", staff_id=str(staff.id)), headers=h
    )

    assert first.json()["sort_order"] == 0
    assert second.json()["sort_order"] == 1
    # 個人スコープは別の採番系列 (共通の 2 に続かない)。
    assert personal.json()["sort_order"] == 0
    assert personal.json()["is_shared"] is False


@pytest.mark.asyncio
async def test_patch_partial_update(client, db):
    admin = await _make_user(db, "a4@example.com", "admin")
    h = _bearer(admin)
    created = (await client.post(BASE, json=_payload(), headers=h)).json()

    res = await client.patch(
        f"{BASE}/{created['id']}",
        json={"title": "  カンファレンス(改)  ", "event_type": "training", "blocking": True},
        headers=h,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "カンファレンス(改)"
    assert body["event_type"] == "training"
    assert body["blocking"] is True
    # 触っていない項目は保持される。
    assert body["start_time"] == "13:00"


@pytest.mark.asyncio
async def test_patch_can_clear_times_and_deactivate(client, db):
    admin = await _make_user(db, "a5@example.com", "admin")
    h = _bearer(admin)
    created = (await client.post(BASE, json=_payload(), headers=h)).json()

    res = await client.patch(
        f"{BASE}/{created['id']}",
        json={"start_time": None, "end_time": None, "is_active": False},
        headers=h,
    )
    assert res.status_code == 200, res.text
    assert res.json()["start_time"] is None
    assert res.json()["end_time"] is None
    assert res.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_is_hard_delete(client, db):
    admin = await _make_user(db, "a6@example.com", "admin")
    h = _bearer(admin)
    created = (await client.post(BASE, json=_payload(), headers=h)).json()

    res = await client.delete(f"{BASE}/{created['id']}", headers=h)
    assert res.status_code == 204
    assert (await db.scalar(select(EventTemplate))) is None
    # 二度目は 404。
    assert (await client.delete(f"{BASE}/{created['id']}", headers=h)).status_code == 404


# ---------------------------------------------------------------------------
# 2. 取得スコープ / include_inactive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_scope_shared_plus_personal(client, db):
    admin = await _make_user(db, "b1@example.com", "admin")
    h = _bearer(admin)
    mine = await _make_staff(db, "自分")
    other = await _make_staff(db, "他人")

    await client.post(BASE, json=_payload(title="共通"), headers=h)
    await client.post(BASE, json=_payload(title="自分の", staff_id=str(mine.id)), headers=h)
    await client.post(BASE, json=_payload(title="他人の", staff_id=str(other.id)), headers=h)

    # staff_id なし = 共通のみ。
    shared_only = (await client.get(BASE, headers=h)).json()
    assert [t["title"] for t in shared_only] == ["共通"]

    # staff_id あり = 共通 + その個人 (他人のは出ない)。
    scoped = (await client.get(f"{BASE}?staff_id={mine.id}", headers=h)).json()
    assert sorted(t["title"] for t in scoped) == ["共通", "自分の"]
    assert {t["title"]: t["is_shared"] for t in scoped} == {"共通": True, "自分の": False}


@pytest.mark.asyncio
async def test_list_include_inactive(client, db):
    admin = await _make_user(db, "b2@example.com", "admin")
    h = _bearer(admin)
    created = (await client.post(BASE, json=_payload(title="無効化する"), headers=h)).json()
    await client.patch(f"{BASE}/{created['id']}", json={"is_active": False}, headers=h)

    assert (await client.get(BASE, headers=h)).json() == []
    with_inactive = (await client.get(f"{BASE}?include_inactive=true", headers=h)).json()
    assert [t["title"] for t in with_inactive] == ["無効化する"]


# ---------------------------------------------------------------------------
# 3. reorder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reorder_shared_scope(client, db):
    admin = await _make_user(db, "c1@example.com", "admin")
    h = _bearer(admin)
    a = (await client.post(BASE, json=_payload(title="A"), headers=h)).json()
    b = (await client.post(BASE, json=_payload(title="B"), headers=h)).json()
    c = (await client.post(BASE, json=_payload(title="C"), headers=h)).json()

    res = await client.put(
        f"{BASE}/reorder",
        json={"staff_id": None, "ordered_ids": [c["id"], a["id"], b["id"]]},
        headers=h,
    )
    assert res.status_code == 200, res.text
    assert [t["title"] for t in res.json()] == ["C", "A", "B"]
    assert [t["sort_order"] for t in res.json()] == [0, 1, 2]
    # 一覧も同じ並びになる。
    assert [t["title"] for t in (await client.get(BASE, headers=h)).json()] == ["C", "A", "B"]


@pytest.mark.asyncio
async def test_reorder_scope_mismatch_is_422(client, db):
    admin = await _make_user(db, "c2@example.com", "admin")
    h = _bearer(admin)
    staff = await _make_staff(db)
    shared = (await client.post(BASE, json=_payload(title="共通"), headers=h)).json()
    personal = (
        await client.post(BASE, json=_payload(title="個人", staff_id=str(staff.id)), headers=h)
    ).json()

    # 共通スコープの並びに個人ひな形を混ぜる → 422。
    res = await client.put(
        f"{BASE}/reorder",
        json={"staff_id": None, "ordered_ids": [shared["id"], personal["id"]]},
        headers=h,
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_reorder_unknown_id_is_422(client, db):
    admin = await _make_user(db, "c3@example.com", "admin")
    h = _bearer(admin)
    a = (await client.post(BASE, json=_payload(title="A"), headers=h)).json()

    res = await client.put(
        f"{BASE}/reorder",
        json={
            "staff_id": None,
            "ordered_ids": [a["id"], "00000000-0000-0000-0000-000000000123"],
        },
        headers=h,
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 4. バリデーション
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"title": "片方だけ", "start_time": "13:00"},
        {"title": "片方だけ", "end_time": "14:00"},
        {"title": "逆転", "start_time": "14:00", "end_time": "13:00"},
        {"title": "ゼロ長", "start_time": "13:00", "end_time": "13:00"},
        {"title": "   ", "start_time": "13:00", "end_time": "14:00"},
        {"title": "", "start_time": "13:00", "end_time": "14:00"},
    ],
)
async def test_create_validation_errors(client, db, body):
    admin = await _make_user(db, "d1@example.com", "admin")
    res = await client.post(BASE, json=body, headers=_bearer(admin))
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_patch_one_sided_time_is_422(client, db):
    admin = await _make_user(db, "d2@example.com", "admin")
    h = _bearer(admin)
    created = (await client.post(BASE, json=_payload(), headers=h)).json()

    res = await client.patch(f"{BASE}/{created['id']}", json={"start_time": "10:00"}, headers=h)
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 5. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_role_can_read_but_not_write(client, db):
    admin = await _make_user(db, "e1@example.com", "admin")
    staff_row = await _make_staff(db)
    member = await _make_user(db, "e2@example.com", "staff", staff_id=staff_row.id)
    created = (await client.post(BASE, json=_payload(), headers=_bearer(admin))).json()

    assert (await client.get(BASE, headers=_bearer(member))).status_code == 200
    assert (
        await client.get(f"{BASE}/history-suggestions", headers=_bearer(member))
    ).status_code == 200
    assert (await client.post(BASE, json=_payload(), headers=_bearer(member))).status_code == 403
    assert (
        await client.patch(f"{BASE}/{created['id']}", json={"title": "x"}, headers=_bearer(member))
    ).status_code == 403
    assert (
        await client.delete(f"{BASE}/{created['id']}", headers=_bearer(member))
    ).status_code == 403
    assert (
        await client.put(
            f"{BASE}/reorder",
            json={"staff_id": None, "ordered_ids": [created["id"]]},
            headers=_bearer(member),
        )
    ).status_code == 403


# ---------------------------------------------------------------------------
# 6. history-suggestions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_suggestions_aggregates_and_orders(client, db):
    admin = await _make_user(db, "f1@example.com", "admin")
    staff = await _make_staff(db)
    today = date.today()

    for offset in (30, 20, 10):
        await _make_event(db, staff, title="カンファレンス", day=today - timedelta(days=offset))
    await _make_event(
        db,
        staff,
        title="  面談 松岡  ",  # trim して集約される
        day=today - timedelta(days=5),
        start=time(14, 0),
        end=time(15, 0),
    )
    await _make_event(
        db,
        staff,
        title="面談 松岡",
        day=today - timedelta(days=1),
        start=time(9, 30),
        end=time(10, 30),
        event_type="training",
    )

    res = await client.get(f"{BASE}/history-suggestions", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    items = res.json()
    assert [i["title"] for i in items] == ["カンファレンス", "面談 松岡"]
    assert items[0]["count"] == 3
    assert items[1]["count"] == 2
    # 直近 1 件の情報を返す。
    assert items[1]["last_date"] == (today - timedelta(days=1)).isoformat()
    assert items[1]["last_start_time"] == "09:30"
    assert items[1]["last_end_time"] == "10:30"
    assert items[1]["event_type"] == "training"


@pytest.mark.asyncio
async def test_history_suggestions_excludes_fixed_defaults_and_existing(client, db):
    admin = await _make_user(db, "f2@example.com", "admin")
    h = _bearer(admin)
    staff = await _make_staff(db)
    today = date.today()

    # (a) source='fixed' は除外。
    await _make_event(db, staff, title="展開済み固定", day=today - timedelta(days=3), source="fixed")
    # (b) staff_event_defaults のタイトルに一致する行は除外 (テーブル駆動)。
    db.add(
        StaffEventDefault(
            staff_id=staff.id,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(9, 15),
            title="朝会",
        )
    )
    await db.commit()
    await _make_event(db, staff, title="朝会", day=today - timedelta(days=2), source="manual")
    # (c) 既にひな形があるタイトルは除外。
    await client.post(BASE, json=_payload(title="カンファレンス"), headers=h)
    await _make_event(db, staff, title="カンファレンス", day=today - timedelta(days=4))
    # 残るのはこれだけ。
    await _make_event(db, staff, title="新入職研修", day=today - timedelta(days=6))

    items = (await client.get(f"{BASE}/history-suggestions", headers=h)).json()
    assert [i["title"] for i in items] == ["新入職研修"]


@pytest.mark.asyncio
async def test_history_suggestions_months_window_and_staff_scope(client, db):
    admin = await _make_user(db, "f3@example.com", "admin")
    h = _bearer(admin)
    mine = await _make_staff(db, "自分")
    other = await _make_staff(db, "他人")
    today = date.today()

    await _make_event(db, mine, title="最近の用事", day=today - timedelta(days=10))
    await _make_event(db, mine, title="ずっと昔の用事", day=today - timedelta(days=400))
    await _make_event(db, other, title="他人の用事", day=today - timedelta(days=10))

    # 既定 6 ヶ月窓: 400 日前は落ちる / 全スタッフから集約。
    all_items = (await client.get(f"{BASE}/history-suggestions", headers=h)).json()
    assert sorted(i["title"] for i in all_items) == ["他人の用事", "最近の用事"]

    # months を伸ばすと古い行も入る。
    wide = (await client.get(f"{BASE}/history-suggestions?months=24", headers=h)).json()
    assert "ずっと昔の用事" in [i["title"] for i in wide]

    # staff_id 指定 = そのスタッフのイベントのみ。
    scoped = (await client.get(f"{BASE}/history-suggestions?staff_id={mine.id}", headers=h)).json()
    assert [i["title"] for i in scoped] == ["最近の用事"]
