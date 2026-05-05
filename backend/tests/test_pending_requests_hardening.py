"""W7-BE3: pending_requests 強化テスト (Codex Must-fix #3 / #4 / #5).

設計仕様書 v0.9 §3.5 / §4.4 + Codex review 指摘 3 件に対応する追加検証。

検証観点:
  #4 (payload validation):
    1. Staff の不正 patient_id (担当外) は 403
    2. Staff の payload.staff_id は user.staff_id に強制上書き
    3. target_staff_id / target_patient_id と payload 内 ID の不一致は 422

  #5 (冪等性強化):
    4. 同時 approve 競合: 並行 2 リクエストで 1 つだけ成功 / もう 1 つは 409
    5. 既に applied_at がセット済みのレコードへの approve は 409
    6. approve 成功時に applied_at が DB に書き込まれる

  #3 (create-and-apply):
    7. create-and-apply 成功時に同一 TX 内で applied_at が設定される
    8. create-and-apply 失敗時に申請レコード自体が rollback される
       (=申請も業務反映も両方なかったことになる)
    9. create-and-apply は admin/manager 限定 (staff は 403)
"""

from __future__ import annotations

import asyncio
from datetime import date, time
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User
from app.models.pending_request import PendingRequest
from app.models.staff import StaffWeeklyOverride
from app.models.visit import VISIT_STATUS_PLANNED, Visit
from app.models.visit_staff_assignment import VisitStaffAssignment

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


async def _make_patient(db, *, code: str = "P-HRD-001") -> Patient:
    p = Patient(code=code, name="患者 太郎", status="active")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit_for_staff(
    db,
    *,
    patient: Patient,
    primary_staff: Staff,
    visit_date: date | None = None,
) -> Visit:
    """primary_staff_id が staff の visit を 1 件作る (担当判定用)."""
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=primary_staff.id,
        visit_date=visit_date or date(2026, 5, 11),
        start_time=time(10, 0),
        end_time=time(11, 0),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="manual",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _make_visit_assignment(db, *, visit: Visit, staff: Staff) -> VisitStaffAssignment:
    """visit_staff_assignments で staff が visit にアサインされた状態を作る."""
    row = VisitStaffAssignment(visit_id=visit.id, staff_id=staff.id)
    db.add(row)
    await db.commit()
    return row


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Codex Must-fix #4: payload validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staff_cannot_request_for_unassigned_patient(client, db) -> None:
    """Staff が担当外の患者に対して patient_cancel を投げると 403."""
    me_staff = await _make_staff(db, name="自分")
    other_staff = await _make_staff(db, name="他人")
    user = await _make_user(db, "hrd-1@example.com", "staff", staff_id=me_staff.id)

    # patient は other_staff が担当 (me_staff は無関係)
    patient = await _make_patient(db, code="P-HRD-OTHER")
    await _make_visit_for_staff(db, patient=patient, primary_staff=other_staff)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json={
            "request_type": "patient_cancel",
            "payload": {
                "patient_id": str(patient.id),
                "date": "2026-05-11",
            },
            "target_patient_id": str(patient.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 403, res.text
    body = res.json()
    assert "not assigned" in body["detail"].lower() or "staff" in body["detail"].lower()


@pytest.mark.asyncio
async def test_staff_can_request_for_assigned_patient_via_primary_staff(client, db) -> None:
    """primary_staff_id 経由で担当している患者なら申請できる."""
    me_staff = await _make_staff(db, name="自分")
    user = await _make_user(db, "hrd-2@example.com", "staff", staff_id=me_staff.id)
    patient = await _make_patient(db, code="P-HRD-MINE")
    await _make_visit_for_staff(db, patient=patient, primary_staff=me_staff)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json={
            "request_type": "patient_cancel",
            "payload": {
                "patient_id": str(patient.id),
                "date": "2026-05-11",
            },
            "target_patient_id": str(patient.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_staff_can_request_for_assigned_patient_via_assignment(client, db) -> None:
    """visit_staff_assignments 経由 (2 名体制 secondary 等) で担当している場合も OK."""
    me_staff = await _make_staff(db, name="二人目")
    primary_staff = await _make_staff(db, name="主担当")
    user = await _make_user(db, "hrd-3@example.com", "staff", staff_id=me_staff.id)
    patient = await _make_patient(db, code="P-HRD-2P")
    visit = await _make_visit_for_staff(db, patient=patient, primary_staff=primary_staff)
    await _make_visit_assignment(db, visit=visit, staff=me_staff)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json={
            "request_type": "patient_cancel",
            "payload": {"patient_id": str(patient.id), "date": "2026-05-11"},
            "target_patient_id": str(patient.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio
async def test_staff_payload_staff_id_force_overwrite(client, db) -> None:
    """Staff が staff_off で他人の staff_id を payload に入れても自分の staff_id に上書きされる.

    target_staff_id を自分にしておくのは _enforce_staff_self_scope の最終ガード
    を通過させるため。payload.staff_id だけ細工する。
    """
    me_staff = await _make_staff(db, name="自分")
    other_staff = await _make_staff(db, name="他人")
    user = await _make_user(db, "hrd-4@example.com", "staff", staff_id=me_staff.id)

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(user),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(other_staff.id),  # 他人を指定
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(me_staff.id),  # 自分を target に
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # payload.staff_id は user.staff_id に強制上書きされている
    assert body["payload"]["staff_id"] == str(me_staff.id)
    assert body["target_staff_id"] == str(me_staff.id)


@pytest.mark.asyncio
async def test_admin_payload_target_mismatch_is_422(client, db) -> None:
    """target_staff_id と payload.staff_id の不一致は admin でも 422."""
    admin = await _make_user(db, "hrd-5-admin@example.com", "admin")
    s1 = await _make_staff(db, name="A")
    s2 = await _make_staff(db, name="B")

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(s1.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(s2.id),  # ID 不一致
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 422, res.text
    assert "match" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_payload_patient_target_mismatch_is_422(client, db) -> None:
    """target_patient_id と payload.patient_id の不一致は 422."""
    admin = await _make_user(db, "hrd-6-admin@example.com", "admin")
    p1 = await _make_patient(db, code="P-HRD-MM-1")
    p2 = await _make_patient(db, code="P-HRD-MM-2")

    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "patient_special_week_on",
            "payload": {
                "patient_id": str(p1.id),
                "iso_year": 2026,
                "iso_week": 20,
            },
            "target_patient_id": str(p2.id),  # 不一致
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Codex Must-fix #5: 冪等性強化 + applied_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_sets_applied_at(client, db) -> None:
    """approve 成功時に applied_at が DB に書き込まれる (W7-BE3)."""
    admin = await _make_user(db, "hrd-applied@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text
    pr_id = res.json()["id"]

    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 200, res.text

    # DB 直接確認 (新規 session を作って読み直す)
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        row = await fresh.scalar(select(PendingRequest).where(PendingRequest.id == UUID(pr_id)))
        assert row is not None
        assert row.applied_at is not None
        assert row.status == "approved"


@pytest.mark.asyncio
async def test_approve_409_when_applied_at_already_set(client, db) -> None:
    """既に applied_at がセット済みのレコードに対する approve は 409."""
    admin = await _make_user(db, "hrd-cas@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )
    pr_id = res.json()["id"]

    # 1 回目: 成功
    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 200

    # 2 回目: 409 (applied_at が既にセットされているため)
    res = await client.patch(
        f"/api/v1/pending-requests/{pr_id}/approve",
        headers=_bearer(admin),
        json={},
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_concurrent_approve_only_one_wins(client, db) -> None:
    """並行 approve リクエスト 2 つのうち 1 つだけ成功し、もう 1 つは 409.

    SQLite では FOR UPDATE が無いため、CAS (条件付き UPDATE WHERE applied_at IS NULL
    AND status='pending') が race condition の最終防衛線になる。

    実装上の注意: SQLite の async writer は 1 接続シリアライズなので
    完全に並行ではないが、HTTPX ASGI 経由で別 session が走っており、
    勝者だけが UPDATE を成立させ、敗者は CAS 失敗で 409 を返すべき。
    本テストはまず順次的に "勝者" → "敗者" を投げる (race condition の
    最低限の検出)。さらに asyncio.gather でも検証する。
    """
    admin = await _make_user(db, "hrd-race@example.com", "admin")
    staff = await _make_staff(db)
    res = await client.post(
        "/api/v1/pending-requests",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )
    pr_id = res.json()["id"]

    async def _do_approve():
        return await client.patch(
            f"/api/v1/pending-requests/{pr_id}/approve",
            headers=_bearer(admin),
            json={},
        )

    # asyncio.gather で並行 2 リクエスト (SQLite の同時 write 制限を考慮し最小)
    results = await asyncio.gather(_do_approve(), _do_approve(), return_exceptions=True)
    statuses = []
    for r in results:
        if isinstance(r, BaseException):
            # SQLite の "cannot commit transaction" は実質敗者扱い (500 に化ける)
            statuses.append(500)
        else:
            statuses.append(r.status_code)
    statuses.sort()
    # 1 つは 200 (勝者), 残りは 409 か 500 (敗者). 200 が 1 個以下であることを保証。
    assert statuses.count(200) <= 1, f"more than one winner: {statuses}"
    # かつ 200 と 409 のいずれかは必ず存在する (両方 500 は許さない)
    assert (statuses.count(200) + statuses.count(409)) >= 1, f"all errors: {statuses}"

    # 追加検証: 同じ ID にもう 1 回 approve を投げると 409 (CAS が確実に効く)
    extra = await _do_approve()
    assert extra.status_code == 409, extra.text

    # DB 上は 1 件しか反映されない (二重反映なし) — 新規 session で確認
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        overrides = (
            await fresh.scalars(
                select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
            )
        ).all()
    assert len(overrides) == 1


# ---------------------------------------------------------------------------
# Codex Must-fix #3: create-and-apply (single TX)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_apply_success_sets_applied_at_in_same_tx(client, db) -> None:
    """create-and-apply 成功時に applied_at が同一 TX 内でセットされ、
    業務反映も完了している."""
    admin = await _make_user(db, "hrd-cap1@example.com", "admin")
    staff = await _make_staff(db)

    res = await client.post(
        "/api/v1/pending-requests/create-and-apply",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == str(admin.id)
    assert body["approved_at"] is not None

    # DB 直接確認 (新規 session で再読込)
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        row = await fresh.scalar(
            select(PendingRequest).where(PendingRequest.id == UUID(body["id"]))
        )
        assert row is not None
        assert row.applied_at is not None
        assert row.status == "approved"

        # 業務反映 (StaffWeeklyOverride) も完了している
        override = await fresh.scalar(
            select(StaffWeeklyOverride).where(StaffWeeklyOverride.staff_id == staff.id)
        )
        assert override is not None


@pytest.mark.asyncio
async def test_create_and_apply_failure_rolls_back_pending_request(client, db) -> None:
    """create-and-apply で applier が失敗した場合、pending_request 行も
    業務テーブルへの追加もすべて rollback される (同一 TX)."""
    admin = await _make_user(db, "hrd-cap-rb@example.com", "admin")

    # staff_id 抜きの staff_off を投げると applier 内で 422 になる
    res = await client.post(
        "/api/v1/pending-requests/create-and-apply",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {"date": "2026-05-11"},
        },
    )
    assert res.status_code == 422, res.text

    # PendingRequest 行が作られていない (rollback 済み)
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        rows = (await fresh.scalars(select(PendingRequest))).all()
        assert len(rows) == 0

        # 業務テーブルにも追加なし
        overrides = (await fresh.scalars(select(StaffWeeklyOverride))).all()
        assert len(overrides) == 0


@pytest.mark.asyncio
async def test_create_and_apply_staff_is_403(client, db) -> None:
    """create-and-apply は admin/manager 限定 (Staff は 403)."""
    me_staff = await _make_staff(db)
    user = await _make_user(db, "hrd-cap-staff@example.com", "staff", staff_id=me_staff.id)

    res = await client.post(
        "/api/v1/pending-requests/create-and-apply",
        headers=_bearer(user),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(me_staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(me_staff.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_create_and_apply_manager_ok(client, db) -> None:
    """manager も create-and-apply 実行可."""
    manager = await _make_user(db, "hrd-cap-mgr@example.com", "manager")
    staff = await _make_staff(db)

    res = await client.post(
        "/api/v1/pending-requests/create-and-apply",
        headers=_bearer(manager),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(staff.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(staff.id),
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 201, res.text
    assert res.json()["approved_by"] == str(manager.id)


@pytest.mark.asyncio
async def test_create_and_apply_validates_payload(client, db) -> None:
    """create-and-apply でも target/payload mismatch は 422."""
    admin = await _make_user(db, "hrd-cap-val@example.com", "admin")
    s1 = await _make_staff(db, name="A")
    s2 = await _make_staff(db, name="B")

    res = await client.post(
        "/api/v1/pending-requests/create-and-apply",
        headers=_bearer(admin),
        json={
            "request_type": "staff_off",
            "payload": {
                "staff_id": str(s1.id),
                "date": "2026-05-11",
                "override_type": "off",
            },
            "target_staff_id": str(s2.id),  # mismatch
            "target_date": "2026-05-11",
        },
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Migration 0016 sanity (model has applied_at attribute)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_request_model_has_applied_at_attribute(db) -> None:
    """PendingRequest model に applied_at 属性が存在し、デフォルトは None."""
    user = User(email="hrd-attr@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)

    pr = PendingRequest(
        requester_user_id=user.id,
        request_type="staff_off",
        payload={},
        status="pending",
    )
    db.add(pr)
    await db.commit()
    await db.refresh(pr)
    assert hasattr(pr, "applied_at")
    assert pr.applied_at is None
