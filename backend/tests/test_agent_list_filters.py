"""GET /patients ?status= &q= / GET /staff ?q= &include_retired= (追加の絞り込み).

指定なしでは従来と同一の結果 (後方互換) であることを先に確かめる。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.patient import Patient


async def _user(db, role: str, staff_id=None) -> User:
    u = User(
        email=f"{role}-{staff_id}@ex.com",
        password_hash=hash_password("x"),
        role=role,
        staff_id=staff_id,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _bearer(u: User) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    }


async def _seed_patients(db) -> None:
    rows = [
        Patient(code="P1", name="田中 花子", kana="タナカ ハナコ", status="active"),
        Patient(code="P2", name="田村 一郎", kana="タムラ", status="admitted"),
        Patient(code="P3", name="鈴木 次郎", kana="スズキ", status="active"),
        Patient(code="P4", name="田中 削除", status="active", deleted_at=datetime(2026, 1, 1)),
        Patient(code="P5", name="100%_さん", status="active"),
    ]
    for r in rows:
        r.special_week_active = []
    db.add_all(rows)
    await db.commit()


@pytest.mark.asyncio
async def test_patients_no_params_unchanged(client, db) -> None:
    await _seed_patients(db)
    u = await _user(db, "admin")
    res = await client.get("/api/v1/patients?limit=500&offset=0", headers=_bearer(u))
    assert res.status_code == 200
    assert [p["code"] for p in res.json()] == ["P1", "P2", "P3", "P5"]


@pytest.mark.asyncio
async def test_patients_status_and_q(client, db) -> None:
    await _seed_patients(db)
    u = await _user(db, "admin")
    h = _bearer(u)
    r = await client.get("/api/v1/patients?status=active", headers=h)
    assert [p["code"] for p in r.json()] == ["P1", "P3", "P5"]
    r = await client.get("/api/v1/patients", params={"q": "田"}, headers=h)
    assert [p["code"] for p in r.json()] == ["P1", "P2"]  # 論理削除は出ない
    r = await client.get("/api/v1/patients", params={"q": "スズキ"}, headers=h)
    assert [p["code"] for p in r.json()] == ["P3"]  # かなでも当たる
    r = await client.get("/api/v1/patients", params={"q": "田", "status": "active"}, headers=h)
    assert [p["code"] for p in r.json()] == ["P1"]
    r = await client.get("/api/v1/patients", params={"q": "%"}, headers=h)
    assert [p["code"] for p in r.json()] == ["P5"]  # ワイルドカードは文字として扱う


async def _seed_staff(db) -> list[Staff]:
    rows = [
        Staff(code="S001", name="青木 花", kana="アオキ"),
        Staff(code="S002", name="青山 太郎", status="on_leave"),
        Staff(code="S003", name="井上", status="retired"),
        Staff(code="S004", name="退職 済", deleted_at=datetime(2026, 1, 1)),
    ]
    db.add_all(rows)
    await db.commit()
    return rows


@pytest.mark.asyncio
async def test_staff_no_params_unchanged(client, db) -> None:
    await _seed_staff(db)
    u = await _user(db, "admin")
    res = await client.get("/api/v1/staff?limit=500&offset=0", headers=_bearer(u))
    assert [s["code"] for s in res.json()] == ["S001", "S002", "S003"]


@pytest.mark.asyncio
async def test_staff_q_and_include_retired(client, db) -> None:
    await _seed_staff(db)
    u = await _user(db, "admin")
    h = _bearer(u)
    r = await client.get("/api/v1/staff", params={"q": "青"}, headers=h)
    assert [s["code"] for s in r.json()] == ["S001", "S002"]
    r = await client.get("/api/v1/staff", params={"q": "アオキ"}, headers=h)
    assert [s["code"] for s in r.json()] == ["S001"]
    r = await client.get("/api/v1/staff", params={"q": "S003"}, headers=h)
    assert [s["code"] for s in r.json()] == ["S003"]
    r = await client.get("/api/v1/staff", params={"include_retired": "false"}, headers=h)
    assert [s["code"] for s in r.json()] == ["S001", "S002"]
    r = await client.get("/api/v1/staff", params={"include_retired": "true"}, headers=h)
    assert [s["code"] for s in r.json()] == ["S001", "S002", "S003", "S004"]


@pytest.mark.asyncio
async def test_staff_role_still_sees_only_self(client, db) -> None:
    rows = await _seed_staff(db)
    u = await _user(db, "staff", staff_id=rows[0].id)
    r = await client.get(
        "/api/v1/staff", params={"include_retired": "true", "q": "青"}, headers=_bearer(u)
    )
    assert [s["code"] for s in r.json()] == ["S001"]
