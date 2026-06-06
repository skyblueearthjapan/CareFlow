"""Phase G-88 Step2: 最適化設定インフラのテスト.

検証観点:
  * 既定値: SchedulingConfig / DEFAULT_SCHEDULING_CONFIG が契約どおりの値.
  * ローダー: 行なし → 全既定 / 部分行 (一部 NULL) → 列ごと fallback.
  * モデル / CHECK 制約: 範囲外 INSERT が拒否される (SQLite CHECK).
  * API: GET 既定 / PUT 更新 / 範囲外 422 / 時刻順 422 / 権限.

⚠️ この Step では最適化挙動は変えない. 値の保存・読み出しのみを検証する.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import time
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.scheduling_settings import SchedulingSettings
from app.services.scheduling.config import (
    DEFAULT_SCHEDULING_CONFIG,
    SchedulingConfig,
    build_scheduling_config,
    load_scheduling_config,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("pw"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 既定値
# ---------------------------------------------------------------------------


def test_default_scheduling_config_values() -> None:
    """DEFAULT_SCHEDULING_CONFIG が契約どおりの 8 既定値."""
    c = DEFAULT_SCHEDULING_CONFIG
    assert c.visit_buffer_min == 8
    assert c.travel_speed_kmh == 20.0
    assert c.lunch_duration_min == 60
    assert c.lunch_window_start == time(11, 30)
    assert c.lunch_window_end == time(13, 30)
    assert c.business_start == time(9, 30)
    assert c.business_end == time(18, 0)
    assert c.max_patients_per_course == 6


def test_scheduling_config_is_frozen() -> None:
    """frozen dataclass: 代入不可."""
    c = SchedulingConfig(
        visit_buffer_min=8,
        travel_speed_kmh=20.0,
        lunch_duration_min=60,
        lunch_window_start=time(11, 30),
        lunch_window_end=time(13, 30),
        business_start=time(9, 30),
        business_end=time(18, 0),
        max_patients_per_course=6,
    )
    with pytest.raises(FrozenInstanceError):
        c.visit_buffer_min = 10  # type: ignore[misc]


def test_build_config_none_row_returns_default() -> None:
    """行 None → 全既定."""
    assert build_scheduling_config(None) == DEFAULT_SCHEDULING_CONFIG


def test_build_config_partial_row_falls_back() -> None:
    """一部の列だけ設定された行: 設定列は採用、NULL 列は既定."""
    row = SchedulingSettings(
        is_singleton=True,
        visit_buffer_min=15,
        travel_speed_kmh=Decimal("30.0"),
        # 他は NULL のまま
    )
    cfg = build_scheduling_config(row)
    assert cfg.visit_buffer_min == 15
    assert cfg.travel_speed_kmh == 30.0
    assert isinstance(cfg.travel_speed_kmh, float)
    # NULL 列は既定 fallback
    assert cfg.lunch_duration_min == 60
    assert cfg.lunch_window_start == time(11, 30)
    assert cfg.business_end == time(18, 0)
    assert cfg.max_patients_per_course == 6


# ---------------------------------------------------------------------------
# ローダー (DB 経由)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_empty_returns_default(db) -> None:
    """行が無ければ全既定."""
    cfg = await load_scheduling_config(db)
    assert cfg == DEFAULT_SCHEDULING_CONFIG


@pytest.mark.asyncio
async def test_loader_partial_row(db) -> None:
    """部分行: 列ごと fallback."""
    row = SchedulingSettings(
        is_singleton=True,
        lunch_duration_min=45,
        business_start=time(8, 0),
    )
    db.add(row)
    await db.commit()

    cfg = await load_scheduling_config(db)
    assert cfg.lunch_duration_min == 45
    assert cfg.business_start == time(8, 0)
    # 未設定列は既定
    assert cfg.visit_buffer_min == 8
    assert cfg.travel_speed_kmh == 20.0
    assert cfg.business_end == time(18, 0)


# ---------------------------------------------------------------------------
# CHECK 制約 (SQLite)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_buffer_range_rejected(db) -> None:
    """visit_buffer_min = 31 は CHECK 制約で拒否."""
    db.add(SchedulingSettings(is_singleton=True, visit_buffer_min=31))
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_check_lunch_window_order_rejected(db) -> None:
    """lunch_window_start >= lunch_window_end は CHECK 制約で拒否."""
    db.add(
        SchedulingSettings(
            is_singleton=True,
            lunch_window_start=time(13, 30),
            lunch_window_end=time(11, 30),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_singleton_partial_unique(db) -> None:
    """is_singleton = true の行は 1 行のみ (部分 UNIQUE)."""
    db.add(SchedulingSettings(is_singleton=True))
    await db.commit()
    db.add(SchedulingSettings(is_singleton=True))
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()


# ---------------------------------------------------------------------------
# API: GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_get_defaults(client, db) -> None:
    """行が無い状態で GET → 全既定値 + is_default 全 true."""
    admin = await _make_user(db, email="sc-get@example.com", role="admin")
    res = await client.get("/api/v1/scheduling-settings", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["values"]["visit_buffer_min"] == 8
    assert body["values"]["travel_speed_kmh"] == 20.0
    assert body["values"]["lunch_window_start"] == "11:30:00"
    assert body["values"]["business_end"] == "18:00:00"
    assert body["values"]["max_patients_per_course"] == 6
    assert all(body["is_default"].values()) is True


# ---------------------------------------------------------------------------
# API: PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_put_partial_update(client, db) -> None:
    """PUT 部分更新: 指定列のみ更新 + is_default が false に."""
    admin = await _make_user(db, email="sc-put@example.com", role="admin")
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"visit_buffer_min": 12, "travel_speed_kmh": 25},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["values"]["visit_buffer_min"] == 12
    assert body["values"]["travel_speed_kmh"] == 25.0
    assert body["is_default"]["visit_buffer_min"] is False
    assert body["is_default"]["travel_speed_kmh"] is False
    # 未指定列は既定のまま
    assert body["values"]["lunch_duration_min"] == 60
    assert body["is_default"]["lunch_duration_min"] is True

    # 永続化確認: 再 GET
    res = await client.get("/api/v1/scheduling-settings", headers=_bearer(admin))
    assert res.json()["values"]["visit_buffer_min"] == 12


@pytest.mark.asyncio
async def test_api_put_null_resets_to_default(client, db) -> None:
    """明示 null でその列を既定に戻す."""
    admin = await _make_user(db, email="sc-null@example.com", role="admin")
    # まず設定
    await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"visit_buffer_min": 20},
    )
    # null で戻す
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"visit_buffer_min": None},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["values"]["visit_buffer_min"] == 8
    assert body["is_default"]["visit_buffer_min"] is True


@pytest.mark.asyncio
async def test_api_put_out_of_range_422(client, db) -> None:
    """範囲外は 422 (Pydantic Field 制約)."""
    admin = await _make_user(db, email="sc-422@example.com", role="admin")
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"visit_buffer_min": 31},
    )
    assert res.status_code == 422, res.text

    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"travel_speed_kmh": 5},
    )
    assert res.status_code == 422, res.text

    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"max_patients_per_course": 11},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_api_put_time_order_422_within_payload(client, db) -> None:
    """同一リクエストで lunch_window_start >= end は 422."""
    admin = await _make_user(db, email="sc-order@example.com", role="admin")
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"lunch_window_start": "13:30:00", "lunch_window_end": "11:30:00"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_api_put_time_order_422_after_merge(client, db) -> None:
    """片方のみ更新でも既存値とのマージ後に矛盾すれば 422."""
    admin = await _make_user(db, email="sc-merge@example.com", role="admin")
    # business 09:30-18:00 が既定. business_start を 19:00 に上げると end(18:00) と矛盾.
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"business_start": "12:00:00", "business_end": "18:00:00"},
    )
    assert res.status_code == 200, res.text
    # 既存 start=12:00. end のみ 11:00 に下げると 12:00 >= 11:00 で矛盾.
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"business_end": "11:00:00"},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_api_put_time_order_422_after_null_reset(client, db) -> None:
    """片側を明示 null で既定に戻した結果、有効値が矛盾すれば 422.

    business_start を 19:00, end を 20:00 に設定後、start のみ null で既定 (09:30)
    に戻すと、有効値が start=09:30 / end=20:00 となり矛盾しない (正常系のため別途
    検証)。ここでは end を既定 (18:00) に戻して有効値 start=19:00 >= end 18:00 を
    検出する: 生 row では end=NULL のため旧実装ではすり抜けるケース.
    """
    admin = await _make_user(db, email="sc-null-order@example.com", role="admin")
    # business_start=19:00 / business_end=20:00 を設定 (両方 non-default).
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"business_start": "19:00:00", "business_end": "20:00:00"},
    )
    assert res.status_code == 200, res.text
    # business_end を null で既定 (18:00) に戻す → 有効値 start 19:00 >= end 18:00.
    res = await client.put(
        "/api/v1/scheduling-settings",
        headers=_bearer(admin),
        json={"business_end": None},
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_api_rbac(client, db) -> None:
    """GET / PUT は admin / manager OK, staff は 403, 未認証 401."""
    manager = await _make_user(db, email="sc-rbac-m@example.com", role="manager")
    staff = await _make_user(db, email="sc-rbac-s@example.com", role="staff")

    # GET
    assert (
        await client.get("/api/v1/scheduling-settings", headers=_bearer(manager))
    ).status_code == 200
    assert (
        await client.get("/api/v1/scheduling-settings", headers=_bearer(staff))
    ).status_code == 403
    assert (await client.get("/api/v1/scheduling-settings")).status_code == 401

    # PUT
    assert (
        await client.put(
            "/api/v1/scheduling-settings",
            headers=_bearer(manager),
            json={"visit_buffer_min": 10},
        )
    ).status_code == 200
    assert (
        await client.put(
            "/api/v1/scheduling-settings",
            headers=_bearer(staff),
            json={"visit_buffer_min": 10},
        )
    ).status_code == 403
