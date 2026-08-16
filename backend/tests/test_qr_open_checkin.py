"""QR 打刻の開放 (担当外 = 代行 / 予定外訪問) のテスト — Phase B (BE).

正典設計書: ``docs/plans/qr-open-checkin-design.md`` §3 / §4 / §6。

* **QR capability 分岐** (§4-2): 担当外でも「その visit の患者」の QR を持って
  いれば打刻・詳細取得が通る。トークン無し = 404 / 別患者 = 409 / 失効 = 410。
  no-show には適用しない (担当専用のまま)。
* **予定外打刻** (§4-3): ``POST /visits/adhoc-checkin`` が visit 生成 + 到着打刻 +
  通知を 1 TX で行う。生成値・二重生成ガード・退出時の end_time 更新。
* **通知 3 種** (§4-4): 代行 / 予定外 / NG 交差を admin へ冪等通知
  (reference_id = visit.id で非 NULL)。
* **モニター合成** (§6): 実績スタッフ・代行判定・予定外専用行・アラート。

TX 後始末: 各テストの終端で ``await db.rollback()`` (aiosqlite 共有コネクションの
順序依存フレーク対策 / 2026-08-11 引き継ぎ §5)。リクエスト間に test session から
INSERT はしない (事前 seed か API 経由)。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.api.v1 import visits as visits_api
from app.core.security import create_access_token, hash_password
from app.models import (
    Notification,
    Office,
    Patient,
    PatientFixedVisit,
    PatientNgStaff,
    RevokedQrToken,
    Staff,
    User,
    Visit,
    VisitCheckin,
    VisitReview,
    VisitStaffAssignment,
)
from app.services.checkin.monitor import (
    ALERT_NONE,
    ALERT_REVIEW,
    UNPLANNED_ROW_LABEL,
    build_monitor,
)
from app.services.checkin.notify import (
    NOTIFY_MISSING,
    NOTIFY_NG_STAFF,
    NOTIFY_SUBSTITUTE,
    NOTIFY_UNPLANNED,
)
from app.services.scheduling.auto_allocator_v2 import (
    _RESET_DELETABLE_SOURCES,
    _RESET_DELETABLE_STATUSES,
)

JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff_user(
    db, email: str, *, staff_name: str = "ヘルパー", sex: str | None = None
) -> tuple[Staff, User]:
    staff = Staff(name=staff_name, sex=sex)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = User(
        email=email,
        password_hash=hash_password("x"),
        role="staff",
        staff_id=staff.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return staff, user


async def _make_admin(db, email: str) -> User:
    user = User(email=email, password_hash=hash_password("x"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(db, code: str, **kwargs) -> Patient:
    p = Patient(code=code, name=kwargs.pop("name", f"利用者{code}"), **kwargs)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    patient_id,
    staff_id,
    *,
    visit_date=None,
    start=time(9, 0),
    end=time(10, 0),
    status="planned",
    is_unplanned=False,
) -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=visit_date or _today_jst(),
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        is_unplanned=is_unplanned,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


async def _notification_count(db, reference_type: str, visit_id=None) -> int:
    stmt = select(Notification).where(Notification.reference_type == reference_type)
    if visit_id is not None:
        stmt = stmt.where(Notification.reference_id == visit_id)
    return len((await db.scalars(stmt)).all())


# ---------------------------------------------------------------------------
# §4-2 QR capability 分岐 — 担当外の打刻 / 詳細取得
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_checkin_with_qr_token_succeeds(client, db) -> None:
    """担当外でも現地 QR があれば到着打刻が通る。予定の担当は書き換えない (決定#4)."""
    owner, _ = await _make_staff_user(db, "sub-1-owner@example.com", staff_name="予定 太郎")
    me_staff, me = await _make_staff_user(db, "sub-1-me@example.com", staff_name="代行 花子")
    p = await _make_patient(db, "SUB-1", qr_token="sub-tok-1", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(me),
        json={"qr_token": "sub-tok-1", "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 予定担当は不変 (実績は checkin 行が持つ)。
    assert body["primary_staff_id"] == str(owner.id)
    assert body["status"] == "in_progress"
    assert body["latest_checkin"]["kind"] == "arrival"

    row = await db.scalar(select(VisitCheckin).where(VisitCheckin.visit_id == visit.id))
    assert row.staff_id == me_staff.id
    assert row.checkin_source == "qr"
    await db.rollback()


@pytest.mark.asyncio
async def test_substitute_checkin_without_qr_token_returns_404(client, db) -> None:
    """担当外 + トークン無しは従来どおり 404 (= 担当外は QR 必須・決定#6)."""
    owner, _ = await _make_staff_user(db, "sub-2-owner@example.com")
    _, me = await _make_staff_user(db, "sub-2-me@example.com")
    p = await _make_patient(db, "SUB-2", qr_token="sub-tok-2")
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(me),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 404, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_substitute_checkin_with_other_patients_token_returns_409(client, db) -> None:
    """別患者の QR は 409 (この visit の患者に解決できない)."""
    owner, _ = await _make_staff_user(db, "sub-3-owner@example.com")
    _, me = await _make_staff_user(db, "sub-3-me@example.com")
    p = await _make_patient(db, "SUB-3", qr_token="sub-tok-3")
    await _make_patient(db, "SUB-3B", qr_token="sub-tok-3b")
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(me),
        json={"qr_token": "sub-tok-3b"},
    )
    assert res.status_code == 409, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_substitute_checkin_with_revoked_token_returns_410(client, db) -> None:
    """再発行で失効した旧 QR は 410 (「QR は更新されています」)."""
    owner, _ = await _make_staff_user(db, "sub-4-owner@example.com")
    _, me = await _make_staff_user(db, "sub-4-me@example.com")
    p = await _make_patient(db, "SUB-4", qr_token="sub-tok-4-new")
    visit = await _make_visit(db, p.id, owner.id)
    db.add(RevokedQrToken(patient_id=p.id, token="sub-tok-4-old"))
    await db.commit()

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(me),
        json={"qr_token": "sub-tok-4-old"},
    )
    assert res.status_code == 410, res.text
    assert "QRは更新" in res.json()["detail"]
    await db.rollback()


@pytest.mark.asyncio
async def test_substitute_checkout_with_qr_token_succeeds(client, db) -> None:
    """代行の退出も再スキャン (qr_token) で通る (§4-2「退出は改めて現地で読む」)."""
    owner, _ = await _make_staff_user(db, "sub-5-owner@example.com")
    _, me = await _make_staff_user(db, "sub-5-me@example.com")
    p = await _make_patient(db, "SUB-5", qr_token="sub-tok-5")
    visit = await _make_visit(db, p.id, owner.id, status="in_progress")

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkout",
        headers=_bearer(me),
        json={"qr_token": "sub-tok-5"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    await db.rollback()


@pytest.mark.asyncio
async def test_no_show_does_not_accept_qr_capability(client, db) -> None:
    """no-show は担当専用のまま (QR capability を適用しない・§4-2)."""
    owner, _ = await _make_staff_user(db, "sub-6-owner@example.com")
    _, me = await _make_staff_user(db, "sub-6-me@example.com")
    p = await _make_patient(db, "SUB-6", qr_token="sub-tok-6")
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/no-show",
        headers=_bearer(me),
        json={"qr_token": "sub-tok-6", "reason": "不在"},
    )
    assert res.status_code == 404, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_get_visit_with_qr_token_allows_non_assigned_staff(client, db) -> None:
    """GET /visits/{id}?qr_token=... は担当外でも 200 (読み取りにも同分岐・§4-2)."""
    owner, _ = await _make_staff_user(db, "get-1-owner@example.com")
    _, me = await _make_staff_user(db, "get-1-me@example.com")
    p = await _make_patient(db, "GET-1", qr_token="get-tok-1")
    visit = await _make_visit(db, p.id, owner.id)

    # トークン無しは従来どおり存在秘匿の 404。
    res = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(me))
    assert res.status_code == 404, res.text

    res = await client.get(
        f"/api/v1/visits/{visit.id}", headers=_bearer(me), params={"qr_token": "get-tok-1"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == str(visit.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_get_visit_with_qr_token_header(client, db) -> None:
    """X-QR-Token ヘッダでも capability が通る (クエリはログ / Referer に残るため)."""
    owner, _ = await _make_staff_user(db, "get-1b-owner@example.com")
    _, me = await _make_staff_user(db, "get-1b-me@example.com")
    p = await _make_patient(db, "GET-1B", qr_token="get-tok-1b")
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.get(
        f"/api/v1/visits/{visit.id}",
        headers={**_bearer(me), "X-QR-Token": "get-tok-1b"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == str(visit.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_get_visit_via_qr_capability_is_restricted(client, db) -> None:
    """capability 経由 (担当外) の GET は業務情報を落とす (現地に要る範囲のみ返す)."""
    owner, _ = await _make_staff_user(db, "get-3-owner@example.com", staff_name="予定 太郎")
    assigned, _ = await _make_staff_user(db, "get-3-asn@example.com")
    _, me = await _make_staff_user(db, "get-3-me@example.com")
    p = await _make_patient(
        db,
        "GET-3",
        qr_token="get-tok-3",
        name="秘匿 花子",
        address="千葉県1-2-3",
        lat=35.0,
        lng=139.0,
    )
    visit = await _make_visit(db, p.id, owner.id)
    visit.note = "鍵は郵便受け・家族と要相談"
    visit.kaipoke_id = "KP-9999"
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=assigned.id))
    await db.commit()

    res = await client.get(
        f"/api/v1/visits/{visit.id}", headers={**_bearer(me), "X-QR-Token": "get-tok-3"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 落とすもの (事業所の業務情報)。
    assert body["note"] is None
    assert body["kaipoke_id"] is None
    assert body["staff_assignments"] == []
    assert body["accompaniment"] is None
    # 残すもの (現地判定・確認表示に要る = resolve v2 で開示済みの範囲)。
    assert body["patient_name"] == "秘匿 花子"
    assert body["patient_address"] == "千葉県1-2-3"
    assert body["patient_lat"] == 35.0
    assert body["staff_name"] == "予定 太郎"
    assert body["start_time"] == "09:00:00"
    assert body["status"] == "planned"
    await db.rollback()


@pytest.mark.asyncio
async def test_get_visit_for_assigned_staff_keeps_full_payload(client, db) -> None:
    """担当者本人の GET は従来どおり全量 (絞り込みは capability 経由だけ)."""
    mine, me = await _make_staff_user(db, "get-4-me@example.com")
    p = await _make_patient(db, "GET-4", qr_token="get-tok-4")
    visit = await _make_visit(db, p.id, mine.id)
    visit.note = "申し送りメモ"
    visit.kaipoke_id = "KP-1111"
    await db.commit()

    res = await client.get(
        f"/api/v1/visits/{visit.id}", headers={**_bearer(me), "X-QR-Token": "get-tok-4"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["note"] == "申し送りメモ"
    assert body["kaipoke_id"] == "KP-1111"
    await db.rollback()


@pytest.mark.asyncio
async def test_get_visit_with_other_patients_token_returns_409(client, db) -> None:
    """GET でも別患者トークンは 409 (打刻経路と同じ意味論)."""
    owner, _ = await _make_staff_user(db, "get-2-owner@example.com")
    _, me = await _make_staff_user(db, "get-2-me@example.com")
    p = await _make_patient(db, "GET-2", qr_token="get-tok-2")
    await _make_patient(db, "GET-2B", qr_token="get-tok-2b")
    visit = await _make_visit(db, p.id, owner.id)

    res = await client.get(
        f"/api/v1/visits/{visit.id}", headers=_bearer(me), params={"qr_token": "get-tok-2b"}
    )
    assert res.status_code == 409, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_substitute_arrival_resolves_missing_notification(client, db) -> None:
    """代行の到着で予定 visit の「未訪問」通知が解消される (§7-2 の退行防止)."""
    owner, _ = await _make_staff_user(db, "mis-1-owner@example.com")
    _, me = await _make_staff_user(db, "mis-1-me@example.com")
    admin = await _make_admin(db, "mis-1-admin@example.com")
    p = await _make_patient(db, "MIS-1", qr_token="mis-tok-1")
    visit = await _make_visit(db, p.id, owner.id)
    # cron が先に生成した未訪問通知を事前 seed する (リクエスト間 INSERT を避ける)。
    db.add(
        Notification(
            user_id=admin.id,
            type=NOTIFY_MISSING,
            title="未訪問の可能性",
            body="x",
            reference_type=NOTIFY_MISSING,
            reference_id=visit.id,
        )
    )
    await db.commit()

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(me),
        json={"qr_token": "mis-tok-1"},
    )
    assert res.status_code == 200, res.text
    assert await _notification_count(db, NOTIFY_MISSING, visit.id) == 0
    await db.rollback()


# ---------------------------------------------------------------------------
# §4-4 通知 3 種 (代行 / 予定外 / NG 交差) の発火と冪等
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substitute_notification_is_idempotent(client, db) -> None:
    """代行通知は admin へ 1 件・再打刻しても増えない (reference_id = visit.id)."""
    owner, _ = await _make_staff_user(db, "ntf-1-owner@example.com", staff_name="予定 太郎")
    _, me = await _make_staff_user(db, "ntf-1-me@example.com", staff_name="代行 花子")
    await _make_admin(db, "ntf-1-admin@example.com")
    p = await _make_patient(db, "NTF-1", qr_token="ntf-tok-1")
    visit = await _make_visit(db, p.id, owner.id)

    for _ in range(2):
        res = await client.post(
            f"/api/v1/visits/{visit.id}/checkin",
            headers=_bearer(me),
            json={"qr_token": "ntf-tok-1"},
        )
        assert res.status_code == 200, res.text

    rows = (
        await db.scalars(
            select(Notification).where(Notification.reference_type == NOTIFY_SUBSTITUTE)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].reference_id == visit.id  # 非 NULL (IS NULL 展開の罠を踏まない)
    assert "代行 花子" in rows[0].body
    assert "予定 太郎" in rows[0].body
    await db.rollback()


@pytest.mark.asyncio
async def test_assigned_staff_checkin_does_not_notify_substitute(client, db) -> None:
    """担当本人の打刻では代行通知を出さない."""
    mine, me = await _make_staff_user(db, "ntf-2-me@example.com")
    await _make_admin(db, "ntf-2-admin@example.com")
    p = await _make_patient(db, "NTF-2", qr_token="ntf-tok-2")
    visit = await _make_visit(db, p.id, mine.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(me), json={"qr_token": "ntf-tok-2"}
    )
    assert res.status_code == 200, res.text
    assert await _notification_count(db, NOTIFY_SUBSTITUTE) == 0
    await db.rollback()


@pytest.mark.asyncio
async def test_ng_staff_checkin_notifies_and_is_idempotent(client, db) -> None:
    """NG スタッフの打刻は記録しつつ通知する (決定#5: 止めない)."""
    ng_staff, me = await _make_staff_user(db, "ntf-3-me@example.com", staff_name="NG 次郎")
    await _make_admin(db, "ntf-3-admin@example.com")
    p = await _make_patient(db, "NTF-3", qr_token="ntf-tok-3")
    visit = await _make_visit(db, p.id, ng_staff.id)
    db.add(PatientNgStaff(patient_id=p.id, staff_id=ng_staff.id, note="相性"))
    await db.commit()

    for _ in range(2):
        res = await client.post(
            f"/api/v1/visits/{visit.id}/checkin",
            headers=_bearer(me),
            json={"qr_token": "ntf-tok-3"},
        )
        assert res.status_code == 200, res.text

    rows = (
        await db.scalars(select(Notification).where(Notification.reference_type == NOTIFY_NG_STAFF))
    ).all()
    assert len(rows) == 1
    assert "NGスタッフ" in rows[0].title
    await db.rollback()


@pytest.mark.asyncio
async def test_sex_restriction_checkin_notifies_ng_staff_type(client, db) -> None:
    """性別制限に反するスタッフの打刻も同じ種別で通知する (§4-4 の NG 交差)."""
    male_staff, me = await _make_staff_user(db, "ntf-4-me@example.com", sex="male")
    await _make_admin(db, "ntf-4-admin@example.com")
    p = await _make_patient(db, "NTF-4", qr_token="ntf-tok-4", sex_restriction="female_only")
    visit = await _make_visit(db, p.id, male_staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(me), json={"qr_token": "ntf-tok-4"}
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(select(Notification).where(Notification.reference_type == NOTIFY_NG_STAFF))
    ).all()
    assert len(rows) == 1
    assert "性別制限外" in rows[0].title
    await db.rollback()


@pytest.mark.asyncio
async def test_checkout_only_substitute_is_notified(client, db) -> None:
    """到着は担当本人・**退出だけ代行**でも代行通知が出る (退出経路にも配線)."""
    owner, owner_user = await _make_staff_user(
        db, "ntf-6-owner@example.com", staff_name="担当 太郎"
    )
    _, me = await _make_staff_user(db, "ntf-6-me@example.com", staff_name="代行 花子")
    await _make_admin(db, "ntf-6-admin@example.com")
    p = await _make_patient(db, "NTF-6", qr_token="ntf-tok-6")
    visit = await _make_visit(db, p.id, owner.id)

    arrival = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(owner_user),
        json={"qr_token": "ntf-tok-6"},
    )
    assert arrival.status_code == 200, arrival.text
    assert await _notification_count(db, NOTIFY_SUBSTITUTE) == 0

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkout",
        headers=_bearer(me),
        json={"qr_token": "ntf-tok-6"},
    )
    assert res.status_code == 200, res.text
    rows = (
        await db.scalars(
            select(Notification).where(Notification.reference_type == NOTIFY_SUBSTITUTE)
        )
    ).all()
    assert len(rows) == 1
    assert "代行 花子" in rows[0].body
    await db.rollback()


@pytest.mark.asyncio
async def test_clean_checkin_creates_no_anomaly_notifications(client, db) -> None:
    """通常の担当打刻では 3 種とも通知しない (誤検知で管理者を疲弊させない)."""
    mine, me = await _make_staff_user(db, "ntf-5-me@example.com")
    await _make_admin(db, "ntf-5-admin@example.com")
    p = await _make_patient(db, "NTF-5", qr_token="ntf-tok-5")
    visit = await _make_visit(db, p.id, mine.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(me), json={"qr_token": "ntf-tok-5"}
    )
    assert res.status_code == 200, res.text
    for ref_type in (NOTIFY_SUBSTITUTE, NOTIFY_UNPLANNED, NOTIFY_NG_STAFF):
        assert await _notification_count(db, ref_type) == 0
    await db.rollback()


# ---------------------------------------------------------------------------
# §4-3 予定外打刻 (POST /visits/adhoc-checkin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adhoc_checkin_creates_unplanned_visit(client, db) -> None:
    """生成値 (§3): 当日 / 開始 = 打刻時刻 / 終了 = 開始 + 基本訪問時間 / 担当 = 打刻者."""
    me_staff, me = await _make_staff_user(db, "adh-1@example.com", staff_name="予定外 太郎")
    await _make_admin(db, "adh-1-admin@example.com")
    p = await _make_patient(
        db,
        "ADH-1",
        qr_token="adh-tok-1",
        lat=35.0,
        lng=139.0,
        weekly_pattern={"service_minutes": 45},
    )

    before = datetime.now(JST)
    res = await client.post(
        "/api/v1/visits/adhoc-checkin",
        headers=_bearer(me),
        json={"qr_token": "adh-tok-1", "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patient_id"] == str(p.id)
    assert body["primary_staff_id"] == str(me_staff.id)
    assert body["course_id"] is None
    assert body["status"] == "in_progress"
    assert body["visit_date"] == _today_jst().isoformat()
    assert body["latest_checkin"]["kind"] == "arrival"
    assert body["latest_checkin"]["match_status"] == "match"

    start = time.fromisoformat(body["start_time"])
    end = time.fromisoformat(body["end_time"])
    # 開始 = 打刻時刻 (秒切り捨てのため 1 分の幅を許容)。
    assert abs(datetime.combine(_today_jst(), start) - before.replace(tzinfo=None)) <= timedelta(
        minutes=1
    )
    # 終了 = 開始 + 患者の基本訪問時間 (45 分)。
    assert datetime.combine(_today_jst(), end) - datetime.combine(_today_jst(), start) == timedelta(
        minutes=45
    )

    visit = await db.scalar(select(Visit).where(Visit.id == UUID(body["id"])))
    assert visit.is_unplanned is True
    assert await _notification_count(db, NOTIFY_UNPLANNED, visit.id) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_defaults_to_60_minutes(client, db) -> None:
    """基本訪問時間が取れない患者は既定 60 分 (§3 / PO 確認事項 §9-1)."""
    _, me = await _make_staff_user(db, "adh-2@example.com")
    p = await _make_patient(db, "ADH-2", qr_token="adh-tok-2")

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-2"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    start = time.fromisoformat(body["start_time"])
    end = time.fromisoformat(body["end_time"])
    today = _today_jst()
    assert datetime.combine(today, end) - datetime.combine(today, start) == timedelta(minutes=60)
    assert body["patient_id"] == str(p.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_prefers_fixed_visit_duration(client, db) -> None:
    """所要時間の優先順位は配置系 (special_visits._resolve_service_minutes) と同順.

    ①固定枠 (PFV duration_min) が weekly_pattern より優先される。
    """
    _, me = await _make_staff_user(db, "adh-2b@example.com")
    p = await _make_patient(
        db, "ADH-2B", qr_token="adh-tok-2b", weekly_pattern={"service_minutes": 45}
    )
    db.add(
        PatientFixedVisit(
            patient_id=p.id, mode="normal", weekday=0, start_time=time(9, 0), duration_min=90
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-2b"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    today = _today_jst()
    start = time.fromisoformat(body["start_time"])
    end = time.fromisoformat(body["end_time"])
    assert datetime.combine(today, end) - datetime.combine(today, start) == timedelta(minutes=90)
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_returns_is_unplanned_in_visit_read(client, db) -> None:
    """VisitRead に is_unplanned が載る (手書き dict の載せ漏れ回帰防止)."""
    mine, me = await _make_staff_user(db, "adh-2c@example.com")
    p = await _make_patient(db, "ADH-2C", qr_token="adh-tok-2c")
    planned = await _make_visit(db, p.id, mine.id)

    created = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-2c"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["is_unplanned"] is True

    res = await client.get(f"/api/v1/visits/{planned.id}", headers=_bearer(me))
    assert res.status_code == 200, res.text
    assert res.json()["is_unplanned"] is False
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_conflicts_when_advisory_lock_is_held(client, db, monkeypatch) -> None:
    """同時 POST は advisory lock で直列化する (取得失敗 = 409 で増殖ゼロ).

    SQLite では ``try_advisory_xact_lock`` が常に True (排他不要) を返すため、
    「lock を取りに行っていること」と「取れなければ visit を作らないこと」を
    取得失敗の注入で固定する。
    """
    _, me = await _make_staff_user(db, "adh-8@example.com")
    p = await _make_patient(db, "ADH-8", qr_token="adh-tok-8")

    captured: list[int] = []

    async def _busy_lock(_db, key: int) -> bool:
        captured.append(key)
        return False

    monkeypatch.setattr(visits_api, "try_advisory_xact_lock", _busy_lock)

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-8"}
    )
    assert res.status_code == 409, res.text
    assert len(captured) == 1
    # キーは (患者 × スタッフ × 当日) で決まる (別患者なら別キー)。
    assert captured[0] == visits_api._adhoc_lock_key(p.id, me.staff_id, _today_jst())
    assert (await db.scalars(select(Visit).where(Visit.patient_id == p.id))).all() == []
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_visit_is_protected_from_week_regeneration(client, db) -> None:
    """予定外 visit は週生成 / reset-to-fixed の削除対象にならない (設計 §7-3)."""
    _, me = await _make_staff_user(db, "adh-9@example.com")
    await _make_patient(db, "ADH-9", qr_token="adh-tok-9")

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-9"}
    )
    assert res.status_code == 200, res.text
    visit = await db.scalar(select(Visit).where(Visit.id == UUID(res.json()["id"])))

    # reset-to-fixed の削除 whitelist (source / status) の外に居ること。
    assert "manual" not in _RESET_DELETABLE_SOURCES
    assert visit.source not in _RESET_DELETABLE_SOURCES
    assert visit.status not in _RESET_DELETABLE_STATUSES
    # generate-week-only は source='auto' のみ削除する。
    assert visit.source != "auto"
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_requires_qr_token(client, db) -> None:
    """qr_token 無しは 422 (患者特定に QR が構造上必須・決定#6)."""
    _, me = await _make_staff_user(db, "adh-3@example.com")
    await _make_patient(db, "ADH-3", qr_token="adh-tok-3")

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"lat": 35.0, "lng": 139.0}
    )
    assert res.status_code == 422, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_unknown_and_revoked_tokens(client, db) -> None:
    """未知 = 404 / 失効 = 410 (resolve と同じ意味論)."""
    _, me = await _make_staff_user(db, "adh-4@example.com")
    p = await _make_patient(db, "ADH-4", qr_token="adh-tok-4-new")
    db.add(RevokedQrToken(patient_id=p.id, token="adh-tok-4-old"))
    await db.commit()

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "no-such"}
    )
    assert res.status_code == 404, res.text

    res = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-4-old"}
    )
    assert res.status_code == 410, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkin_is_guarded_against_double_creation(client, db) -> None:
    """再スキャンで visit を増殖させず、既存の予定外 visit へ打刻を追記する (§4-3)."""
    _, me = await _make_staff_user(db, "adh-5@example.com")
    await _make_admin(db, "adh-5-admin@example.com")
    p = await _make_patient(db, "ADH-5", qr_token="adh-tok-5")

    first = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-5"}
    )
    assert first.status_code == 200, first.text
    second = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-5"}
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]

    visits = (await db.scalars(select(Visit).where(Visit.patient_id == p.id))).all()
    assert len(visits) == 1
    checkins = (
        await db.scalars(select(VisitCheckin).where(VisitCheckin.visit_id == visits[0].id))
    ).all()
    assert len(checkins) == 2  # 打刻は append-only で 2 行。
    # 通知は冪等 (reference_id = visit.id)。
    assert await _notification_count(db, NOTIFY_UNPLANNED, visits[0].id) == 1
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkout_updates_end_time_to_actual(client, db) -> None:
    """予定外 visit の退出打刻で end_time を実退出時刻 (= 打刻時刻) へ更新する (§3).

    生成直後に退出するとサーバ時刻が数秒しか進まないため、ここでは「朝に開始した
    予定外訪問を今 (JST) 退出する」形を事前 seed して更新そのものを固定する。
    """
    mine, me = await _make_staff_user(db, "adh-6@example.com")
    p = await _make_patient(db, "ADH-6", qr_token="adh-tok-6")
    visit = await _make_visit(
        db,
        p.id,
        mine.id,
        start=time(0, 0),
        end=time(1, 0),  # 生成時の暫定 (開始 + 基本訪問時間)。
        status="in_progress",
        is_unplanned=True,
    )

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkout",
        headers=_bearer(me),
        json={"qr_token": "adh-tok-6"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    now_jst = datetime.now(JST)
    updated = time.fromisoformat(body["end_time"])
    assert updated != time(1, 0)
    # 実退出時刻 (秒切り捨て) に一致する (分境界のズレのみ許容)。
    assert abs(
        datetime.combine(now_jst.date(), updated) - now_jst.replace(tzinfo=None)
    ) <= timedelta(minutes=1)
    await db.rollback()


@pytest.mark.asyncio
async def test_adhoc_checkout_never_moves_end_before_start(client, db) -> None:
    """生成直後 (同じ分) の退出では暫定 end_time を巻き戻さない (開始以前を作らない)."""
    _, me = await _make_staff_user(db, "adh-6b@example.com")
    await _make_patient(db, "ADH-6B", qr_token="adh-tok-6b")

    created = await client.post(
        "/api/v1/visits/adhoc-checkin", headers=_bearer(me), json={"qr_token": "adh-tok-6b"}
    )
    assert created.status_code == 200, created.text
    provisional_end = created.json()["end_time"]

    res = await client.post(
        f"/api/v1/visits/{created.json()['id']}/checkout",
        headers=_bearer(me),
        json={"qr_token": "adh-tok-6b"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["end_time"] == provisional_end
    assert res.json()["end_time"] >= res.json()["start_time"]
    await db.rollback()


@pytest.mark.asyncio
async def test_planned_visit_checkout_keeps_end_time(client, db) -> None:
    """予定 visit の end_time は退出打刻で書き換えない (予定外だけの挙動)."""
    mine, me = await _make_staff_user(db, "adh-7@example.com")
    p = await _make_patient(db, "ADH-7", qr_token="adh-tok-7")
    visit = await _make_visit(db, p.id, mine.id, status="in_progress")

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkout", headers=_bearer(me), json={"qr_token": "adh-tok-7"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["end_time"] == "10:00:00"
    await db.rollback()


# ---------------------------------------------------------------------------
# §6 モニター合成 (実績スタッフ / 代行 / 予定外専用行)
# ---------------------------------------------------------------------------


def _find_visit(monitor, visit_id) -> tuple[object, object]:
    """(row, visit) を返す。見つからなければ (None, None)."""
    for row in monitor.staff:
        for mv in row.visits:
            if mv.visit_id == visit_id:
                return row, mv
    return None, None


@pytest.mark.asyncio
async def test_monitor_marks_substitute_with_actual_staff(db) -> None:
    """最新 arrival の打刻者が担当集合の外なら代行 = 実績名つき + review."""
    owner, _ = await _make_staff_user(db, "mon-1-owner@example.com", staff_name="予定 太郎")
    sub, _ = await _make_staff_user(db, "mon-1-sub@example.com", staff_name="代行 花子")
    p = await _make_patient(db, "MON-1")
    target = _today_jst()
    visit = await _make_visit(db, p.id, owner.id, visit_date=target)
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=p.id,
            staff_id=sub.id,
            kind="arrival",
            scanned_at=datetime.combine(target, time(9, 0), tzinfo=JST).astimezone(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 30), tzinfo=JST).astimezone(UTC)
    )
    _row, mv = _find_visit(monitor, visit.id)
    assert mv is not None
    assert mv.staff_id == owner.id  # 予定は不変。
    assert mv.actual_staff_id == sub.id
    assert mv.actual_staff_name == "代行 花子"
    assert mv.substitute_staff_id == sub.id
    assert mv.substitute_staff_name == "代行 花子"
    assert mv.is_substitute is True
    assert mv.is_unplanned is False
    assert mv.alert_level == ALERT_REVIEW
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_assigned_staff_arrival_is_not_substitute(db) -> None:
    """担当本人の到着は代行ではない (アラートも上がらない)."""
    owner, _ = await _make_staff_user(db, "mon-2-owner@example.com")
    p = await _make_patient(db, "MON-2")
    target = _today_jst()
    visit = await _make_visit(db, p.id, owner.id, visit_date=target)
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=p.id,
            staff_id=owner.id,
            kind="arrival",
            scanned_at=datetime.combine(target, time(9, 0), tzinfo=JST).astimezone(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 30), tzinfo=JST).astimezone(UTC)
    )
    _row, mv = _find_visit(monitor, visit.id)
    assert mv.is_substitute is False
    assert mv.actual_staff_id == owner.id
    # 代行が居ないので代行者フィールドは None のまま。
    assert mv.substitute_staff_id is None
    assert mv.substitute_staff_name is None
    assert mv.alert_level == ALERT_NONE
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_review_clears_substitute_alert(db) -> None:
    """「確認済み」(visit_reviews) で代行アラートは消える (現行運用に乗せる)."""
    owner, _ = await _make_staff_user(db, "mon-3-owner@example.com")
    sub, _ = await _make_staff_user(db, "mon-3-sub@example.com")
    p = await _make_patient(db, "MON-3")
    target = _today_jst()
    visit = await _make_visit(db, p.id, owner.id, visit_date=target)
    db.add(
        VisitCheckin(
            visit_id=visit.id,
            patient_id=p.id,
            staff_id=sub.id,
            kind="arrival",
            scanned_at=datetime.combine(target, time(9, 0), tzinfo=JST).astimezone(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    db.add(VisitReview(visit_id=visit.id))
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 30), tzinfo=JST).astimezone(UTC)
    )
    _row, mv = _find_visit(monitor, visit.id)
    assert mv.is_substitute is True  # 事実は残す。
    assert mv.reviewed is True
    assert mv.alert_level == ALERT_NONE  # トレイからは外れる。
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_groups_unplanned_visits_into_dedicated_row(db) -> None:
    """予定外 visit は専用行 (📌予定外訪問) に集まり、通常行には混ざらない (§6)."""
    staff, _ = await _make_staff_user(db, "mon-4@example.com", staff_name="実績 太郎")
    p_planned = await _make_patient(db, "MON-4A")
    p_adhoc = await _make_patient(db, "MON-4B")
    target = _today_jst()
    planned = await _make_visit(db, p_planned.id, staff.id, visit_date=target)
    adhoc = await _make_visit(
        db,
        p_adhoc.id,
        staff.id,
        visit_date=target,
        start=time(11, 0),
        end=time(12, 0),
        status="in_progress",
        is_unplanned=True,
    )
    db.add(
        VisitCheckin(
            visit_id=adhoc.id,
            patient_id=p_adhoc.id,
            staff_id=staff.id,
            kind="arrival",
            scanned_at=datetime.combine(target, time(11, 0), tzinfo=JST).astimezone(UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(11, 30), tzinfo=JST).astimezone(UTC)
    )
    unplanned_rows = [r for r in monitor.staff if r.course_label == UNPLANNED_ROW_LABEL]
    assert len(unplanned_rows) == 1
    assert [mv.visit_id for mv in unplanned_rows[0].visits] == [adhoc.id]
    mv = unplanned_rows[0].visits[0]
    assert mv.is_unplanned is True
    assert mv.is_substitute is False  # 担当 = 打刻者なので代行ではない。
    assert mv.actual_staff_name == "実績 太郎"
    assert mv.alert_level == ALERT_REVIEW  # 未 review の予定外はトレイに載せる。

    # 同じスタッフの予定 visit は従来どおり通常行のまま。
    planned_row, _ = _find_visit(monitor, planned.id)
    assert planned_row.course_label != UNPLANNED_ROW_LABEL
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_substitute_survives_later_assigned_arrival(db) -> None:
    """後から担当本人が打ち直しても代行の事実は消えない (いずれかの arrival で判定)."""
    owner, _ = await _make_staff_user(db, "mon-6-owner@example.com", staff_name="担当 太郎")
    sub, _ = await _make_staff_user(db, "mon-6-sub@example.com", staff_name="代行 花子")
    p = await _make_patient(db, "MON-6")
    target = _today_jst()
    visit = await _make_visit(db, p.id, owner.id, visit_date=target)
    for staff, hh, mm in ((sub, 9, 0), (owner, 9, 10)):  # 代行 → 担当の順に打刻。
        db.add(
            VisitCheckin(
                visit_id=visit.id,
                patient_id=p.id,
                staff_id=staff.id,
                kind="arrival",
                scanned_at=datetime.combine(target, time(hh, mm), tzinfo=JST).astimezone(UTC),
                match_status="match",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 30), tzinfo=JST).astimezone(UTC)
    )
    _row, mv = _find_visit(monitor, visit.id)
    assert mv.actual_staff_id == owner.id  # 実績表示は最新の打刻者。
    assert mv.actual_staff_name == "担当 太郎"
    assert mv.is_substitute is True  # 代行が居た事実は残す。
    # 「代行バッジ + 担当本人名」の自己矛盾を防ぐため、代行者は別フィールドで返す。
    assert mv.substitute_staff_id == sub.id
    assert mv.substitute_staff_name == "代行 花子"
    assert mv.alert_level == ALERT_REVIEW
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_substitute_staff_is_latest_non_assigned_arrival(db) -> None:
    """代行者が複数居る場合、substitute_staff_* は**最新**の担当集合外の打刻者."""
    owner, _ = await _make_staff_user(db, "mon-8-owner@example.com", staff_name="担当 太郎")
    sub1, _ = await _make_staff_user(db, "mon-8-sub1@example.com", staff_name="代行 一郎")
    sub2, _ = await _make_staff_user(db, "mon-8-sub2@example.com", staff_name="代行 二郎")
    p = await _make_patient(db, "MON-8")
    target = _today_jst()
    visit = await _make_visit(db, p.id, owner.id, visit_date=target)
    for staff, hh, mm in ((sub1, 9, 0), (sub2, 9, 5), (owner, 9, 10)):
        db.add(
            VisitCheckin(
                visit_id=visit.id,
                patient_id=p.id,
                staff_id=staff.id,
                kind="arrival",
                scanned_at=datetime.combine(target, time(hh, mm), tzinfo=JST).astimezone(UTC),
                match_status="match",
                threshold_snapshot={"v": 1},
            )
        )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 30), tzinfo=JST).astimezone(UTC)
    )
    _row, mv = _find_visit(monitor, visit.id)
    assert mv.actual_staff_id == owner.id
    assert mv.substitute_staff_id == sub2.id
    assert mv.substitute_staff_name == "代行 二郎"
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_unplanned_rows_are_split_per_office(db) -> None:
    """予定外専用行は拠点ごとに分かれ、拠点フィルタで当該拠点分だけ残る (§6)."""
    office_a = Office(name="拠点A")
    office_b = Office(name="拠点B")
    db.add_all([office_a, office_b])
    await db.commit()
    await db.refresh(office_a)
    await db.refresh(office_b)
    staff_a, _ = await _make_staff_user(db, "mon-7-a@example.com", staff_name="A拠点 太郎")
    staff_b, _ = await _make_staff_user(db, "mon-7-b@example.com", staff_name="B拠点 花子")
    staff_a.primary_office_id = office_a.id
    staff_b.primary_office_id = office_b.id
    p_a = await _make_patient(db, "MON-7A")
    p_b = await _make_patient(db, "MON-7B")
    target = _today_jst()
    adhoc_a = await _make_visit(
        db, p_a.id, staff_a.id, visit_date=target, status="in_progress", is_unplanned=True
    )
    adhoc_b = await _make_visit(
        db,
        p_b.id,
        staff_b.id,
        visit_date=target,
        start=time(13, 0),
        end=time(14, 0),
        status="in_progress",
        is_unplanned=True,
    )
    await db.commit()

    now = datetime.combine(target, time(15, 0), tzinfo=JST).astimezone(UTC)
    # フィルタ無し: 拠点ごとに 1 本ずつ = 2 本。
    monitor = await build_monitor(db, target, now=now)
    rows = [r for r in monitor.staff if r.course_label == UNPLANNED_ROW_LABEL]
    assert len(rows) == 2
    assert {r.office_id for r in rows} == {office_a.id, office_b.id}

    # 拠点Aフィルタ: A の予定外だけが出る (行ごと消えない / B が混ざらない)。
    monitor_a = await build_monitor(db, target, office_id=office_a.id, now=now)
    rows_a = [r for r in monitor_a.staff if r.course_label == UNPLANNED_ROW_LABEL]
    assert len(rows_a) == 1
    assert rows_a[0].office_id == office_a.id
    assert [mv.visit_id for mv in rows_a[0].visits] == [adhoc_a.id]
    assert adhoc_b.id not in [mv.visit_id for r in monitor_a.staff for mv in r.visits]
    await db.rollback()


@pytest.mark.asyncio
async def test_monitor_unplanned_row_is_scoped_to_target_date(db) -> None:
    """予定外専用行は当日 (JST) 発生分のみ (別日の予定外は出ない)."""
    staff, _ = await _make_staff_user(db, "mon-5@example.com")
    p = await _make_patient(db, "MON-5")
    target = _today_jst()
    await _make_visit(
        db,
        p.id,
        staff.id,
        visit_date=target + timedelta(days=1),
        status="in_progress",
        is_unplanned=True,
    )
    await db.commit()

    monitor = await build_monitor(
        db, target, now=datetime.combine(target, time(9, 0), tzinfo=JST).astimezone(UTC)
    )
    assert [r for r in monitor.staff if r.course_label == UNPLANNED_ROW_LABEL] == []
    await db.rollback()
