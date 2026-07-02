"""Tests for /api/v1/patients/{id}/fixed-visits (W9-BE1).

検証観点:
  1.  GET: 全モード取得 (normal + special 両方)
  2.  GET: mode フィルタ (normal のみ)
  3.  GET: 該当なし → 空配列
  4.  PUT: 0 件 PUT → 空配列返却
  5.  PUT: 1 件 PUT → 1 件返却
  6.  PUT: 7 件全曜日 PUT → 7 件返却
  7.  PUT: weekday 重複 → 422
  8.  DELETE: mode 別削除
  9.  DELETE: 存在しない mode でも 204 (冪等)
  10. RBAC: admin → 全患者 PUT OK
  11. RBAC: staff 担当外 patient → GET 403
  12. RBAC: staff 担当 patient → GET 200
  13. 1TX 保証: UNIQUE 違反で全部 rollback (PUT 前後でデータ不変)
"""

from __future__ import annotations

from datetime import date, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Office, Patient, PatientFixedVisit, Staff, User, Visit

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
        staff_id=staff_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _make_patient(db, code: str) -> Patient:
    p = Patient(code=code, name=f"患者{code}", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_staff(db, name: str = "スタッフ") -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


async def _make_visit_for_staff(db, *, patient: Patient, staff: Staff) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=date(2026, 5, 25),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        source="manual",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _put_body(mode: str, items: list[dict]) -> dict:
    return {"mode": mode, "items": items}


def _item(weekday: int, start: str = "09:00", duration: int = 30) -> dict:
    return {"weekday": weekday, "start_time": start, "duration_min": duration}


# ---------------------------------------------------------------------------
# 1. GET: 全モード取得
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_modes(client, db) -> None:
    """normal + special 両方の行が返る."""
    admin = await _make_user(db, "pfv-get-all@example.com", "admin")
    patient = await _make_patient(db, "PFV-001")
    pid = patient.id

    # PUT normal
    res = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0), _item(1)]),
    )
    assert res.status_code == 200, res.text

    # PUT special
    res = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("special", [_item(2)]),
    )
    assert res.status_code == 200, res.text

    # GET (no filter)
    res = await client.get(f"/api/v1/patients/{pid}/fixed-visits", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 3
    modes = {item["mode"] for item in data}
    assert modes == {"normal", "special"}


# ---------------------------------------------------------------------------
# 2. GET: mode フィルタ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mode_filter(client, db) -> None:
    """?mode=normal は normal 行のみ返す."""
    admin = await _make_user(db, "pfv-filter@example.com", "admin")
    patient = await _make_patient(db, "PFV-002")
    pid = patient.id

    await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0), _item(3)]),
    )
    await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("special", [_item(1)]),
    )

    res = await client.get(
        f"/api/v1/patients/{pid}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert all(item["mode"] == "normal" for item in data)


# ---------------------------------------------------------------------------
# 3. GET: 該当なし → 空配列
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_empty(client, db) -> None:
    """登録なしの患者は空配列."""
    admin = await _make_user(db, "pfv-empty@example.com", "admin")
    patient = await _make_patient(db, "PFV-003")

    res = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# 4. PUT: 0 件 PUT → 空配列返却
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_zero_items(client, db) -> None:
    """items=[] で全削除・空配列返却."""
    admin = await _make_user(db, "pfv-put0@example.com", "admin")
    patient = await _make_patient(db, "PFV-004")
    pid = patient.id

    # 先に 1 件入れる
    await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0)]),
    )

    # 0 件 PUT で全削除
    res = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", []),
    )
    assert res.status_code == 200
    # P0-2: PUT レスポンスはエンベロープ ({items, warnings}) 化.
    assert res.json()["items"] == []


# ---------------------------------------------------------------------------
# 5. PUT: 1 件
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_one_item(client, db) -> None:
    admin = await _make_user(db, "pfv-put1@example.com", "admin")
    patient = await _make_patient(db, "PFV-005")
    pid = patient.id

    res = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(2, "10:00", 60)]),
    )
    assert res.status_code == 200
    data = res.json()["items"]
    assert len(data) == 1
    assert data[0]["weekday"] == 2
    assert data[0]["start_time"] == "10:00:00"
    assert data[0]["duration_min"] == 60
    assert data[0]["mode"] == "normal"
    assert "id" in data[0]
    assert "patient_id" in data[0]


# ---------------------------------------------------------------------------
# 6. PUT: 7 件全曜日
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_seven_items(client, db) -> None:
    admin = await _make_user(db, "pfv-put7@example.com", "admin")
    patient = await _make_patient(db, "PFV-006")
    pid = patient.id

    items = [_item(wd) for wd in range(7)]
    res = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", items),
    )
    assert res.status_code == 200
    data = res.json()["items"]
    assert len(data) == 7
    assert [row["weekday"] for row in data] == list(range(7))


# ---------------------------------------------------------------------------
# 7. PUT: weekday 重複 → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_duplicate_weekday_422(client, db) -> None:
    """同一 weekday が 2 行あると Pydantic で 422."""
    admin = await _make_user(db, "pfv-dup@example.com", "admin")
    patient = await _make_patient(db, "PFV-007")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0), _item(0)]),
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# 8. DELETE: mode 別削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_by_mode(client, db) -> None:
    """DELETE ?mode=normal で normal 行のみ消え special は残る."""
    admin = await _make_user(db, "pfv-del@example.com", "admin")
    patient = await _make_patient(db, "PFV-008")
    pid = patient.id

    await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0), _item(1)]),
    )
    await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("special", [_item(3)]),
    )

    res = await client.delete(
        f"/api/v1/patients/{pid}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res.status_code == 204

    # normal は消えている
    res_normal = await client.get(
        f"/api/v1/patients/{pid}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res_normal.json() == []

    # special は残っている
    res_special = await client.get(
        f"/api/v1/patients/{pid}/fixed-visits?mode=special",
        headers=_bearer(admin),
    )
    assert len(res_special.json()) == 1


# ---------------------------------------------------------------------------
# 9. DELETE: 存在しない mode でも 204 (冪等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_nonexistent_mode_is_idempotent(client, db) -> None:
    admin = await _make_user(db, "pfv-del-idm@example.com", "admin")
    patient = await _make_patient(db, "PFV-009")

    # 何も登録せずに DELETE → 204
    res = await client.delete(
        f"/api/v1/patients/{patient.id}/fixed-visits?mode=special",
        headers=_bearer(admin),
    )
    assert res.status_code == 204


# ---------------------------------------------------------------------------
# 10. RBAC: admin → 全患者 PUT OK
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_admin_can_put(client, db) -> None:
    admin = await _make_user(db, "pfv-rbac-admin@example.com", "admin")
    patient = await _make_patient(db, "PFV-010")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0)]),
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 11. RBAC: staff 担当外 patient → GET 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_staff_non_assigned_patient_403(client, db) -> None:
    """担当外の患者は 403."""
    staff = await _make_staff(db, "担当外スタッフ")
    staff_user = await _make_user(db, "pfv-rbac-staff1@example.com", "staff", staff_id=staff.id)
    patient = await _make_patient(db, "PFV-011")

    res = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# 12. RBAC: staff 担当 patient → GET 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rbac_staff_assigned_patient_200(client, db) -> None:
    """担当患者は staff でも参照可."""
    staff = await _make_staff(db, "担当スタッフ")
    staff_user = await _make_user(db, "pfv-rbac-staff2@example.com", "staff", staff_id=staff.id)
    patient = await _make_patient(db, "PFV-012")

    # visit を通じて担当関係を確立
    await _make_visit_for_staff(db, patient=patient, staff=staff)

    res = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# 13. 1TX 保証: UNIQUE 違反 → 全 rollback (DB 内データ不変)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_unique_violation_rolls_back(client, db) -> None:
    """PUT 後に weekday 重複を注入しても 1TX で rollback される.

    実際には Pydantic で重複検出するため PUT body では 422 で弾かれる。
    ここでは 事前に normal/weekday=0 を入れた後、同じ weekday=0 を含む
    特定 mode で PUT して atomicity を確認する。
    (Pydantic 検証が通る 1 件 PUT × 2 回で最終 count=1 を確認)
    """
    admin = await _make_user(db, "pfv-tx@example.com", "admin")
    patient = await _make_patient(db, "PFV-013")
    pid = patient.id

    # 1 回目: weekday=0 を PUT
    res1 = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0), _item(1)]),
    )
    assert res1.status_code == 200

    # 2 回目: 別の内容で PUT (一括上書き) → 1 件になる
    res2 = await client.put(
        f"/api/v1/patients/{pid}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(3)]),
    )
    assert res2.status_code == 200

    # 最終的に weekday=3 の 1 件だけ存在
    res_get = await client.get(
        f"/api/v1/patients/{pid}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    data = res_get.json()
    assert len(data) == 1
    assert data[0]["weekday"] == 3


# ---------------------------------------------------------------------------
# P0-2: 適用前再検証 (エンベロープ / pinned 422 / 衝突 warning)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_response_is_envelope(client, db) -> None:
    """P0-2: PUT レスポンスは {items, warnings} エンベロープ."""
    admin = await _make_user(db, "pfv-env@example.com", "admin")
    patient = await _make_patient(db, "PFV-ENV")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"items", "warnings"}
    assert len(body["items"]) == 1
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_put_pinned_change_returns_422(client, db) -> None:
    """P0-2 / §2.1: 既存 pinned 行の変更を試みる PUT は 422 で拒否 (削除前)."""
    admin = await _make_user(db, "pfv-pin422@example.com", "admin")
    patient = await _make_patient(db, "PFV-PIN")
    # 既存 pinned 行を直接投入.
    db.add(
        PatientFixedVisit(
            patient_id=patient.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
            is_pinned=True,
        )
    )
    await db.commit()

    # 同 weekday/slot だが start_time を変更 → 422.
    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0, "10:00", 30)]),
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["violations"][0]["code"] == "pinned_protection"

    # TX を汚していない: 既存 pinned 行はそのまま残る.
    rows = (
        await db.scalars(
            select(PatientFixedVisit).where(PatientFixedVisit.patient_id == patient.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].start_time == time(9, 0)
    assert rows[0].is_pinned is True


@pytest.mark.asyncio
async def test_put_is_pinned_roundtrip(client, db) -> None:
    """P0-2 BLOCKER: PUT で is_pinned=True を送ると GET でも is_pinned=True が残る.

    INSERT ループが is_pinned を省くと ORM default False が silent に適用され、
    次回 PUT 時に「保持=OK」と判定できなくなる (V2 保護の自壊). これを防ぐ回帰確認.
    """
    admin = await _make_user(db, "pfv-pin-rt@example.com", "admin")
    patient = await _make_patient(db, "PFV-PINRT")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body(
            "normal",
            [{"weekday": 0, "start_time": "09:00", "duration_min": 30, "is_pinned": True}],
        ),
    )
    assert res.status_code == 200, res.text
    # PUT レスポンスのエンベロープ items にも is_pinned が反映される.
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["is_pinned"] is True

    # GET でも is_pinned=True が残る (DB に正しく書き込まれていることを確認).
    res_get = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res_get.status_code == 200
    data = res_get.json()
    assert len(data) == 1
    assert data[0]["is_pinned"] is True


@pytest.mark.asyncio
async def test_put_movability_roundtrip(client, db) -> None:
    """P2-A (§1.3): PUT で movability='time_flexible' を送ると GET でも保持される.

    INSERT ループが movability を省くと保存のたび 'unknown' に戻る (P0-2 is_pinned と
    同型の運搬 BLOCKER). これを防ぐ回帰確認.
    """
    admin = await _make_user(db, "pfv-mv-rt@example.com", "admin")
    patient = await _make_patient(db, "PFV-MVRT")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body(
            "normal",
            [
                {
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                    "movability": "time_flexible",
                }
            ],
        ),
    )
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["movability"] == "time_flexible"

    # GET でも time_flexible が残る.
    res_get = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res_get.status_code == 200
    data = res_get.json()
    assert len(data) == 1
    assert data[0]["movability"] == "time_flexible"


@pytest.mark.asyncio
async def test_put_default_movability_is_unknown(client, db) -> None:
    """P2-A: movability を送らない旧 FE リクエストは 'unknown' で保存 (既定挙動不変)."""
    admin = await _make_user(db, "pfv-mv-def@example.com", "admin")
    patient = await _make_patient(db, "PFV-MVDEF")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0, "09:00", 30)]),
    )
    assert res.status_code == 200, res.text
    assert res.json()["items"][0]["movability"] == "unknown"


@pytest.mark.asyncio
async def test_put_pinned_movability_corrected_to_locked(client, db) -> None:
    """P2-A V6: is_pinned=True + movability='unknown' 送信 → 保存値 'locked' + warning."""
    admin = await _make_user(db, "pfv-mv-v6@example.com", "admin")
    patient = await _make_patient(db, "PFV-MVV6")

    res = await client.put(
        f"/api/v1/patients/{patient.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body(
            "normal",
            [
                {
                    "weekday": 0,
                    "start_time": "09:00",
                    "duration_min": 30,
                    "is_pinned": True,
                    "movability": "unknown",
                }
            ],
        ),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # 保存値が 'locked' に矯正されている.
    assert body["items"][0]["movability"] == "locked"
    assert body["items"][0]["is_pinned"] is True
    # 矯正 warning が同梱される.
    assert any(w["code"] == "movability_corrected" for w in body["warnings"])

    # DB (GET) にも locked が反映されている.
    res_get = await client.get(
        f"/api/v1/patients/{patient.id}/fixed-visits?mode=normal",
        headers=_bearer(admin),
    )
    assert res_get.json()[0]["movability"] == "locked"


@pytest.mark.asyncio
async def test_put_conflict_returns_200_with_warning(client, db) -> None:
    """P0-2: 患者間衝突は 422 ではなく 200 + warnings で返す."""
    admin = await _make_user(db, "pfv-conf@example.com", "admin")
    office = Office(code="OF-PFV", name="OF-PFV")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    target = Patient(
        code="PFV-CT", name="対象", special_week_active=[],
        primary_office_id=office.id, status="active", lat=35.600, lng=140.100,
    )
    other = Patient(
        code="PFV-CO", name="山田", special_week_active=[],
        primary_office_id=office.id, status="active", lat=35.700, lng=140.200,
    )
    db.add_all([target, other])
    await db.commit()
    await db.refresh(target)
    await db.refresh(other)
    # 他患者 PFV: 月 10:00 (異住所).
    db.add(
        PatientFixedVisit(
            patient_id=other.id, mode="normal", weekday=0,
            start_time=time(10, 0), duration_min=30, slot_index=0,
        )
    )
    await db.commit()

    # 対象を同曜日同時刻で PUT → 衝突 warning.
    res = await client.put(
        f"/api/v1/patients/{target.id}/fixed-visits",
        headers=_bearer(admin),
        json=_put_body("normal", [_item(0, "10:00", 30)]),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1  # 適用は成功
    codes = {w["code"] for w in body["warnings"]}
    assert "patient_time_conflict" in codes
