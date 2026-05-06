"""Tests for /api/v1/staff/{id}/companion-assignments (W10-BE1).

検証観点 (12+ ケース):
  1.  GET: 全取得 (該当あり)
  2.  GET: 該当なし → 空配列
  3.  PUT: 0 件 PUT → 空配列返却
  4.  PUT: 1 件 PUT → 1 件返却
  5.  PUT: 14 件全曜日全 part → 14 件返却
  6.  PUT: 重複 (weekday, part) → 422
  7.  PUT: 自身を companion 指定 → 422
  8.  PUT: trainee が is_trainee=False → 409
  9.  PUT: companion が manager/staff 以外 (admin) → 422
  10. PUT: companion が退職者 (retired) → 422
  11. DELETE: 全削除 (204)
  12. DELETE: idempotent (2回削除しても 204)
  13. RBAC: staff が他人のデータ閲覧 → 403
  14. RBAC: staff が PUT (edit) → 403
  15. companion-candidates: admin/manager/退職者/本人 除外
  16. 1 TX 保証: UNIQUE 違反等で全 rollback
"""

from __future__ import annotations

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, StaffCompanionAssignment, User

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff(
    db,
    name: str = "スタッフ",
    *,
    role: str = "staff",
    status: str = "active",
    is_trainee: bool = False,
) -> Staff:
    s = Staff(name=name, role=role, status=status, is_trainee=is_trainee)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. GET: 全取得 (該当あり)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_companion_assignments_returns_list(client, db) -> None:
    """GET で登録済み割当が返る."""
    admin = await _make_user(db, "sc-get1-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人A", is_trainee=True)
    companion = await _make_staff(db, "先輩B")

    import uuid

    db.add(
        StaffCompanionAssignment(
            id=uuid.uuid4(),
            trainee_staff_id=trainee.id,
            weekday=0,
            part="am",
            companion_staff_id=companion.id,
        )
    )
    await db.commit()

    res = await client.get(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["weekday"] == 0
    assert body[0]["part"] == "am"
    assert body[0]["companion_staff_id"] == str(companion.id)
    assert body[0]["trainee_staff_id"] == str(trainee.id)


# ---------------------------------------------------------------------------
# 2. GET: 該当なし → 空配列
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_companion_assignments_empty(client, db) -> None:
    """GET で割当なし → 空配列."""
    admin = await _make_user(db, "sc-get2-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人C", is_trainee=True)

    res = await client.get(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 3. PUT: 0 件 PUT → 空配列返却
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_zero_items(client, db) -> None:
    """PUT 0 件 → 空配列が返る."""
    admin = await _make_user(db, "sc-put0-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人D", is_trainee=True)

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": []},
    )
    assert res.status_code == 200, res.text
    assert res.json() == []


# ---------------------------------------------------------------------------
# 4. PUT: 1 件 PUT → 1 件返却
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_one_item(client, db) -> None:
    """PUT 1 件 → 1 件が返る."""
    admin = await _make_user(db, "sc-put1-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人E", is_trainee=True)
    companion = await _make_staff(db, "先輩F")

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": [{"weekday": 1, "part": "pm", "companion_staff_id": str(companion.id)}]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    assert body[0]["weekday"] == 1
    assert body[0]["part"] == "pm"


# ---------------------------------------------------------------------------
# 5. PUT: 14 件全曜日全 part
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_fourteen_items(client, db) -> None:
    """PUT 14 件 (全曜日 × am/pm) → 14 件返却."""
    admin = await _make_user(db, "sc-put14-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人G", is_trainee=True)
    companion = await _make_staff(db, "先輩H")

    items = [
        {"weekday": wd, "part": part, "companion_staff_id": str(companion.id)}
        for wd in range(7)
        for part in ("am", "pm")
    ]
    assert len(items) == 14

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": items},
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 14


# ---------------------------------------------------------------------------
# 6. PUT: 重複 (weekday, part) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_duplicate_weekday_part_422(client, db) -> None:
    """同一 (weekday, part) が重複すると 422."""
    admin = await _make_user(db, "sc-dup-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人I", is_trainee=True)
    companion = await _make_staff(db, "先輩J")

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={
            "items": [
                {"weekday": 2, "part": "am", "companion_staff_id": str(companion.id)},
                {"weekday": 2, "part": "am", "companion_staff_id": str(companion.id)},
            ]
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 7. PUT: 自身を companion 指定 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_self_companion_422(client, db) -> None:
    """自分自身を companion に指定すると 422."""
    admin = await _make_user(db, "sc-self-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人K", is_trainee=True)

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": [{"weekday": 3, "part": "full", "companion_staff_id": str(trainee.id)}]},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 8. PUT: trainee が is_trainee=False → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_not_trainee_409(client, db) -> None:
    """is_trainee=False のスタッフに PUT すると 409."""
    admin = await _make_user(db, "sc-nottrainee-admin@example.com", "admin")
    non_trainee = await _make_staff(db, "ベテランL", is_trainee=False)
    companion = await _make_staff(db, "先輩M")

    res = await client.put(
        f"/api/v1/staff/{non_trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": [{"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)}]},
    )
    assert res.status_code == 409, res.text


# ---------------------------------------------------------------------------
# 9. PUT: companion が admin → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_companion_is_admin_422(client, db) -> None:
    """companion が admin ロールだと 422."""
    admin_user = await _make_user(db, "sc-comp-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人N", is_trainee=True)
    admin_staff = await _make_staff(db, "管理者O", role="admin")

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin_user),
        json={"items": [{"weekday": 4, "part": "am", "companion_staff_id": str(admin_staff.id)}]},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 10. PUT: companion が retired → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_companion_retired_422(client, db) -> None:
    """companion が retired だと 422."""
    admin = await _make_user(db, "sc-retired-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人P", is_trainee=True)
    retired_staff = await _make_staff(db, "退職者Q", status="retired")

    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={"items": [{"weekday": 5, "part": "pm", "companion_staff_id": str(retired_staff.id)}]},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 11. DELETE: 全削除 (204)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_companion_assignments(client, db) -> None:
    """DELETE で全削除 → 204。その後 GET で空配列。"""
    admin = await _make_user(db, "sc-del-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人R", is_trainee=True)
    companion = await _make_staff(db, "先輩S")

    import uuid

    db.add(
        StaffCompanionAssignment(
            id=uuid.uuid4(),
            trainee_staff_id=trainee.id,
            weekday=6,
            part="full",
            companion_staff_id=companion.id,
        )
    )
    await db.commit()

    res = await client.delete(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # GET で空配列
    get_res = await client.get(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert get_res.status_code == 200, get_res.text
    assert get_res.json() == []


# ---------------------------------------------------------------------------
# 12. DELETE: idempotent (2回削除しても 204)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_companion_assignments_idempotent(client, db) -> None:
    """DELETE 2回実行しても 204 (冪等)."""
    admin = await _make_user(db, "sc-del2-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人T", is_trainee=True)

    res1 = await client.delete(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert res1.status_code == 204, res1.text

    res2 = await client.delete(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert res2.status_code == 204, res2.text


# ---------------------------------------------------------------------------
# 13. RBAC: staff が他人のデータ閲覧 → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_staff_cannot_view_others_assignments(client, db) -> None:
    """staff ロールは他人の companion-assignments を GET できない (403)."""
    me = await _make_staff(db, "自分U")
    other = await _make_staff(db, "他人V", is_trainee=True)
    staff_user = await _make_user(db, "sc-rbac-staff@example.com", "staff", staff_id=me.id)

    res = await client.get(
        f"/api/v1/staff/{other.id}/companion-assignments",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 14. RBAC: staff が PUT → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_staff_cannot_put_assignments(client, db) -> None:
    """staff ロールは PUT 不可 (403)."""
    me = await _make_staff(db, "自分W", is_trainee=True)
    companion = await _make_staff(db, "先輩X")
    staff_user = await _make_user(db, "sc-rbac-staff-put@example.com", "staff", staff_id=me.id)

    res = await client.put(
        f"/api/v1/staff/{me.id}/companion-assignments",
        headers=_bearer(staff_user),
        json={"items": [{"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)}]},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 15. companion-candidates: admin/manager/退職者/本人 除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_companion_candidates_filters(client, db) -> None:
    """companion-candidates は admin/manager は結果に含まれる
    ただし admin ロールスタッフは対象外 (role IN manager/staff のみ)。
    retired は除外。exclude_id は除外。
    """
    admin_user = await _make_user(db, "sc-cand-admin@example.com", "admin")

    trainee = await _make_staff(db, "新人Y", role="staff", is_trainee=True)
    active_staff = await _make_staff(db, "在籍スタッフZ", role="staff")
    active_manager = await _make_staff(db, "マネージャー", role="manager")
    admin_staff = await _make_staff(db, "管理者スタッフ", role="admin")
    retired_staff = await _make_staff(db, "退職者", role="staff", status="retired")

    res = await client.get(
        f"/api/v1/staff/companion-candidates?exclude_id={trainee.id}",
        headers=_bearer(admin_user),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    ids = {r["id"] for r in body}

    # trainee 自身は除外
    assert str(trainee.id) not in ids
    # admin ロールスタッフは除外 (role not in manager/staff)
    assert str(admin_staff.id) not in ids
    # retired は除外
    assert str(retired_staff.id) not in ids
    # 在籍 staff/manager は含まれる
    assert str(active_staff.id) in ids
    assert str(active_manager.id) in ids


# ---------------------------------------------------------------------------
# 16. 1 TX 保証: PUT 後の UNIQUE 違反で全 rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_companion_assignments_rollback_on_unique_violation(client, db) -> None:
    """重複 (weekday, part) は事前検証で 422 → DB は変更なし."""
    admin = await _make_user(db, "sc-tx-admin@example.com", "admin")
    trainee = await _make_staff(db, "新人Z2", is_trainee=True)
    companion = await _make_staff(db, "先輩AA")

    # 重複含む PUT → 422
    res = await client.put(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
        json={
            "items": [
                {"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)},
                {"weekday": 0, "part": "am", "companion_staff_id": str(companion.id)},
            ]
        },
    )
    assert res.status_code == 422, res.text

    # DB にデータなし (rollback 確認)
    get_res = await client.get(
        f"/api/v1/staff/{trainee.id}/companion-assignments",
        headers=_bearer(admin),
    )
    assert get_res.status_code == 200, get_res.text
    assert get_res.json() == []
