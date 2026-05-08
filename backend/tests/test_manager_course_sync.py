"""W16-BE4: マネージャー追加/退職時の M course_template 自動同期テスト.

検証観点:
  1. 新規 manager 追加 → M course_template が自動 INSERT
  2. manager 退職 (status='retired' or soft-delete) → M course_template が論理削除
  3. 同 office に 2 manager → M, M2 が作成される
  4. 既に M 存在 → skip (重複 INSERT しない)
  5. role='staff' 追加では M は作成されない
  6. PendingRequestApplier 経由 (staff_create / staff_status_update) でも同期する
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Staff, User
from app.models.course_template import CourseTemplate
from app.models.pending_request import PendingRequest
from app.services.manager_course_sync import (
    sync_manager_course_templates,
)
from app.services.pending_request_applier import PendingRequestApplier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_office(db, *, name: str = "本店(稲毛)") -> Office:
    office = Office(name=name, lat=35.6383, lng=140.1041)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    return office


async def _count_active_m_templates(db, *, office_id) -> list[CourseTemplate]:
    rows = (
        await db.scalars(
            select(CourseTemplate).where(
                CourseTemplate.office_id == office_id,
                CourseTemplate.deleted_at.is_(None),
                CourseTemplate.label.like("M%"),
            )
        )
    ).all()
    # M / M2 / .. のみ抽出
    return [
        r for r in rows if r.label == "M" or (r.label.startswith("M") and r.label[1:].isdigit())
    ]


# ---------------------------------------------------------------------------
# 1) sync_manager_course_templates 単体テスト
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_m_template_for_first_manager(db) -> None:
    """active manager 1 名 → M ラベルが INSERT される."""
    office = await _make_office(db)
    mgr = Staff(
        code="MGR-01",
        name="川名",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.commit()

    created, deleted = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()

    assert created == 1
    assert deleted == 0

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1
    assert rows[0].label == "M"
    # 標準 capacity (月7/火7/水7/木7/金7/土5/日0)
    assert rows[0].capacity_mon == 7
    assert rows[0].capacity_sat == 5
    assert rows[0].capacity_sun == 0


@pytest.mark.asyncio
async def test_sync_creates_m_and_m2_for_two_managers(db) -> None:
    """同一拠点に active manager 2 名 → M + M2 が作成される."""
    office = await _make_office(db)
    mgr1 = Staff(
        code="MGR-01",
        name="川名",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    mgr2 = Staff(
        code="MGR-02",
        name="第二マネージャー",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add_all([mgr1, mgr2])
    await db.commit()

    created, _ = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()

    assert created == 2
    rows = await _count_active_m_templates(db, office_id=office.id)
    labels = sorted(r.label for r in rows)
    assert labels == ["M", "M2"]


@pytest.mark.asyncio
async def test_sync_skips_when_m_already_exists(db) -> None:
    """既に M 存在 → skip (重複 INSERT しない)."""
    office = await _make_office(db)
    mgr = Staff(
        code="MGR-01",
        name="川名",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.commit()

    # 1 回目: 1 件作成
    c1, d1 = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()
    assert c1 == 1

    # 2 回目: 何もしない
    c2, d2 = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()
    assert c2 == 0
    assert d2 == 0

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_sync_soft_deletes_m_when_manager_retired(db) -> None:
    """manager 退職 (status='retired') → M course_template が論理削除される."""
    office = await _make_office(db)
    mgr = Staff(
        code="MGR-01",
        name="川名",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.commit()

    # 初期状態: M 1 件
    await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1

    # 退職
    mgr.status = "retired"
    await db.commit()

    # 再 sync で M が論理削除される
    c, d = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()

    assert c == 0
    assert d == 1

    active_rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(active_rows) == 0

    # 論理削除済み行 (deleted_at IS NOT NULL) は残る
    all_rows = (
        await db.scalars(
            select(CourseTemplate).where(
                CourseTemplate.office_id == office.id,
                CourseTemplate.label == "M",
            )
        )
    ).all()
    assert len(all_rows) == 1
    assert all_rows[0].deleted_at is not None


@pytest.mark.asyncio
async def test_sync_no_op_when_no_managers(db) -> None:
    """active manager 0 名 → 何もしない."""
    office = await _make_office(db)
    # role='staff' のみ
    s1 = Staff(
        code="S-01",
        name="一般",
        role="staff",
        status="active",
        primary_office_id=office.id,
    )
    db.add(s1)
    await db.commit()

    c, d = await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()
    assert c == 0
    assert d == 0

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# 2) HTTP API 経由: POST /staff (manager 作成) → M 自動 INSERT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_staff_manager_creates_m_template(client, db) -> None:
    admin = await _make_user(db, "mc-1@example.com", "admin")
    office = await _make_office(db)

    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={
            "code": "MGR-01",
            "name": "川名 千恵",
            "role": "manager",
            "status": "active",
            "primary_office_id": str(office.id),
        },
    )
    assert res.status_code == 201, res.text

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1
    assert rows[0].label == "M"


@pytest.mark.asyncio
async def test_post_staff_role_staff_does_not_create_m(client, db) -> None:
    admin = await _make_user(db, "mc-2@example.com", "admin")
    office = await _make_office(db)

    res = await client.post(
        "/api/v1/staff",
        headers=_bearer(admin),
        json={
            "code": "S-01",
            "name": "一般スタッフ",
            "role": "staff",
            "status": "active",
            "primary_office_id": str(office.id),
        },
    )
    assert res.status_code == 201, res.text

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# 3) PendingRequestApplier 経由 (staff_create / staff_status_update)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_applier_staff_create_manager_syncs_m(db) -> None:
    """PendingRequestApplier._apply_staff_create で manager 追加時に sync が走る."""
    office = await _make_office(db)

    pr = PendingRequest(
        request_type="staff_create",
        scope="permanent",
        status="pending",
        payload={
            "name": "川名 千恵",
            "role": "manager",
            "status": "active",
            "primary_office_id": str(office.id),
        },
        requester_user_id=uuid4(),
    )
    db.add(pr)
    await db.commit()

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_pending_applier_status_update_to_retired_syncs_m(db) -> None:
    """PendingRequestApplier._apply_staff_status_update で manager 退職時に sync が走る."""
    office = await _make_office(db)
    mgr = Staff(
        code="MGR-01",
        name="川名",
        role="manager",
        status="active",
        primary_office_id=office.id,
    )
    db.add(mgr)
    await db.commit()

    # 初期 M を作成
    await sync_manager_course_templates(db, office_id=office.id)
    await db.commit()
    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 1

    # status_update で 'retired' に
    pr = PendingRequest(
        request_type="staff_status_update",
        scope="permanent",
        status="pending",
        target_staff_id=mgr.id,
        payload={
            "staff_id": str(mgr.id),
            "status": "retired",
        },
        requester_user_id=uuid4(),
    )
    db.add(pr)
    await db.commit()

    applier = PendingRequestApplier()
    await applier.apply(db, pr)
    await db.commit()

    # M が論理削除される
    rows = await _count_active_m_templates(db, office_id=office.id)
    assert len(rows) == 0
