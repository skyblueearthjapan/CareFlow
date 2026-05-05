"""W1-BE1: v2 patient master CRUD + RBAC tests.

設計仕様書 v0.9 §4.1 / API 契約 v0.1 §1 / 実装手順書 v0.2 §2 W1-BE1 に対応する
受入テスト。``extra='ignore'`` による旧フィールド受理 (後方互換) も検証。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, User

# ---------------------------------------------------------------------------
# helpers
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


def _v2_payload(**overrides) -> dict:
    """Minimal v2 PatientCreate payload."""
    base = {
        "code": "P-V2-001",
        "name": "テスト 太郎",
        "kana": "テスト タロウ",
        "sex": "male",
        "status": "active",
        "insurance": "medical",
        "address": "千葉市稲毛区xxx",
        "lat": 35.65,
        "lng": 140.10,
        "sex_restriction": None,
        "weekly_pattern": {
            "frequency_per_week": 2,
            "preferred_weekdays": ["Mon", "Thu"],
            "service_minutes": 60,
            "time_type": "午前",
        },
        "note": "v2 schema test",
    }
    base.update(overrides)
    return base


_LEGACY_FIELDS = (
    "age",
    "ng_time_start",
    "ng_time_end",
    "specified_type",
    "ng_staff_ids",
    "preferred_staff_ids",
    "continuous_request",
    "required_staff_count",
    "area",
)


# ---------------------------------------------------------------------------
# 1) POST 患者作成 (v2 形式) → 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_patient_v2_returns_201(client, db) -> None:
    admin = await _make_user(db, "v2-c-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-CREATE"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["code"] == "P-V2-CREATE"
    assert body["name"] == "テスト 太郎"
    assert body["status"] == "active"
    # 削除フィールドが応答に含まれない
    for legacy in _LEGACY_FIELDS:
        assert legacy not in body, f"removed field {legacy!r} leaked into response"


# ---------------------------------------------------------------------------
# 2) GET 一覧 / 詳細 → v2 形式レスポンス
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_patient_v2_excludes_legacy_fields(client, db) -> None:
    admin = await _make_user(db, "v2-l-admin@example.com", "admin")
    # 直接 ORM で投入
    p = Patient(code="P-V2-L1", name="一覧テスト")
    db.add(p)
    await db.commit()

    res = await client.get("/api/v1/patients", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body, list)
    assert any(item["code"] == "P-V2-L1" for item in body)
    for item in body:
        for legacy in _LEGACY_FIELDS:
            assert legacy not in item


@pytest.mark.asyncio
async def test_get_patient_v2_excludes_legacy_fields(client, db) -> None:
    admin = await _make_user(db, "v2-g-admin@example.com", "admin")
    p = Patient(code="P-V2-G1", name="詳細テスト")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    res = await client.get(f"/api/v1/patients/{p.id}", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == "P-V2-G1"
    for legacy in _LEGACY_FIELDS:
        assert legacy not in body


# ---------------------------------------------------------------------------
# 3) PATCH 部分更新 → 成功
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_patient_v2_partial_update(client, db) -> None:
    admin = await _make_user(db, "v2-p-admin@example.com", "admin")
    p = Patient(code="P-V2-PATCH", name="旧名前")
    db.add(p)
    await db.commit()
    await db.refresh(p)

    res = await client.patch(
        f"/api/v1/patients/{p.id}",
        headers=_bearer(admin),
        json={"name": "新名前", "note": "更新済"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["name"] == "新名前"
    assert body["note"] == "更新済"
    assert body["code"] == "P-V2-PATCH"  # unchanged


# ---------------------------------------------------------------------------
# 4) DELETE → soft delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_patient_v2_soft_deletes(client, db) -> None:
    admin = await _make_user(db, "v2-d-admin@example.com", "admin")
    p = Patient(code="P-V2-DEL", name="削除対象")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    pid = p.id

    res = await client.delete(f"/api/v1/patients/{pid}", headers=_bearer(admin))
    assert res.status_code == 204, res.text

    # GET should now return 404 (soft-deleted rows are filtered out)
    follow = await client.get(f"/api/v1/patients/{pid}", headers=_bearer(admin))
    assert follow.status_code == 404, follow.text


# ---------------------------------------------------------------------------
# 5) RBAC: staff が POST すると 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_patient_v2_staff_returns_403(client, db) -> None:
    staff = await _make_user(db, "v2-c-staff@example.com", "staff")
    res = await client.post(
        "/api/v1/patients",
        headers=_bearer(staff),
        json=_v2_payload(code="P-V2-RBAC"),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_delete_patient_v2_manager_returns_403(client, db) -> None:
    """DELETE は admin のみ。manager は 403。"""
    manager = await _make_user(db, "v2-d-mgr@example.com", "manager")
    p = Patient(code="P-V2-RBAC2", name="削除権限")
    db.add(p)
    await db.commit()
    await db.refresh(p)
    res = await client.delete(f"/api/v1/patients/{p.id}", headers=_bearer(manager))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 6) 旧フィールド付きリクエストは extra='ignore' で受理
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_patient_v2_ignores_legacy_fields(client, db) -> None:
    admin = await _make_user(db, "v2-le-admin@example.com", "admin")
    payload = _v2_payload(code="P-V2-LEG")
    # 旧フィールドを混ぜる -- 受理されつつ silently 落ちる
    payload.update(
        {
            "age": 80,
            "ng_time_start": "10:00:00",
            "ng_time_end": "12:00:00",
            "specified_type": "必須",
            "ng_staff_ids": [str(uuid4())],
            "preferred_staff_ids": [str(uuid4())],
            "continuous_request": True,
            "required_staff_count": 2,
            "area": "B1",
            # 念のため旧 weekday_priority キーも weekly_pattern に混入
            "weekly_pattern": {
                "frequency_per_week": 1,
                "preferred_weekdays": ["Mon"],
                "weekday_priority": "高",  # 旧キー (extra='allow' で残置)
                "ng_weekdays": ["Sat", "Sun"],  # 旧キー
                "service_minutes": 60,
                "time_type": "固定",
                "preferred_start": "09:00",
                "preferred_end": "10:00",
            },
        }
    )
    res = await client.post("/api/v1/patients", headers=_bearer(admin), json=payload)
    assert res.status_code == 201, res.text
    body = res.json()
    # 旧フィールドはレスポンスに乗らない
    for legacy in _LEGACY_FIELDS:
        assert legacy not in body, f"legacy field {legacy!r} leaked into response"


# ---------------------------------------------------------------------------
# 7) weekly_pattern.staff_count=2 が保存・取得できる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weekly_pattern_staff_count_persists(client, db) -> None:
    admin = await _make_user(db, "v2-sc-admin@example.com", "admin")
    payload = _v2_payload(
        code="P-V2-SC",
        weekly_pattern={
            "frequency_per_week": 2,
            "preferred_weekdays": ["Tue", "Fri"],
            "service_minutes": 90,
            "time_type": "時間帯",
            "preferred_start": "13:00",
            "preferred_end": "14:30",
            "entries": [
                {
                    "weekday": "Tue",
                    "time_type": "時間帯",
                    "preferred_start": "13:00",
                    "preferred_end": "14:30",
                    "service_minutes": 90,
                    "staff_count": 2,
                },
                {
                    "weekday": "Fri",
                    "time_type": "時間帯",
                    "preferred_start": "13:00",
                    "preferred_end": "14:30",
                    "service_minutes": 90,
                    "staff_count": 1,
                },
            ],
        },
    )
    create = await client.post("/api/v1/patients", headers=_bearer(admin), json=payload)
    assert create.status_code == 201, create.text
    pid = create.json()["id"]

    follow = await client.get(f"/api/v1/patients/{pid}", headers=_bearer(admin))
    assert follow.status_code == 200, follow.text
    body = follow.json()
    wp = body["weekly_pattern"]
    assert wp is not None
    entries = wp.get("entries") or []
    by_weekday = {e["weekday"]: e for e in entries}
    assert by_weekday["Tue"]["staff_count"] == 2
    assert by_weekday["Fri"]["staff_count"] == 1


# ---------------------------------------------------------------------------
# 8) special_weekly_pattern + special_week_active が保存・取得できる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_special_week_pattern_and_active_persist(client, db) -> None:
    admin = await _make_user(db, "v2-sw-admin@example.com", "admin")
    payload = _v2_payload(
        code="P-V2-SW",
        special_weekly_pattern={
            "frequency_per_week": 5,
            "preferred_weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "service_minutes": 45,
            "time_type": "午前",
        },
        special_week_active=[
            {"iso_year": 2026, "iso_week": 20},
            {"iso_year": 2026, "iso_week": 21},
        ],
    )
    create = await client.post("/api/v1/patients", headers=_bearer(admin), json=payload)
    assert create.status_code == 201, create.text
    pid = create.json()["id"]

    follow = await client.get(f"/api/v1/patients/{pid}", headers=_bearer(admin))
    assert follow.status_code == 200, follow.text
    body = follow.json()

    swp = body.get("special_weekly_pattern")
    assert swp is not None
    assert swp["frequency_per_week"] == 5
    assert swp["time_type"] == "午前"

    swa = body.get("special_week_active") or []
    weeks = {(item["iso_year"], item["iso_week"]) for item in swa}
    assert weeks == {(2026, 20), (2026, 21)}


# ---------------------------------------------------------------------------
# 9) supplemental: PATCH で special_week_active を空リスト化できる
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_special_week_active_to_empty(client, db) -> None:
    admin = await _make_user(db, "v2-sw2-admin@example.com", "admin")
    create = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(
            code="P-V2-SW2",
            special_week_active=[{"iso_year": 2026, "iso_week": 25}],
        ),
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]

    res = await client.patch(
        f"/api/v1/patients/{pid}",
        headers=_bearer(admin),
        json={"special_week_active": []},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body.get("special_week_active") in ([], None)
