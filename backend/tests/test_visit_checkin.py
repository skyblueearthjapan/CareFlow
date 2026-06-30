"""QR 訪問チェックイン Phase 1 のテスト (judge + checkin/checkout/no-show API).

- distance/accuracy 分岐 (compute_match_status の全分岐 + 境界 99/100/101・
  299/300/301)。
- no_gps (GPS 欠落 / 精度 > review_m)。
- API: qr 無し = manual 成立、別患者 QR = 409、status 遷移、no_show reason 必須、
  当日外 / 取消 / 削除拒否、既存 me.ts ペイロード {lat,lng,at} 互換 (R1)。
- checkin_settings range / review_gte_match / singleton 制約。
- DB しきい値の override が judge に反映される。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.security import create_access_token, hash_password
from app.models import CheckinSettings, Patient, Staff, User, Visit, VisitCheckin
from app.schemas.visit_checkin import CheckinCreate
from app.services.checkin.judge import (
    DEFAULT_THRESHOLDS,
    compute_distance_m,
    compute_match_status,
    judge_checkin,
    load_thresholds,
)
from app.utils.geo import haversine_m

JST = ZoneInfo("Asia/Tokyo")


def _today_jst() -> date:
    return datetime.now(JST).date()


async def _make_staff_user(db, email: str) -> tuple[Staff, User]:
    staff = Staff(name="担当ヘルパー")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter"),
        role="staff",
        staff_id=staff.id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return staff, user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, code: str, *, lat=None, lng=None, qr_token=None) -> Patient:
    p = Patient(code=code, name="利用者", lat=lat, lng=lng, qr_token=qr_token)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(db, patient_id, staff_id, *, visit_date=None, status="planned") -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=visit_date or _today_jst(),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status=status,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


async def _load_visit_with_patient(db, visit_id) -> Visit:
    """judge を直接呼ぶテスト用に patient を eager-load した visit を返す.

    同一 session の identity map に既に visit が居ると selectinload が再読込を
    省くため、populate_existing で関係を確実にロードする。
    """
    return await db.scalar(
        select(Visit)
        .where(Visit.id == visit_id)
        .options(selectinload(Visit.patient))
        .execution_options(populate_existing=True)
    )


# ---------------------------------------------------------------------------
# Unit: geo
# ---------------------------------------------------------------------------


def test_haversine_m_is_km_times_1000() -> None:
    # 同一点は 0。
    assert haversine_m(35.0, 139.0, 35.0, 139.0) == pytest.approx(0.0)
    # 緯度 0.001 度 ≒ 111 m オーダー。km*1000 の整合のみ確認。
    d = haversine_m(35.0, 139.0, 35.001, 139.0)
    assert 100 < d < 130


# ---------------------------------------------------------------------------
# Unit: compute_match_status — 距離境界 + 精度分岐
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        (99.0, "match"),
        (100.0, "match"),  # <= match_m
        (101.0, "review"),
        (299.0, "review"),
        (300.0, "review"),  # <= review_m
        (301.0, "mismatch"),
    ],
)
def test_match_status_distance_boundaries(distance, expected) -> None:
    # accuracy 良好 (10m) で純粋に距離境界を確認。
    assert compute_match_status(distance, 10.0, DEFAULT_THRESHOLDS) == expected


def test_match_status_accuracy_downgrades_near_to_review() -> None:
    # 近距離でも精度 > 許容 (50m) なら review。
    assert compute_match_status(50.0, 60.0, DEFAULT_THRESHOLDS) == "review"
    # 精度が許容内なら match。
    assert compute_match_status(50.0, 40.0, DEFAULT_THRESHOLDS) == "match"


def test_match_status_accuracy_at_tolerance_boundary_is_match() -> None:
    # 精度 == 許容 (50.0) は厳密大 (> tol) ではないので review に落ちず match。
    # accuracy_m=50.0, distance=50.0 (<= match_m=100) で境界等値を確認。
    assert compute_match_status(50.0, 50.0, DEFAULT_THRESHOLDS) == "match"


def test_match_status_no_gps_when_distance_none() -> None:
    assert compute_match_status(None, 10.0, DEFAULT_THRESHOLDS) == "no_gps"
    assert compute_match_status(None, None, DEFAULT_THRESHOLDS) == "no_gps"


def test_match_status_no_gps_when_accuracy_exceeds_review() -> None:
    # 精度 > review_m (300m) は測位不能 = no_gps (距離で断定しない)。
    assert compute_match_status(50.0, 301.0, DEFAULT_THRESHOLDS) == "no_gps"
    assert compute_match_status(500.0, 400.0, DEFAULT_THRESHOLDS) == "no_gps"


def test_match_status_accuracy_none_is_treated_as_acceptable() -> None:
    assert compute_match_status(50.0, None, DEFAULT_THRESHOLDS) == "match"
    assert compute_match_status(500.0, None, DEFAULT_THRESHOLDS) == "mismatch"


def test_compute_distance_m_none_when_coords_missing() -> None:
    p = Patient(code="X", name="n", lat=None, lng=None)
    assert compute_distance_m(p, 35.0, 139.0) is None
    p2 = Patient(code="Y", name="n", lat=35.0, lng=139.0)
    assert compute_distance_m(p2, None, None) is None
    assert compute_distance_m(p2, 35.0, 139.0) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Unit: load_thresholds — DB override / fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_thresholds_defaults_when_no_row(db) -> None:
    assert await load_thresholds(db) == DEFAULT_THRESHOLDS


@pytest.mark.asyncio
async def test_load_thresholds_db_override(db) -> None:
    db.add(CheckinSettings(match_m=200, review_m=400))
    await db.commit()
    th = await load_thresholds(db)
    assert th["match_m"] == 200
    assert th["review_m"] == 400
    # NULL 列は既定 fallback。
    assert th["accuracy_m"] == DEFAULT_THRESHOLDS["accuracy_m"]
    # override 後は distance 150 が match (match_m=200)。
    assert compute_match_status(150.0, 10.0, th) == "match"


# ---------------------------------------------------------------------------
# Integration: checkin / checkout / no-show
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_manual_without_qr_succeeds(client, db) -> None:
    """R1: qr_token 無し = manual 成立。既存 me.ts {lat,lng,at} がそのまま通る."""
    staff, user = await _make_staff_user(db, "ci-1@example.com")
    p = await _make_patient(db, "CI-1", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0, "at": "2026-06-30T09:00:00+09:00"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "in_progress"
    assert body["latest_checkin"]["checkin_source"] == "manual"
    assert body["latest_checkin"]["kind"] == "arrival"
    assert body["latest_checkin"]["match_status"] == "match"


@pytest.mark.asyncio
async def test_checkin_without_any_body_is_not_422(client, db) -> None:
    """R1: 全フィールド任意。空ボディでも 422 にならない (no_gps で記録)."""
    staff, user = await _make_staff_user(db, "ci-empty@example.com")
    p = await _make_patient(db, "CI-EMPTY")
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["match_status"] == "no_gps"


@pytest.mark.asyncio
async def test_checkin_with_matching_qr_sets_source_qr(client, db) -> None:
    staff, user = await _make_staff_user(db, "ci-qr@example.com")
    p = await _make_patient(db, "CI-QR", lat=35.0, lng=139.0, qr_token="tok-match-1")
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"qr_token": "tok-match-1", "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["checkin_source"] == "qr"


@pytest.mark.asyncio
async def test_checkin_with_other_patient_qr_returns_409(client, db) -> None:
    staff, user = await _make_staff_user(db, "ci-409@example.com")
    p1 = await _make_patient(db, "CI-P1", lat=35.0, lng=139.0)
    await _make_patient(db, "CI-P2", lat=36.0, lng=140.0, qr_token="tok-other-2")
    visit = await _make_visit(db, p1.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"qr_token": "tok-other-2"},
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_checkin_with_unknown_qr_returns_404(client, db) -> None:
    staff, user = await _make_staff_user(db, "ci-404@example.com")
    p = await _make_patient(db, "CI-404", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"qr_token": "no-such-token"},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_checkout_sets_completed(client, db) -> None:
    staff, user = await _make_staff_user(db, "co-1@example.com")
    p = await _make_patient(db, "CO-1", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, status="in_progress")

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkout",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["latest_checkin"]["kind"] == "departure"


@pytest.mark.asyncio
async def test_no_show_requires_reason(client, db) -> None:
    staff, user = await _make_staff_user(db, "ns-1@example.com")
    p = await _make_patient(db, "NS-1")
    visit = await _make_visit(db, p.id, staff.id)

    # reason 無し → 422。
    res = await client.post(f"/api/v1/visits/{visit.id}/no-show", headers=_bearer(user), json={})
    assert res.status_code == 422, res.text

    # 空白のみも拒否。
    res_blank = await client.post(
        f"/api/v1/visits/{visit.id}/no-show",
        headers=_bearer(user),
        json={"reason": "   "},
    )
    assert res_blank.status_code == 422, res_blank.text


@pytest.mark.asyncio
async def test_no_show_with_reason_keeps_status_planned(client, db) -> None:
    staff, user = await _make_staff_user(db, "ns-2@example.com")
    p = await _make_patient(db, "NS-2")
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/no-show",
        headers=_bearer(user),
        json={"reason": "不在のため"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # status は据置 (planned)。
    assert body["status"] == "planned"
    assert body["latest_checkin"]["kind"] == "no_show"
    assert body["latest_checkin"]["reason"] == "不在のため"


# ---------------------------------------------------------------------------
# 契約対称性: GET /visits/{id} と GET /visits (一覧) も latest_checkin を返す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_visit_returns_latest_checkin_after_checkin(client, db) -> None:
    """checkin 後の GET /visits/{id} が latest_checkin を含む (契約対称性)."""
    staff, user = await _make_staff_user(db, "getlc@example.com")
    p = await _make_patient(db, "GETLC", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    # checkin 前は latest_checkin が None。
    res_before = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert res_before.status_code == 200, res_before.text
    assert res_before.json()["latest_checkin"] is None

    res_ci = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res_ci.status_code == 200, res_ci.text

    # checkin 後の単一 GET が latest_checkin を返す。
    res_after = await client.get(f"/api/v1/visits/{visit.id}", headers=_bearer(user))
    assert res_after.status_code == 200, res_after.text
    lc = res_after.json()["latest_checkin"]
    assert lc is not None
    assert lc["kind"] == "arrival"
    assert lc["match_status"] == "match"

    # 一覧 (GET /visits) でも同じ visit が latest_checkin を含む。
    res_list = await client.get("/api/v1/visits", headers=_bearer(user))
    assert res_list.status_code == 200, res_list.text
    target = next((v for v in res_list.json() if v["id"] == str(visit.id)), None)
    assert target is not None
    assert target["latest_checkin"] is not None
    assert target["latest_checkin"]["kind"] == "arrival"

    # 共有 in-memory 接続に開いたままの読み取りトランザクションを解放する
    # (テスト間汚染防止; 他の client+db 併用テストと同方針)。
    await db.rollback()


# ---------------------------------------------------------------------------
# Integration: visit ガード (当日外 / 取消 / 削除) + 可視性 + staff 未紐付け
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_not_today_returns_409(client, db) -> None:
    staff, user = await _make_staff_user(db, "g-day@example.com")
    p = await _make_patient(db, "G-DAY", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, visit_date=_today_jst() - timedelta(days=1))

    res = await client.post(f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_checkin_cancelled_returns_409(client, db) -> None:
    staff, user = await _make_staff_user(db, "g-cancel@example.com")
    p = await _make_patient(db, "G-CANCEL")
    visit = await _make_visit(db, p.id, staff.id, status="cancelled")

    res = await client.post(f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_checkin_deleted_returns_409(client, db) -> None:
    staff, user = await _make_staff_user(db, "g-del@example.com")
    p = await _make_patient(db, "G-DEL")
    visit = await _make_visit(db, p.id, staff.id)
    visit.deleted_at = datetime.now(JST)
    await db.commit()

    res = await client.post(f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_checkin_other_staff_visit_returns_404(client, db) -> None:
    """担当外の visit は存在を秘匿 (404)."""
    _owner_staff, _owner_user = await _make_staff_user(db, "g-owner@example.com")
    _other_staff, other_user = await _make_staff_user(db, "g-other@example.com")
    p = await _make_patient(db, "G-VIS")
    visit = await _make_visit(db, p.id, _owner_staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin", headers=_bearer(other_user), json={}
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_checkin_user_without_staff_returns_403(client, db) -> None:
    user = User(
        email="g-nostaff@example.com",
        password_hash=hash_password("x"),
        role="staff",
        staff_id=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    res = await client.post(f"/api/v1/visits/{uuid4()}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_checkin_unknown_visit_returns_404(client, db) -> None:
    _staff, user = await _make_staff_user(db, "g-unknown@example.com")
    res = await client.post(f"/api/v1/visits/{uuid4()}/checkin", headers=_bearer(user), json={})
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# checkin_settings DB 制約 (range / review_gte_match / singleton)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_settings_match_m_range_rejected(db) -> None:
    db.add(CheckinSettings(match_m=5))  # < 10
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_checkin_settings_review_gte_match_rejected(db) -> None:
    db.add(CheckinSettings(match_m=300, review_m=100))  # review < match
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_checkin_settings_singleton_unique(db) -> None:
    db.add(CheckinSettings())
    await db.commit()
    db.add(CheckinSettings())  # 2 行目の is_singleton=true は禁止
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ---------------------------------------------------------------------------
# patients.qr_token 部分 UNIQUE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patient_qr_token_partial_unique(db) -> None:
    # NULL は複数許容。
    await _make_patient(db, "QT-1")
    await _make_patient(db, "QT-2")
    # 非 NULL の重複は禁止。
    await _make_patient(db, "QT-3", qr_token="dup-token")
    p4 = Patient(code="QT-4", name="n", qr_token="dup-token")
    db.add(p4)
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


# ---------------------------------------------------------------------------
# JST 深夜境界 (judge を now で直接駆動) — 設計 R3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_judge_jst_midnight_same_day_succeeds(db) -> None:
    """JST 00:30 (= 前日 UTC 15:30) でも当日 visit の checkin は成立する."""
    staff, _user = await _make_staff_user(db, "jst-a@example.com")
    p = await _make_patient(db, "JST-A", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, visit_date=date(2026, 6, 30))
    visit = await _load_visit_with_patient(db, visit.id)

    # JST 2026-06-30T00:30 は UTC では 2026-06-29T15:30 (前日)。JST 換算で当日扱い。
    now = datetime(2026, 6, 30, 0, 30, 0, tzinfo=JST)
    checkin = await judge_checkin(db, visit, staff.id, CheckinCreate(), "arrival", now=now)
    # 例外を投げずに行を生成 = ガード通過。
    assert checkin.visit_id == visit.id
    assert checkin.kind == "arrival"
    # judge は未 commit で session に add するだけ。共有 in-memory 接続に開いた
    # トランザクションを残さないよう明示的に rollback する (テスト間汚染防止)。
    await db.rollback()


@pytest.mark.asyncio
async def test_judge_jst_late_night_next_day_visit_returns_409(db) -> None:
    """JST 23:59:59 で翌日 (07-01) の visit を打刻すると当日外 409."""
    staff, _user = await _make_staff_user(db, "jst-b@example.com")
    p = await _make_patient(db, "JST-B", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, visit_date=date(2026, 7, 1))
    visit = await _load_visit_with_patient(db, visit.id)

    now = datetime(2026, 6, 30, 23, 59, 59, tzinfo=JST)
    with pytest.raises(HTTPException) as exc:
        await judge_checkin(db, visit, staff.id, CheckinCreate(), "arrival", now=now)
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# 再スキャン (二重打刻) — append-only で 2 行残る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_rescan_appends_second_row(client, db) -> None:
    staff, user = await _make_staff_user(db, "rescan@example.com")
    p = await _make_patient(db, "RESCAN", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res1 = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res1.status_code == 200, res1.text
    res2 = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res2.status_code == 200, res2.text

    first_at = datetime.fromisoformat(res1.json()["latest_checkin"]["scanned_at"])
    second_at = datetime.fromisoformat(res2.json()["latest_checkin"]["scanned_at"])
    # 2 回目の最新打刻は 1 回目以降 (scanned_at は server now=UTC, 単調増加)。
    assert second_at >= first_at
    # latest_checkin は 2 回目の行を指す (= append-only で最新採用)。
    assert res2.json()["latest_checkin"]["id"] != res1.json()["latest_checkin"]["id"]

    # DB に 2 行存在する (更新でなく追記)。
    count = await db.scalar(
        select(func.count()).select_from(VisitCheckin).where(VisitCheckin.visit_id == visit.id)
    )
    assert count == 2


# ---------------------------------------------------------------------------
# device_time / at エイリアスが DB に保存される (API レスポンスでなく DB を検証)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_persists_device_time_from_at_alias(client, db) -> None:
    staff, user = await _make_staff_user(db, "dtime@example.com")
    p = await _make_patient(db, "DTIME", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0, "at": "2026-06-30T09:00:00+09:00"},
    )
    assert res.status_code == 200, res.text

    row = await db.scalar(select(VisitCheckin).where(VisitCheckin.visit_id == visit.id))
    # 送った `at` が device_time として永続化されている (None でない)。
    assert row is not None
    assert row.device_time is not None


# ---------------------------------------------------------------------------
# 入力境界検証 (schema) — 負の accuracy / 範囲外座標は 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_negative_accuracy_rejected_422(client, db) -> None:
    """負の accuracy で精度ガードをバイパスできないよう 422 で弾く (security)."""
    staff, user = await _make_staff_user(db, "neg-acc@example.com")
    p = await _make_patient(db, "NEG-ACC", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0, "accuracy": -1.0},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_checkin_out_of_range_lat_rejected_422(client, db) -> None:
    """緯度 範囲外 (>90) は 422."""
    staff, user = await _make_staff_user(db, "oor-lat@example.com")
    p = await _make_patient(db, "OOR-LAT", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 100.0, "lng": 139.0},
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# status 退行ガード — completed の visit に再 checkin しても completed のまま
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkin_does_not_regress_completed_status(client, db) -> None:
    staff, user = await _make_staff_user(db, "noregress@example.com")
    p = await _make_patient(db, "NOREGRESS", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id, status="completed")

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(user),
        json={"lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # completed は in_progress に巻き戻らない。
    assert body["status"] == "completed"
    # 打刻行は据置せず記録される。
    assert body["latest_checkin"]["kind"] == "arrival"
