"""W2-BE5: pending_requests CRUD + RBAC + state transition tests.

設計仕様書 v0.9 §3.5 / §4.4 / API 契約 v0.1 §7 に対応する受入テスト。

検証観点:
  1. POST 申請作成 (admin / manager / staff)
  2. RBAC:
     - staff は staff_off / staff_event の自分軸のみ申請可
     - staff_create / staff_mentor / patient_create は staff から 403
     - staff は他スタッフの target_staff_id を指定すると 403
     - approve / reject は admin / manager のみ (staff は 403)
  3. GET /api/v1/pending-requests 一覧 + フィルタ
  4. GET /api/v1/pending-requests/{id} 詳細
  5. PATCH approve / approve-with-edit / reject の状態遷移
  6. 却下理由必須バリデーション (rejection_reason 空 → 422)
  7. 冪等性: 同一申請の二重 approve は 409 を返す
  8. patient_reschedule は scope 必須 (422)
"""

from __future__ import annotations

from datetime import date

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User
from app.models.pending_request import PendingRequest

# ---------------------------------------------------------------------------
# Helpers
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


async def _make_staff(db, name: str = "山田 花子") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_patient(db, *, code: str = "P-PR-001") -> Patient:
    p = Patient(code=code, name="患者 太郎", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _staff_off_payload(*, staff_id, target_date: date | str = "2026-05-11") -> dict:
    return {
        "request_type": "staff_off",
        "payload": {
            "staff_id": str(staff_id),
            "date": str(target_date),
            "override_type": "off",
        },
        "target_staff_id": str(staff_id),
        "target_date": str(target_date),
    }


# ---------------------------------------------------------------------------
# 1) POST 申請作成 (admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_creates_pending_request(client, db) -> None:
    admin = await _make_user(db, "pr-admin1@example.com", "admin")
    staff = await _make_staff(db)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["request_type"] == "staff_off"
    assert body["status"] == "pending"
    assert body["requester_user_id"] == str(admin.id)
    assert body["target_staff_id"] == str(staff.id)
    assert body["approved_at"] is None
    assert body["rejected_at"] is None


# ---------------------------------------------------------------------------
# 2) RBAC: staff の自分軸スコープ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_can_request_own_staff_off(client, db) -> None:
    staff = await _make_staff(db, name="自分 太郎")
    user = await _make_user(db, "pr-self@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_staff_off_payload(staff_id=staff.id),
    )
    assert res.status_code == 201, res.text
    assert res.json()["target_staff_id"] == str(staff.id)


@pytest.mark.asyncio
async def test_staff_cannot_request_others_staff_off(client, db) -> None:
    me_staff = await _make_staff(db, name="自分 太郎")
    other_staff = await _make_staff(db, name="他人 次郎")
    user = await _make_user(db, "pr-other@example.com", "staff", staff_id=me_staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json=_staff_off_payload(staff_id=other_staff.id),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_staff_cannot_create_admin_only_request_types(client, db) -> None:
    staff = await _make_staff(db)
    user = await _make_user(db, "pr-admin-only@example.com", "staff", staff_id=staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json={
            "request_type": "staff_create",
            "payload": {"name": "新人 花子"},
        },
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 3) GET /api/v1/pending-requests 一覧 + フィルタ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_status_and_request_type(client, db) -> None:
    admin = await _make_user(db, "pr-list@example.com", "admin")
    staff = await _make_staff(db)

    # 2 件作成 (staff_off / staff_event)
    await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "staff_event",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "start_time": "10:00",
                "end_time": "11:00",
                "title": "管理者会議",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )

    # filter status=pending
    res = await client.get(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        params={"status": "pending"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    assert all(item["status"] == "pending" for item in body["items"])

    # filter request_type=staff_off
    res = await client.get(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        params={"request_type": "staff_off"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["request_type"] == "staff_off"


@pytest.mark.asyncio
async def test_list_filters_by_target_date_range(client, db) -> None:
    admin = await _make_user(db, "pr-daterange@example.com", "admin")
    staff = await _make_staff(db)

    for d in ("2026-05-11", "2026-05-15", "2026-06-01"):
        await client.post(
            "/api/v1/pending-requests",
            headers=_bearer(admin),
            json=_staff_off_payload(staff_id=staff.id, target_date=d),
        )

    res = await client.get(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        params={
            "target_date_from": "2026-05-01",
            "target_date_to": "2026-05-31",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2


# ---------------------------------------------------------------------------
# 4) GET 詳細
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail(client, db) -> None:
    admin = await _make_user(db, "pr-detail@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    assert res.status_code == 201
    pr_id = res.json()["id"]

    res = await client.get(
        f"/api/v1/pending-requests/{pr_id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["id"] == pr_id


@pytest.mark.asyncio
async def test_staff_cannot_read_others_request(client, db) -> None:
    me_staff = await _make_staff(db, name="自分")
    other_staff = await _make_staff(db, name="他人")
    me_user = await _make_user(db, "pr-staff-read1@example.com", "staff", staff_id=me_staff.id)
    other_user = await _make_user(
        db, "pr-staff-read2@example.com", "staff", staff_id=other_staff.id
    )

    # other_user が自分用に申請
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(other_user),
        json=_staff_off_payload(staff_id=other_staff.id),
    )
    assert res.status_code == 201
    pr_id = res.json()["id"]

    # me_user は他人の申請を見れない (404)
    res2 = await client.get(
        f"/api/v1/pending-requests/{pr_id}",
        headers=_bearer(me_user),
    )
    assert res2.status_code == 404


# ---------------------------------------------------------------------------
# 5) approve の状態遷移
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_transitions_to_approved(client, db) -> None:
    admin = await _make_user(db, "pr-approve@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == str(admin.id)
    assert body["approved_at"] is not None


@pytest.mark.asyncio
async def test_approve_with_edit_requires_edited_payload(client, db) -> None:
    admin = await _make_user(db, "pr-approve-edit-req@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    # edited_payload を渡さないと 422
    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve-with-edit",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_approve_with_edit_uses_edited_payload(client, db) -> None:
    admin = await _make_user(db, "pr-approve-edit2@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve-with-edit",
        headers=_bearer(admin),
        json={
            "edited_payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
                "reason": "編集理由",
            }
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["edited_payload"]["reason"] == "編集理由"


# ---------------------------------------------------------------------------
# 6) reject + rejection_reason 必須
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_requires_reason(client, db) -> None:
    admin = await _make_user(db, "pr-reject@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": ""},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_reject_transitions_to_rejected(client, db) -> None:
    admin = await _make_user(db, "pr-reject2@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "業務調整のため"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "rejected"
    assert body["rejected_by"] == str(admin.id)
    assert body["rejection_reason"] == "業務調整のため"


# ---------------------------------------------------------------------------
# 7) 冪等性: 二重 approve / approve→reject は 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_approve_returns_409(client, db) -> None:
    admin = await _make_user(db, "pr-dbl-approve@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res1 = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res1.status_code == 200, res1.text

    # 2 回目は 409
    res2 = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res2.status_code == 409, res2.text


@pytest.mark.asyncio
async def test_reject_after_approve_returns_409(client, db) -> None:
    admin = await _make_user(db, "pr-rej-after-app@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 200

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(admin),
        json={"rejection_reason": "後出し却下"},
    )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# 8) RBAC: staff は approve / reject 不可
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_cannot_approve(client, db) -> None:
    admin = await _make_user(db, "pr-rbac-admin@example.com", "admin")
    staff_obj = await _make_staff(db)
    staff_user = await _make_user(db, "pr-rbac-staff@example.com", "staff", staff_id=staff_obj.id)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff_obj.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(staff_user),
        json={},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_staff_cannot_reject(client, db) -> None:
    admin = await _make_user(db, "pr-rbac-admin2@example.com", "admin")
    staff_obj = await _make_staff(db)
    staff_user = await _make_user(db, "pr-rbac-staff2@example.com", "staff", staff_id=staff_obj.id)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff_obj.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/reject",
        headers=_bearer(staff_user),
        json={"rejection_reason": "却下"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 9) patient_reschedule は scope 必須 (§3.5.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_reschedule_requires_scope(client, db) -> None:
    admin = await _make_user(db, "pr-resched@example.com", "admin")
    patient = await _make_patient(db)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "patient_reschedule",
            "payload": {
                "patient_id": str(patient.id),
                "date": "2026-05-11",
                "start_time": "10:00",
                "end_time": "11:00",
            },
            "target_patient_id": str(patient.id),
            "target_date": "2026-05-11",
            # scope を意図的に未指定
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 10) 認証なし → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client, db) -> None:
    res = await client.get("/api/v1/pending-requests")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 11) Manager は approve / reject 可能 (admin と同等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manager_can_approve(client, db) -> None:
    admin = await _make_user(db, "pr-mgr-admin@example.com", "admin")
    manager = await _make_user(db, "pr-mgr@example.com", "manager")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(manager),
        json={},
    )
    assert res.status_code == 200, res.text
    assert res.json()["approved_by"] == str(manager.id)


# ---------------------------------------------------------------------------
# 12) DB レベル: pending_requests 行が記録されている
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_row_persisted(client, db) -> None:
    admin = await _make_user(db, "pr-dbrow@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json=_staff_off_payload(staff_id=staff.id),
    )
    assert res.status_code == 201
    pr_id = res.json()["id"]

    # SQLAlchemy で直接確認
    from sqlalchemy import select

    row = await db.scalar(select(PendingRequest).where(PendingRequest.id == pr_id))
    assert row is not None
    assert row.request_type == "staff_off"
    assert row.status == "pending"
    assert row.requester_user_id == admin.id
