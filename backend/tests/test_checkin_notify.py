"""QR 訪問チェックイン Phase 5-2 のテスト — アプリ内通知 producer.

- 場所違い (mismatch) checkin → active admin/manager に 1 行ずつ通知。再 checkin で
  重複しない (冪等)。
- 一致 (match) checkin は通知しない。
- check-missing は未訪問のみ通知 / reviewed 除外 / 到着済み除外 / 冪等。
- 管理 API のロール (admin のみ)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import Notification, Patient, Staff, User, Visit, VisitCheckin, VisitReview
from app.services.checkin.notify import (
    NOTIFY_MISMATCH,
    NOTIFY_MISSING,
    run_check_missing,
)

JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_user(db, email: str, role: str, *, staff_id=None, deleted=False) -> User:
    user = User(
        email=email,
        password_hash=hash_password("x"),
        role=role,
        staff_id=staff_id,
        deleted_at=datetime.now(JST) if deleted else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_staff_user(db, email: str) -> tuple[Staff, User]:
    staff = Staff(name="担当ヘルパー")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = await _make_user(db, email, "staff", staff_id=staff.id)
    return staff, user


async def _make_patient(db, code: str, *, lat=None, lng=None) -> Patient:
    p = Patient(code=code, name="利用者", lat=lat, lng=lng)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(db, patient_id, staff_id, *, visit_date=None, start=time(9, 0)) -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=visit_date or _today_jst(),
        start_time=start,
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


async def _count_notifications(db, *, reference_id, reference_type) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.reference_id == reference_id,
                Notification.reference_type == reference_type,
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# mismatch (イベント駆動 / checkin 記録時)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mismatch_checkin_notifies_admin_manager_once(client, db) -> None:
    admin = await _make_user(db, "n-admin@example.com", "admin")
    manager = await _make_user(db, "n-manager@example.com", "manager")
    await _make_user(db, "n-deleted@example.com", "admin", deleted=True)  # 除外される
    staff, user = await _make_staff_user(db, "n-staff@example.com")
    # 患者宅 (35.0,139.0) から ~9km 離れた座標で打刻 → mismatch (> review_m=300)。
    p = await _make_patient(db, "N-MM", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.1},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["match_status"] == "mismatch"

    # admin + manager に 1 行ずつ (= 2)。論理削除 admin は対象外。
    assert (
        await _count_notifications(db, reference_id=visit.id, reference_type=NOTIFY_MISMATCH) == 2
    )
    for u in (admin, manager):
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == u.id,
                    Notification.reference_id == visit.id,
                    Notification.type == NOTIFY_MISMATCH,
                )
            )
            or 0
        )
        assert cnt == 1, f"user {u.email} should have exactly 1 notification"

    # 再 checkin (再び mismatch) しても重複しない (冪等)。
    res2 = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.1},
    )
    assert res2.status_code == 200, res2.text
    assert (
        await _count_notifications(db, reference_id=visit.id, reference_type=NOTIFY_MISMATCH) == 2
    )

    await db.rollback()


@pytest.mark.asyncio
async def test_match_checkin_does_not_notify(client, db) -> None:
    await _make_user(db, "m-admin@example.com", "admin")
    staff, user = await _make_staff_user(db, "m-staff@example.com")
    p = await _make_patient(db, "N-OK", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["match_status"] == "match"

    total = int(await db.scalar(select(func.count()).select_from(Notification)) or 0)
    assert total == 0

    await db.rollback()


# ---------------------------------------------------------------------------
# missing (時間ベース / 管理 API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_missing_notifies_only_missing_and_is_idempotent(db) -> None:
    admin = await _make_user(db, "cm-admin@example.com", "admin")
    manager = await _make_user(db, "cm-manager@example.com", "manager")
    staff, _ = await _make_staff_user(db, "cm-staff@example.com")

    # (1) 未訪問: 到着なし・予定 09:00 + grace 超過。
    p_missing = await _make_patient(db, "CM-MISS")
    v_missing = await _make_visit(db, p_missing.id, staff.id, start=time(9, 0))

    # (2) 到着済み: arrival checkin あり → 除外。
    p_arrived = await _make_patient(db, "CM-ARR")
    v_arrived = await _make_visit(db, p_arrived.id, staff.id, start=time(9, 0))
    db.add(
        VisitCheckin(
            visit_id=v_arrived.id,
            patient_id=p_arrived.id,
            staff_id=staff.id,
            kind="arrival",
            scanned_at=datetime.now(JST),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )

    # (3) reviewed 済み 未訪問 → 除外。
    p_reviewed = await _make_patient(db, "CM-REV")
    v_reviewed = await _make_visit(db, p_reviewed.id, staff.id, start=time(9, 0))
    db.add(VisitReview(visit_id=v_reviewed.id, reviewed_by=admin.id, comment="確認済"))
    await db.commit()

    # now = 当日 12:00 JST (grace=20 を超過)。
    now = datetime.combine(_today_jst(), time(12, 0), tzinfo=JST)
    result = await run_check_missing(db, now=now)

    assert result["locked"] is False
    assert result["missing"] == 1  # 未訪問は 1 件のみ
    assert result["created"] == 2  # admin + manager に 1 行ずつ

    # 未訪問 visit のみ通知される。
    assert (
        await _count_notifications(db, reference_id=v_missing.id, reference_type=NOTIFY_MISSING)
        == 2
    )
    assert (
        await _count_notifications(db, reference_id=v_arrived.id, reference_type=NOTIFY_MISSING)
        == 0
    )
    assert (
        await _count_notifications(db, reference_id=v_reviewed.id, reference_type=NOTIFY_MISSING)
        == 0
    )
    # ターゲットは admin/manager 両方。
    assert {manager.id, admin.id} == set(
        (
            await db.scalars(
                select(Notification.user_id).where(Notification.reference_id == v_missing.id)
            )
        ).all()
    )

    # 冪等: 再実行で新規生成 0。
    result2 = await run_check_missing(db, now=now)
    assert result2["created"] == 0
    assert (
        await _count_notifications(db, reference_id=v_missing.id, reference_type=NOTIFY_MISSING)
        == 2
    )


@pytest.mark.asyncio
async def test_check_missing_skips_before_grace(db) -> None:
    await _make_user(db, "bg-admin@example.com", "admin")
    staff, _ = await _make_staff_user(db, "bg-staff@example.com")
    p = await _make_patient(db, "BG-MISS")
    await _make_visit(db, p.id, staff.id, start=time(9, 0))

    # now = 09:05 (grace=20 未満) → まだ未訪問ではない。
    now = datetime.combine(_today_jst(), time(9, 5), tzinfo=JST)
    result = await run_check_missing(db, now=now)
    assert result["missing"] == 0
    assert result["created"] == 0


@pytest.mark.asyncio
async def test_arrival_checkin_resolves_missing_notifications(client, db) -> None:
    """遅刻→到着 (arrival checkin) で当該 visit の未訪問通知が全ユーザー分消える."""
    admin = await _make_user(db, "rs-admin@example.com", "admin")
    manager = await _make_user(db, "rs-manager@example.com", "manager")
    staff, staff_user = await _make_staff_user(db, "rs-staff@example.com")
    # 一致打刻になるよう患者宅座標を持たせる (mismatch 通知を混入させない)。
    p = await _make_patient(db, "RS-MISS", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, start=time(9, 0))

    # 先に cron が未訪問通知を生成 (admin + manager の 2 行)。
    now = datetime.combine(_today_jst(), time(12, 0), tzinfo=JST)
    result = await run_check_missing(db, now=now)
    assert result["created"] == 2
    assert await _count_notifications(db, reference_id=visit.id, reference_type=NOTIFY_MISSING) == 2

    # 遅れて到着 checkin → 同一 transaction で missing 通知が解消される。
    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(staff_user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["match_status"] == "match"

    # 未訪問通知は全ユーザー分消える。
    assert await _count_notifications(db, reference_id=visit.id, reference_type=NOTIFY_MISSING) == 0
    for u in (admin, manager):
        cnt = int(
            await db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == u.id,
                    Notification.reference_id == visit.id,
                    Notification.type == NOTIFY_MISSING,
                )
            )
            or 0
        )
        assert cnt == 0, f"user {u.email} should have no missing notification left"

    await db.rollback()


@pytest.mark.asyncio
async def test_check_missing_pair_correction(db) -> None:
    """同住所・同時刻ペア: A 到着中は B に未訪問通知を作らず、起点超過後に作る."""
    # admin + manager が通知ターゲット (created == 2 で検証)。
    await _make_user(db, "pc-admin@example.com", "admin")
    await _make_user(db, "pc-manager@example.com", "manager")
    staff, _ = await _make_staff_user(db, "pc-staff@example.com")

    # A / B 同住所 (同座標)・同 staff・同時刻 (09:00–10:00, 所要 60分)・別患者。
    pa = await _make_patient(db, "PC-A", lat=35.0, lng=139.0)
    pb = await _make_patient(db, "PC-B", lat=35.0, lng=139.0)
    va = await _make_visit(db, pa.id, staff.id, start=time(9, 0))
    vb = await _make_visit(db, pb.id, staff.id, start=time(9, 0))
    # A は 09:00 到着 (退出なし)。A 完了見込 = 09:00 + 60 = 10:00。
    db.add(
        VisitCheckin(
            visit_id=va.id,
            patient_id=pa.id,
            staff_id=staff.id,
            kind="arrival",
            scanned_at=datetime.combine(_today_jst(), time(9, 0), tzinfo=JST).astimezone(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    # (1) now = 09:40 (予定 + grace 超過だが 補正後起点 10:00 + grace 内)。
    #     B は未訪問通知されない (A の完了待ち)。
    now_wait = datetime.combine(_today_jst(), time(9, 40), tzinfo=JST)
    res_wait = await run_check_missing(db, now=now_wait)
    assert res_wait["missing"] == 0
    assert await _count_notifications(db, reference_id=vb.id, reference_type=NOTIFY_MISSING) == 0

    # (2) now = 10:25 (補正後起点 10:00 + grace(20) = 10:20 を超過)。B は未訪問通知される。
    now_over = datetime.combine(_today_jst(), time(10, 25), tzinfo=JST)
    res_over = await run_check_missing(db, now=now_over)
    assert res_over["missing"] == 1
    assert res_over["created"] == 2  # admin + manager
    assert await _count_notifications(db, reference_id=vb.id, reference_type=NOTIFY_MISSING) == 2


# ---------------------------------------------------------------------------
# 管理 API のロール (admin のみ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_missing_api_requires_admin(client, db) -> None:
    _staff, staff_user = await _make_staff_user(db, "rbac-staff@example.com")
    admin = await _make_user(db, "rbac-admin@example.com", "admin")

    res_staff = await client.post(
        "/api/v1/admin/checkin/check-missing", headers=_bearer(staff_user)
    )
    assert res_staff.status_code == 403, res_staff.text

    res_admin = await client.post("/api/v1/admin/checkin/check-missing", headers=_bearer(admin))
    assert res_admin.status_code == 200, res_admin.text
    body = res_admin.json()
    assert set(body.keys()) == {"locked", "scanned", "missing", "created"}

    await db.rollback()
