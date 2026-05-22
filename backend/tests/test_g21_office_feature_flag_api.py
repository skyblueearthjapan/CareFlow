"""Phase G-21 T2: Office feature flag CRUD API tests.

検証観点:
  T2.13 POST /flags で enabled=true (= enabled_at セット)
  T2.14 POST /flags で enabled=false → enabled_at=NULL
  T2.15 GET /flags?feature_key=g21_new_algorithm
  T2.16 DELETE
  T2.17 非 admin で 403
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, User
from app.models.audit_log import AuditLog
from app.models.office_feature_flag import OfficeFeatureFlag

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str) -> Office:
    office = Office(name=name)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


# ---------------------------------------------------------------------------
# T2.13: POST /flags で enabled=true (enabled_at セット)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_13_post_flag_enables(client, db) -> None:
    admin = await _make_user(db, email="off-en@example.com", role="admin")
    office = await _make_office(db, name="EN-Office")

    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": "g21_new_algorithm",
            "enabled": True,
            "note": "canary phase 1",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["office_id"] == str(office.id)
    assert body["feature_key"] == "g21_new_algorithm"
    assert body["enabled_at"] is not None
    assert body["enabled_by_user_id"] == str(admin.id)
    assert body["note"] == "canary phase 1"

    # audit_log
    # Phase G-21 final H2: target_id = office_id のみ (String(64) overflow 回避).
    # feature_key は after JSONB に格納.
    rows = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "office_feature_flag_set",
                AuditLog.target_id == str(office.id),
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].after == {
        "feature_key": "g21_new_algorithm",
        "enabled": True,
        "note": "canary phase 1",
    }


# ---------------------------------------------------------------------------
# T2.14: POST /flags で enabled=false → enabled_at=NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_14_post_flag_disables(client, db) -> None:
    admin = await _make_user(db, email="off-dis@example.com", role="admin")
    office = await _make_office(db, name="DIS-Office")

    # 一度 enable
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": "g21_new_algorithm",
            "enabled": True,
        },
    )
    assert res.status_code == 200, res.text

    # disable に切替 (UPSERT)
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": "g21_new_algorithm",
            "enabled": False,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["enabled_at"] is None
    assert body["enabled_by_user_id"] is None

    # DB 上は 1 行
    rows = (
        await db.scalars(
            select(OfficeFeatureFlag).where(
                OfficeFeatureFlag.office_id == office.id,
                OfficeFeatureFlag.feature_key == "g21_new_algorithm",
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].enabled_at is None


# ---------------------------------------------------------------------------
# T2.15: GET /flags?feature_key=g21_new_algorithm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_15_list_flags_filtered_by_key(client, db) -> None:
    admin = await _make_user(db, email="off-list@example.com", role="admin")
    office_a = await _make_office(db, name="LIST-A")
    office_b = await _make_office(db, name="LIST-B")

    # 同 key で 2 office に作成
    for office in (office_a, office_b):
        res = await client.post(
            "/api/v1/office-feature-flags",
            headers=_bearer(admin),
            json={
                "office_id": str(office.id),
                "feature_key": "g21_new_algorithm",
                "enabled": True,
            },
        )
        assert res.status_code == 200, res.text

    # 未知 key は 422 (= Literal による schema 拒否 / M1)
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office_a.id),
            "feature_key": "other_feature",
            "enabled": True,
        },
    )
    assert res.status_code == 422, res.text

    res = await client.get(
        "/api/v1/office-feature-flags?feature_key=g21_new_algorithm",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 2
    for row in rows:
        assert row["feature_key"] == "g21_new_algorithm"

    # フィルタなし = 同 2 件
    res = await client.get(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 2


# ---------------------------------------------------------------------------
# T2.16: DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_16_delete_flag(client, db) -> None:
    admin = await _make_user(db, email="off-del@example.com", role="admin")
    office = await _make_office(db, name="DEL-Office")

    # 一度作成
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": "g21_new_algorithm",
            "enabled": True,
        },
    )
    assert res.status_code == 200, res.text

    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    rows = (
        await db.scalars(
            select(OfficeFeatureFlag).where(
                OfficeFeatureFlag.office_id == office.id,
                OfficeFeatureFlag.feature_key == "g21_new_algorithm",
            )
        )
    ).all()
    assert rows == []

    # 冪等
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(admin),
    )
    assert res.status_code == 204


# ---------------------------------------------------------------------------
# T2.17: 非 admin で 403 (POST / DELETE は admin only, GET は admin/manager)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_17_rbac(client, db) -> None:
    admin = await _make_user(db, email="off-rbac-admin@example.com", role="admin")
    manager = await _make_user(db, email="off-rbac-mgr@example.com", role="manager")
    staff_user = await _make_user(db, email="off-rbac-staff@example.com", role="staff")
    office = await _make_office(db, name="RBAC-Office")

    payload = {
        "office_id": str(office.id),
        "feature_key": "g21_new_algorithm",
        "enabled": True,
    }

    # POST: manager / staff は 403, admin は 200
    res = await client.post("/api/v1/office-feature-flags", headers=_bearer(manager), json=payload)
    assert res.status_code == 403

    res = await client.post(
        "/api/v1/office-feature-flags", headers=_bearer(staff_user), json=payload
    )
    assert res.status_code == 403

    res = await client.post("/api/v1/office-feature-flags", headers=_bearer(admin), json=payload)
    assert res.status_code == 200, res.text

    # DELETE: manager / staff は 403, admin は 204
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(manager),
    )
    assert res.status_code == 403
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403

    # GET: admin / manager OK, staff は 403
    res = await client.get("/api/v1/office-feature-flags", headers=_bearer(manager))
    assert res.status_code == 200
    res = await client.get("/api/v1/office-feature-flags", headers=_bearer(staff_user))
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# T2.18 (L4): DELETE で audit_log (action="office_feature_flag_delete") を記録
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_18_delete_writes_audit_log(client, db) -> None:
    """L4: DELETE 操作で audit_log に before (= 削除前の enabled/note) が残る."""
    admin = await _make_user(db, email="off-del-audit@example.com", role="admin")
    office = await _make_office(db, name="DEL-AUDIT-Office")

    # 作成 (enabled=true, note="canary")
    res = await client.post(
        "/api/v1/office-feature-flags",
        headers=_bearer(admin),
        json={
            "office_id": str(office.id),
            "feature_key": "g21_new_algorithm",
            "enabled": True,
            "note": "canary",
        },
    )
    assert res.status_code == 200, res.text

    # 削除
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # Phase G-21 final H2: target_id = office_id のみ. feature_key は JSONB.
    rows = (
        await db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == "office_feature_flag_delete",
                AuditLog.target_id == str(office.id),
            )
            .order_by(AuditLog.id)
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == admin.id
    assert row.target_table == "office_feature_flags"
    assert row.before == {
        "feature_key": "g21_new_algorithm",
        "enabled": True,
        "note": "canary",
    }
    assert row.after is None

    # 冪等 DELETE (= 行が無い場合) では audit_log は追加されない
    res = await client.delete(
        f"/api/v1/office-feature-flags/{office.id}/g21_new_algorithm",
        headers=_bearer(admin),
    )
    assert res.status_code == 204
    rows = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "office_feature_flag_delete",
                AuditLog.target_id == str(office.id),
            )
        )
    ).all()
    assert len(rows) == 1  # 増えない
