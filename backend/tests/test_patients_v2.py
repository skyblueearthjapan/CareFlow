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


# ---------------------------------------------------------------------------
# 10) W18 Phase A: requires_multiple_staff の round-trip (default=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_patient_v2_requires_multiple_staff_defaults_false(client, db) -> None:
    """POST 時に requires_multiple_staff を省略すると False がセットされる."""
    admin = await _make_user(db, "v2-rms-default-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-RMS-DEF"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["requires_multiple_staff"] is False


@pytest.mark.asyncio
async def test_create_patient_v2_requires_multiple_staff_true_persists(client, db) -> None:
    """POST で requires_multiple_staff=True を送ると保存され、GET でも True が返る."""
    admin = await _make_user(db, "v2-rms-true-admin@example.com", "admin")
    res = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-RMS-TRUE", requires_multiple_staff=True),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["requires_multiple_staff"] is True
    pid = body["id"]

    follow = await client.get(f"/api/v1/patients/{pid}", headers=_bearer(admin))
    assert follow.status_code == 200, follow.text
    assert follow.json()["requires_multiple_staff"] is True


@pytest.mark.asyncio
async def test_patch_patient_v2_requires_multiple_staff_toggle(client, db) -> None:
    """PATCH で requires_multiple_staff を False → True → False に切り替えられる."""
    admin = await _make_user(db, "v2-rms-patch-admin@example.com", "admin")
    create = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-RMS-PATCH"),
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    assert create.json()["requires_multiple_staff"] is False

    # False → True
    r1 = await client.patch(
        f"/api/v1/patients/{pid}",
        headers=_bearer(admin),
        json={"requires_multiple_staff": True},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["requires_multiple_staff"] is True

    # 他フィールドだけ更新しても requires_multiple_staff は維持される (PATCH partial)
    r2 = await client.patch(
        f"/api/v1/patients/{pid}",
        headers=_bearer(admin),
        json={"note": "更新"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["requires_multiple_staff"] is True
    assert r2.json()["note"] == "更新"

    # True → False
    r3 = await client.patch(
        f"/api/v1/patients/{pid}",
        headers=_bearer(admin),
        json={"requires_multiple_staff": False},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["requires_multiple_staff"] is False


@pytest.mark.asyncio
async def test_list_patient_v2_includes_requires_multiple_staff(client, db) -> None:
    """GET 一覧に requires_multiple_staff が含まれる (FE が一覧から制約を判定する用).

    POST 経由で 2 件登録し、一覧で各 patient の requires_multiple_staff が
    シリアライズされていることを検証する (POST 経由なら special_week_active が
    既定空 list で安定する)。
    """
    admin = await _make_user(db, "v2-rms-list-admin@example.com", "admin")

    # 2 件作成 (片方 True / 片方 False で両方シリアライズされることを確認)
    r1 = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-RMS-LIST-T", requires_multiple_staff=True),
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/api/v1/patients",
        headers=_bearer(admin),
        json=_v2_payload(code="P-V2-RMS-LIST-F", requires_multiple_staff=False),
    )
    assert r2.status_code == 201, r2.text

    res = await client.get("/api/v1/patients", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    by_code = {item["code"]: item for item in body}
    assert "P-V2-RMS-LIST-T" in by_code
    assert by_code["P-V2-RMS-LIST-T"]["requires_multiple_staff"] is True
    assert "P-V2-RMS-LIST-F" in by_code
    assert by_code["P-V2-RMS-LIST-F"]["requires_multiple_staff"] is False
