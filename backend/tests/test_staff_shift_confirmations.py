"""月次出勤カレンダー確定 (shift-confirmations) + 休み関連通知のテスト.

正典 = docs/plans/staff-shift-confirmation-design.md。

検証観点:
  1. 確定 API: RBAC (本人GET可/他人404/staff POST 403/admin POST 201)、
     月初日以外 422、upsert (再確定で同一行更新)、通知 (再確定で2通目)
  2. 却下通知: staff_off の reject → 申請者に leave_rejected 1行 (却下理由入り)。
     staff_event の reject では出ない
  3. 取消通知: override DELETE → 紐付けユーザーに leave_cancelled。
     未紐付け staff は通知 0 行でも削除成功。再作成→再削除で 2 通目が出る
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.notification import Notification


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(db, name: str = "確定 花子") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _notification_count(db, *, user_id, type_: str) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.type == type_)
        )
        or 0
    )


def _url(staff_id) -> str:
    return f"/api/v1/staff/{staff_id}/shift-confirmations"


# ---------------------------------------------------------------------------
# 1) 確定 API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_month_creates_and_notifies(client, db) -> None:
    admin = await _make_user(db, "sc-admin1@example.com", "admin")
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-staff1@example.com", "staff", staff_id=staff.id)

    res = await client.post(_url(staff.id), headers=_bearer(admin), json={"month": "2026-09-01"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["staff_id"] == str(staff.id)
    assert body["month"] == "2026-09-01"
    assert body["confirmed_by"] == str(admin.id)

    assert await _notification_count(db, user_id=staff_user.id, type_="shift_confirmed") == 1
    row = await db.scalar(
        select(Notification).where(
            Notification.user_id == staff_user.id, Notification.type == "shift_confirmed"
        )
    )
    assert row is not None and "9月" in row.title


@pytest.mark.asyncio
async def test_confirm_month_upserts_and_renotifies(client, db) -> None:
    """再確定 = 同一行の更新 + 2 通目の通知 (再周知)。"""
    admin = await _make_user(db, "sc-admin2@example.com", "admin")
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-staff2@example.com", "staff", staff_id=staff.id)

    res1 = await client.post(_url(staff.id), headers=_bearer(admin), json={"month": "2026-09-01"})
    res2 = await client.post(_url(staff.id), headers=_bearer(admin), json={"month": "2026-09-01"})
    assert res2.status_code == 201, res2.text
    assert res1.json()["id"] == res2.json()["id"]  # upsert = 同一行
    assert res2.json()["confirmed_at"] >= res1.json()["confirmed_at"]

    assert await _notification_count(db, user_id=staff_user.id, type_="shift_confirmed") == 2


@pytest.mark.asyncio
async def test_confirm_month_requires_first_of_month(client, db) -> None:
    admin = await _make_user(db, "sc-admin3@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(_url(staff.id), headers=_bearer(admin), json={"month": "2026-09-15"})
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_confirm_month_staff_forbidden(client, db) -> None:
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-staff4@example.com", "staff", staff_id=staff.id)
    res = await client.post(
        _url(staff.id), headers=_bearer(staff_user), json={"month": "2026-09-01"}
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_confirm_month_without_linked_user_is_noop_notification(client, db) -> None:
    """アカウント未紐付け staff でも確定は成功し、通知だけ no-op。"""
    admin = await _make_user(db, "sc-admin5@example.com", "admin")
    staff = await _make_staff(db)  # 紐付けユーザーなし

    res = await client.post(_url(staff.id), headers=_bearer(admin), json={"month": "2026-09-01"})
    assert res.status_code == 201, res.text
    total = await db.scalar(
        select(func.count()).select_from(Notification).where(Notification.type == "shift_confirmed")
    )
    assert int(total or 0) == 0


@pytest.mark.asyncio
async def test_list_confirmations_rbac_and_range(client, db) -> None:
    admin = await _make_user(db, "sc-admin6@example.com", "admin")
    staff = await _make_staff(db)
    me = await _make_user(db, "sc-staff6@example.com", "staff", staff_id=staff.id)
    other_staff = await _make_staff(db, name="他人 次郎")
    other = await _make_user(db, "sc-staff6b@example.com", "staff", staff_id=other_staff.id)

    for m in ("2026-08-01", "2026-09-01", "2026-10-01"):
        await client.post(_url(staff.id), headers=_bearer(admin), json={"month": m})

    # 本人は読める + 範囲フィルタ
    res = await client.get(
        _url(staff.id),
        headers=_bearer(me),
        params={"from": "2026-09-01", "to": "2026-09-30"},
    )
    assert res.status_code == 200, res.text
    months = [r["month"] for r in res.json()]
    assert months == ["2026-09-01"]

    # 他人の staff は 404
    res = await client.get(_url(staff.id), headers=_bearer(other))
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 2) 却下通知 (leave_rejected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_staff_off_notifies_requester(client, db) -> None:
    admin = await _make_user(db, "sc-rj-admin@example.com", "admin")
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-rj-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(staff_user),
        json={
            "request_type": "staff_off",
            "payload": {"staff_id": str(staff.id), "date": "2026-09-07", "override_type": "off"},
            "target_staff_id": str(staff.id),
            "target_date": "2026-09-07",
        },
    )
    assert res.status_code == 201, res.text
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "その日は人員不足のため"},
    )
    assert res.status_code == 200, res.text

    assert await _notification_count(db, user_id=staff_user.id, type_="leave_rejected") == 1
    row = await db.scalar(
        select(Notification).where(
            Notification.user_id == staff_user.id, Notification.type == "leave_rejected"
        )
    )
    assert row is not None
    assert "却下" in row.title
    assert "人員不足" in (row.body or "")


@pytest.mark.asyncio
async def test_reject_staff_event_does_not_notify(client, db) -> None:
    """leave_rejected は staff_off 限定 (staff_event の却下では出ない)。"""
    admin = await _make_user(db, "sc-rj2-admin@example.com", "admin")
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-rj2-staff@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(staff_user),
        json={
            "request_type": "staff_event",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-09-07",
                "start_time": "10:00",
                "end_time": "11:00",
                "title": "面談",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-09-07",
        },
    )
    assert res.status_code == 201, res.text
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "却下"},
    )
    assert res.status_code == 200, res.text
    assert await _notification_count(db, user_id=staff_user.id, type_="leave_rejected") == 0


# ---------------------------------------------------------------------------
# 3) 取消通知 (leave_cancelled)
# ---------------------------------------------------------------------------


async def _create_override(client, admin, staff_id, *, day: str = "2026-09-07") -> str:
    res = await client.post(
        f"/api/v1/staff/{staff_id}/overrides",
        headers=_bearer(admin),
        json={"date": day, "type": "休み"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


@pytest.mark.asyncio
async def test_delete_override_notifies_staff(client, db) -> None:
    admin = await _make_user(db, "sc-cx-admin@example.com", "admin")
    staff = await _make_staff(db)
    staff_user = await _make_user(db, "sc-cx-staff@example.com", "staff", staff_id=staff.id)

    oid = await _create_override(client, admin, staff.id)
    res = await client.delete(f"/api/v1/staff/{staff.id}/overrides/{oid}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    assert await _notification_count(db, user_id=staff_user.id, type_="leave_cancelled") == 1
    row = await db.scalar(
        select(Notification).where(
            Notification.user_id == staff_user.id, Notification.type == "leave_cancelled"
        )
    )
    assert row is not None
    assert "休み" in row.title
    assert "2026年9月7日" in (row.body or "")

    # 再作成 → 再削除で 2 通目が出る (毎回通知)
    oid2 = await _create_override(client, admin, staff.id)
    res = await client.delete(f"/api/v1/staff/{staff.id}/overrides/{oid2}", headers=_bearer(admin))
    assert res.status_code == 204
    assert await _notification_count(db, user_id=staff_user.id, type_="leave_cancelled") == 2


@pytest.mark.asyncio
async def test_delete_override_without_linked_user_still_works(client, db) -> None:
    admin = await _make_user(db, "sc-cx2-admin@example.com", "admin")
    staff = await _make_staff(db)  # 紐付けユーザーなし

    oid = await _create_override(client, admin, staff.id)
    res = await client.delete(f"/api/v1/staff/{staff.id}/overrides/{oid}", headers=_bearer(admin))
    assert res.status_code == 204, res.text
    total = await db.scalar(
        select(func.count()).select_from(Notification).where(Notification.type == "leave_cancelled")
    )
    assert int(total or 0) == 0
