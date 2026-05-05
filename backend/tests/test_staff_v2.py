"""W1-BE2 v2 スタッフマスタ整理のための CRUD + RBAC + バリデーション検証.

設計仕様書 v0.9 §4.2 / 実装手順書 v0.2 §2 W1-BE2 / API 契約 §2 に対応。

検証観点:
  1. 削除 8 カラム (can_double_team / home_address / home_lat / home_lng /
     areas / max_per_day / skill_level / assignment_volume) が API レスポンスに
     含まれない
  2. 削除カラムを Create / Update リクエストに含めると 422 になる
     (StaffBase は ``extra="forbid"``)
  3. status の 3 値 enum バリデーション (active / on_leave / retired 以外で 422)
  4. メンター (mentor_id) の設定・取得が動く
  5. RBAC: 認証なし 401 / staff ロールでの delete 403 / 自分以外の取得 404
  6. 状態 3 値の境界値で 200
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User

# ---------------------------------------------------------------------------
# テストヘルパ
# ---------------------------------------------------------------------------


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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. 削除 8 カラムが API レスポンスに含まれない
# ---------------------------------------------------------------------------


_REMOVED_FIELDS: tuple[str, ...] = (
    "can_double_team",
    "home_address",
    "home_lat",
    "home_lng",
    "areas",
    "max_per_day",
    "skill_level",
    "assignment_volume",
)


@pytest.mark.asyncio
async def test_v2_staff_create_response_excludes_removed_fields(client, db) -> None:
    """POST /staff の 201 レスポンスに削除カラムが出てこない."""
    admin = await _make_user(db, "v2-staff-admin@example.com", "admin")
    payload = {"name": "山田 花子", "kana": "ヤマダ ハナコ"}
    res = await client.post("/api/v1/staff", json=payload, headers=_bearer(admin))
    assert res.status_code == 201, res.text

    body = res.json()
    for field in _REMOVED_FIELDS:
        assert field not in body, f"削除済みフィールド {field!r} が response に残存"


@pytest.mark.asyncio
async def test_v2_staff_get_response_excludes_removed_fields(client, db) -> None:
    """GET /staff/{id} のレスポンスに削除カラムが出てこない."""
    admin = await _make_user(db, "v2-staff-admin2@example.com", "admin")

    # 直接 ORM 経由で行を作る (既存 test_staff.py と同パターン)
    s = Staff(name="鈴木 太郎", kana="スズキ タロウ")
    db.add(s)
    await db.commit()
    await db.refresh(s)

    res = await client.get(f"/api/v1/staff/{s.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    for field in _REMOVED_FIELDS:
        assert field not in body, f"削除済みフィールド {field!r} が response に残存"


@pytest.mark.asyncio
async def test_v2_staff_list_response_excludes_removed_fields(client, db) -> None:
    """GET /staff 一覧のレスポンス各要素に削除カラムが出てこない."""
    admin = await _make_user(db, "v2-staff-admin-list@example.com", "admin")

    s = Staff(name="佐藤 一郎")
    db.add(s)
    await db.commit()

    res = await client.get("/api/v1/staff", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    rows = res.json()
    assert isinstance(rows, list)
    assert rows, "list が空。最低 1 件取得できる前提"

    for row in rows:
        for field in _REMOVED_FIELDS:
            assert field not in row, f"削除済みフィールド {field!r} が list に残存"


# ---------------------------------------------------------------------------
# 2. 削除カラムを Create リクエストに含めると 422 (extra="forbid")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_payload",
    [
        {"can_double_team": True},
        {"home_address": "千葉県千葉市稲毛区1-2-3"},
        {"home_lat": 35.66},
        {"home_lng": 140.10},
        {"areas": ["A1", "B1"]},
        {"max_per_day": 5},
        {"skill_level": "ベテラン"},
        {"assignment_volume": "多め"},
    ],
)
@pytest.mark.asyncio
async def test_v2_staff_create_rejects_removed_fields(client, db, extra_payload: dict) -> None:
    """削除カラムを含む payload は 422 で拒否される."""
    admin = await _make_user(
        db,
        f"v2-extra-{next(iter(extra_payload))}@example.com",
        "admin",
    )
    payload = {"name": "テスト 太郎", **extra_payload}
    res = await client.post("/api/v1/staff", json=payload, headers=_bearer(admin))
    assert res.status_code == 422, (
        f"削除済みフィールド {extra_payload!r} は 422 で拒否されるべき: {res.text}"
    )


# ---------------------------------------------------------------------------
# 3. 状態 (status) の 3 値 enum バリデーション
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valid_status", ["active", "on_leave", "retired"])
@pytest.mark.asyncio
async def test_v2_staff_create_accepts_valid_status(client, db, valid_status: str) -> None:
    """3 値のいずれかなら 201 で作成成功."""
    admin = await _make_user(db, f"v2-st-ok-{valid_status}@example.com", "admin")
    res = await client.post(
        "/api/v1/staff",
        json={"name": f"STATUS_{valid_status}", "status": valid_status},
        headers=_bearer(admin),
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == valid_status


@pytest.mark.parametrize(
    "invalid_status",
    ["inactive", "ACTIVE", "在籍", "draft", "", "休職"],
)
@pytest.mark.asyncio
async def test_v2_staff_create_rejects_invalid_status(client, db, invalid_status: str) -> None:
    """3 値以外なら 422 でリクエスト拒否."""
    admin = await _make_user(
        db,
        f"v2-st-ng-{abs(hash(invalid_status)) % 10_000}@example.com",
        "admin",
    )
    res = await client.post(
        "/api/v1/staff",
        json={"name": "NG", "status": invalid_status},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, f"status={invalid_status!r} は 422 で拒否されるべき: {res.text}"


@pytest.mark.asyncio
async def test_v2_staff_default_status_is_active(client, db) -> None:
    """status を省略すると active になる."""
    admin = await _make_user(db, "v2-st-default@example.com", "admin")
    res = await client.post("/api/v1/staff", json={"name": "デフォルト"}, headers=_bearer(admin))
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "active"


@pytest.mark.asyncio
async def test_v2_staff_patch_rejects_invalid_status(client, db) -> None:
    """PATCH でも 3 値以外は 422."""
    admin = await _make_user(db, "v2-st-patch-ng@example.com", "admin")
    s = Staff(name="patch対象")
    db.add(s)
    await db.commit()
    await db.refresh(s)

    res = await client.patch(
        f"/api/v1/staff/{s.id}",
        json={"status": "inactive"},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 4. メンター (mentor_id) の設定・取得
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_staff_mentor_set_and_get(client, db) -> None:
    """mentor_id を作成 / PATCH / GET で round-trip できる."""
    admin = await _make_user(db, "v2-mentor-admin@example.com", "admin")

    # メンター候補のスタッフを先に作る
    mentor_payload = {"name": "メンター先輩", "kana": "メンターセンパイ"}
    res_mentor = await client.post("/api/v1/staff", json=mentor_payload, headers=_bearer(admin))
    assert res_mentor.status_code == 201, res_mentor.text
    mentor_id = res_mentor.json()["id"]

    # 新人スタッフを mentor_id 付きで作る
    new_payload = {"name": "新人 一郎", "mentor_id": mentor_id}
    res_new = await client.post("/api/v1/staff", json=new_payload, headers=_bearer(admin))
    assert res_new.status_code == 201, res_new.text
    assert res_new.json()["mentor_id"] == mentor_id

    new_id = res_new.json()["id"]

    # GET でも mentor_id が読める
    res_get = await client.get(f"/api/v1/staff/{new_id}", headers=_bearer(admin))
    assert res_get.status_code == 200, res_get.text
    assert res_get.json()["mentor_id"] == mentor_id


@pytest.mark.asyncio
async def test_v2_staff_mentor_patch_to_null(client, db) -> None:
    """mentor_id を PATCH で null クリアできる."""
    admin = await _make_user(db, "v2-mentor-clear@example.com", "admin")

    mentor = Staff(name="メンター")
    mentee = Staff(name="メンティー")
    db.add_all([mentor, mentee])
    await db.commit()
    await db.refresh(mentor)
    await db.refresh(mentee)
    mentee.mentor_id = mentor.id
    await db.commit()
    await db.refresh(mentee)

    # クリア
    res = await client.patch(
        f"/api/v1/staff/{mentee.id}",
        json={"mentor_id": None},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["mentor_id"] is None


# ---------------------------------------------------------------------------
# 5. RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_staff_no_token_returns_401(client) -> None:
    """無認証で list するとは 401."""
    res = await client.get("/api/v1/staff")
    assert res.status_code == 401, res.text


@pytest.mark.asyncio
async def test_v2_staff_create_requires_admin_or_manager(client, db) -> None:
    """staff ロールは POST 不可 (403)."""
    s = Staff(name="自分自身")
    db.add(s)
    await db.commit()
    await db.refresh(s)

    staff_user = await _make_user(db, "v2-staff-create-self@example.com", "staff", staff_id=s.id)
    res = await client.post(
        "/api/v1/staff",
        json={"name": "他人"},
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_v2_staff_delete_requires_admin(client, db) -> None:
    """manager は DELETE 不可 (admin only)."""
    target = Staff(name="削除対象")
    db.add(target)
    await db.commit()
    await db.refresh(target)

    manager = await _make_user(db, "v2-mgr@example.com", "manager")
    res = await client.delete(f"/api/v1/staff/{target.id}", headers=_bearer(manager))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_v2_staff_role_can_only_see_self(client, db) -> None:
    """staff は他人の GET で 404 (情報漏洩防止)."""
    me = Staff(name="自分")
    other = Staff(name="他人")
    db.add_all([me, other])
    await db.commit()
    await db.refresh(me)
    await db.refresh(other)

    staff_user = await _make_user(db, "v2-staff-self@example.com", "staff", staff_id=me.id)
    res = await client.get(f"/api/v1/staff/{other.id}", headers=_bearer(staff_user))
    assert res.status_code == 404, res.text

    # 自分は見える
    res_self = await client.get(f"/api/v1/staff/{me.id}", headers=_bearer(staff_user))
    assert res_self.status_code == 200, res_self.text


@pytest.mark.asyncio
async def test_v2_staff_unknown_id_returns_404(client, db) -> None:
    admin = await _make_user(db, "v2-staff-404@example.com", "admin")
    res = await client.get(f"/api/v1/staff/{uuid4()}", headers=_bearer(admin))
    assert res.status_code == 404, res.text
