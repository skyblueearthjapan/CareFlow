"""Phase G-21 T2: 同住所候補 + 同住所紐付け CRUD API tests.

検証観点:
  T2.5  GET /candidates で 同 bucket 患者リスト
  T2.6  candidate に住所文字列不一致は除外
  T2.7  POST /links で pair_mode 設定 (blocked/required)
  T2.8  POST /links で pair_mode=preferred → DB から削除 (= 行が消える)
  T2.8b POST /links で preferred (audit_log の before/after 検証 / H1)
  T2.9  a > b 順で POST しても DB は a < b で保存
  T2.10 DELETE /links/{a}/{b}
  T2.11 GET /links?patient_id=X で a/b 両方向の link を返す
  T2.12 非 admin で 403
  T2.13 candidates が全角/末尾空白の住所差を NFKC で吸収 (M2)
  T2.14 staff は担当患者を含む link のみ (M5)
  T2.15 delete_patient で同住所 link が物理削除 (M3)
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, User
from app.models.audit_log import AuditLog
from app.models.patient_same_address_link import PatientSameAddressLink

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("pw"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(
    db,
    *,
    code: str,
    lat: float | None = 35.6500,
    lng: float | None = 140.1000,
    address: str | None = "千葉県千葉市稲毛区1-2-3",
) -> Patient:
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=lat,
        lng=lng,
        address=address,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# T2.5: 同 bucket 患者リスト取得 (pair_mode はデフォルト preferred)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_5_candidates_returns_same_bucket_patients(client, db) -> None:
    admin = await _make_user(db, email="cand-admin@example.com", role="admin")
    base = await _make_patient(db, code="C-BASE")
    same1 = await _make_patient(db, code="C-SAME-1")
    same2 = await _make_patient(db, code="C-SAME-2")
    # 異なる bucket (= lat/lng が 0.01 以上ずれる)
    far = await _make_patient(  # noqa: F841
        db,
        code="C-FAR",
        lat=36.0,
        lng=141.0,
        address="千葉県千葉市稲毛区1-2-3",
    )

    res = await client.get(
        f"/api/v1/patients/{base.id}/same-address-candidates",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    returned_ids = {row["patient_id"] for row in rows}
    assert str(same1.id) in returned_ids
    assert str(same2.id) in returned_ids
    assert str(far.id) not in returned_ids  # 異 bucket は除外
    # base 自身は除外
    assert str(base.id) not in returned_ids
    # link 無し → preferred
    for row in rows:
        assert row["pair_mode"] == "preferred"


# ---------------------------------------------------------------------------
# T2.6: 住所文字列不一致は除外
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_6_candidates_exclude_address_mismatch(client, db) -> None:
    admin = await _make_user(db, email="cand-addr@example.com", role="admin")
    base = await _make_patient(db, code="ADDR-BASE", address="A-1-2-3")
    # 同 bucket (lat/lng ほぼ同じ) だが address 文字列が違う → 除外される
    diff_addr = await _make_patient(
        db, code="ADDR-DIFF", address="B-9-9-9", lat=35.6501, lng=140.1001
    )
    # 同 bucket かつ address 一致 → 含む
    same_addr = await _make_patient(
        db, code="ADDR-SAME", address="A-1-2-3", lat=35.6502, lng=140.1002
    )

    res = await client.get(
        f"/api/v1/patients/{base.id}/same-address-candidates",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    returned_ids = {row["patient_id"] for row in res.json()}
    assert str(same_addr.id) in returned_ids
    assert str(diff_addr.id) not in returned_ids


# ---------------------------------------------------------------------------
# T2.7: POST /links で pair_mode 設定 (blocked / required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_7_post_link_sets_blocked_or_required(client, db) -> None:
    admin = await _make_user(db, email="link-set@example.com", role="admin")
    p1 = await _make_patient(db, code="L-1")
    p2 = await _make_patient(db, code="L-2")
    a, b = sorted([p1.id, p2.id], key=lambda u: str(u))

    # blocked
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
            "note": "block-test",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["pair_mode"] == "blocked"
    assert body["note"] == "block-test"
    assert body["decided_by_user_id"] == str(admin.id)

    # required に上書き (UPSERT)
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "required",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["pair_mode"] == "required"

    # DB 上は 1 行のみ
    rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                PatientSameAddressLink.patient_a_id == a,
                PatientSameAddressLink.patient_b_id == b,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].pair_mode == "required"


# ---------------------------------------------------------------------------
# T2.8: POST /links で pair_mode=preferred → DB から削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_8_post_preferred_deletes_row(client, db) -> None:
    admin = await _make_user(db, email="link-pref@example.com", role="admin")
    p1 = await _make_patient(db, code="PREF-1")
    p2 = await _make_patient(db, code="PREF-2")
    a, b = sorted([p1.id, p2.id], key=lambda u: str(u))

    # まず blocked にする
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
        },
    )
    assert res.status_code == 200, res.text

    # preferred で POST → DELETE 相当
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "preferred",
        },
    )
    assert res.status_code == 200, res.text
    # body は null (= JSON で None)
    assert res.json() is None

    rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                PatientSameAddressLink.patient_a_id == a,
                PatientSameAddressLink.patient_b_id == b,
            )
        )
    ).all()
    assert rows == []


# ---------------------------------------------------------------------------
# T2.9: a > b 順で POST しても DB は a < b で保存
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_9_post_reorders_a_lt_b(client, db) -> None:
    admin = await _make_user(db, email="link-order@example.com", role="admin")
    p1 = await _make_patient(db, code="ORD-1")
    p2 = await _make_patient(db, code="ORD-2")
    lo, hi = sorted([p1.id, p2.id], key=lambda u: str(u))

    # わざと逆順 (a=hi, b=lo) で POST
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(hi),
            "patient_b_id": str(lo),
            "pair_mode": "required",
        },
    )
    assert res.status_code == 200, res.text

    # DB 上は a<b で保存されている
    rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                PatientSameAddressLink.patient_a_id == lo,
                PatientSameAddressLink.patient_b_id == hi,
            )
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].patient_a_id == lo
    assert rows[0].patient_b_id == hi


# ---------------------------------------------------------------------------
# T2.10: DELETE /links/{a}/{b}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_10_delete_link(client, db) -> None:
    admin = await _make_user(db, email="link-del@example.com", role="admin")
    p1 = await _make_patient(db, code="DEL-1")
    p2 = await _make_patient(db, code="DEL-2")
    a, b = sorted([p1.id, p2.id], key=lambda u: str(u))

    # まず作成
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
        },
    )
    assert res.status_code == 200, res.text

    # DELETE
    res = await client.delete(
        f"/api/v1/patient-same-address-links/{a}/{b}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                PatientSameAddressLink.patient_a_id == a,
                PatientSameAddressLink.patient_b_id == b,
            )
        )
    ).all()
    assert rows == []

    # 冪等 — もう一度 DELETE しても 204
    res = await client.delete(
        f"/api/v1/patient-same-address-links/{a}/{b}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204


# ---------------------------------------------------------------------------
# T2.11: GET /links?patient_id=X で a/b 両方向の link を返す
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_11_list_links_filters_both_sides(client, db) -> None:
    admin = await _make_user(db, email="link-list@example.com", role="admin")
    target = await _make_patient(db, code="LIST-T")
    partner1 = await _make_patient(db, code="LIST-A")
    partner2 = await _make_patient(db, code="LIST-B")
    other = await _make_patient(db, code="LIST-X")
    other2 = await _make_patient(db, code="LIST-Y")

    # target を含む link を 2 件作成 (片方は target が a 側, もう片方は b 側になるよう
    # ID 順に依存. _normalize_pair が a<b にするため、ペアごとに判定して挿入).
    for partner in (partner1, partner2):
        res = await client.post(
            "/api/v1/patient-same-address-links",
            headers=_bearer(admin),
            json={
                "patient_a_id": str(target.id),
                "patient_b_id": str(partner.id),
                "pair_mode": "blocked",
            },
        )
        assert res.status_code == 200, res.text

    # target を含まない link (= フィルタで除外されること)
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(other.id),
            "patient_b_id": str(other2.id),
            "pair_mode": "required",
        },
    )
    assert res.status_code == 200, res.text

    # filter 指定で 2 件返る
    res = await client.get(
        f"/api/v1/patient-same-address-links?patient_id={target.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 2
    for row in rows:
        assert target.id in (
            UUID(row["patient_a_id"]),
            UUID(row["patient_b_id"]),
        )

    # フィルタなしなら全 3 件
    res = await client.get(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 3


# ---------------------------------------------------------------------------
# T2.12: 非 admin で 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_12_staff_cannot_mutate_links(client, db) -> None:
    admin = await _make_user(db, email="link-rbac-admin@example.com", role="admin")
    staff_user = await _make_user(db, email="link-rbac-staff@example.com", role="staff")
    p1 = await _make_patient(db, code="RBAC-1")
    p2 = await _make_patient(db, code="RBAC-2")
    a, b = sorted([p1.id, p2.id], key=lambda u: str(u))

    # staff は POST 不可
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(staff_user),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
        },
    )
    assert res.status_code == 403, res.text

    # 一度 admin で作成
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
        },
    )
    assert res.status_code == 200, res.text

    # staff は DELETE 不可
    res = await client.delete(
        f"/api/v1/patient-same-address-links/{a}/{b}",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 403, res.text

    # staff は GET は可能 (read-only)
    res = await client.get(
        "/api/v1/patient-same-address-links",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# T2.8b (H1): preferred の audit_log 検証 — 既存行 / 既存行なし両方
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_8b_preferred_writes_audit_log(client, db) -> None:
    """H1: preferred で POST した時に audit_log が必ず残る.

    * 既存行 (blocked) があった状態で preferred → 削除 + audit (before=blocked).
    * 既存行が無い状態で preferred → 行は依然なし + audit (before=None) も記録.
    * C1: target_id は str(a) のみ (UUID 2 個 = 73 文字で String(64) overflow 防止).
    """
    admin = await _make_user(db, email="link-pref-audit@example.com", role="admin")
    p1 = await _make_patient(db, code="PREF-AUDIT-1")
    p2 = await _make_patient(db, code="PREF-AUDIT-2")
    a, b = sorted([p1.id, p2.id], key=lambda u: str(u))

    # ---- ケース 1: 既存行あり → preferred で削除 ----
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "blocked",
            "note": "init",
        },
    )
    assert res.status_code == 200, res.text

    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a),
            "patient_b_id": str(b),
            "pair_mode": "preferred",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json() is None

    rows = (
        await db.scalars(
            select(AuditLog)
            .where(
                AuditLog.action == "patient_same_address_link_set",
                AuditLog.target_id == str(a),
            )
            .order_by(AuditLog.id)
        )
    ).all()
    assert len(rows) == 2  # blocked への set + preferred への戻し
    # 1 件目: blocked への新規 set (before=None, after=blocked)
    assert rows[0].before is None
    assert rows[0].after == {"peer_id": str(b), "pair_mode": "blocked", "note": "init"}
    # 2 件目: preferred (before=blocked, after=preferred)
    assert rows[1].before == {"peer_id": str(b), "pair_mode": "blocked", "note": "init"}
    assert rows[1].after == {"peer_id": str(b), "pair_mode": "preferred"}
    # M4: target_id は 36 文字以内 (= String(64) overflow しない)
    for row in rows:
        assert len(row.target_id) <= 64

    # ---- ケース 2: 既存行なしで preferred → 行は無いまま audit のみ残る ----
    p3 = await _make_patient(db, code="PREF-AUDIT-3")
    p4 = await _make_patient(db, code="PREF-AUDIT-4")
    a2, b2 = sorted([p3.id, p4.id], key=lambda u: str(u))

    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a2),
            "patient_b_id": str(b2),
            "pair_mode": "preferred",
        },
    )
    assert res.status_code == 200, res.text
    assert res.json() is None

    rows2 = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.action == "patient_same_address_link_set",
                AuditLog.target_id == str(a2),
            )
        )
    ).all()
    assert len(rows2) == 1
    assert rows2[0].before is None
    assert rows2[0].after == {"peer_id": str(b2), "pair_mode": "preferred"}

    # DB 上は行が存在しない (preferred はデフォルトなので保存しない).
    db_rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                PatientSameAddressLink.patient_a_id == a2,
                PatientSameAddressLink.patient_b_id == b2,
            )
        )
    ).all()
    assert db_rows == []


# ---------------------------------------------------------------------------
# T2.13 (M2): NFKC + strip 正規化で全角/末尾空白は同住所扱い
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_13_candidates_nfkc_normalizes_address(client, db) -> None:
    """M2: 半角 / 全角 / 末尾空白の差は NFKC + strip で吸収."""
    admin = await _make_user(db, email="cand-nfkc@example.com", role="admin")
    # base: 半角数字 + ハイフン
    base = await _make_patient(db, code="NFKC-BASE", address="千葉県千葉市稲毛区1-2-3")
    # 同住所 (全角数字 + 末尾空白)
    same_fullwidth = await _make_patient(
        db,
        code="NFKC-FW",
        address="千葉県千葉市稲毛区1-2-3  ",  # 全角 1, 全角 -, 末尾空白
        lat=35.6501,
        lng=140.1001,
    )

    res = await client.get(
        f"/api/v1/patients/{base.id}/same-address-candidates",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    returned_ids = {row["patient_id"] for row in res.json()}
    assert str(same_fullwidth.id) in returned_ids


# ---------------------------------------------------------------------------
# T2.14 (M5): staff は担当患者を含む link のみ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_14_staff_list_filtered_to_assigned_patients(client, db) -> None:
    """M5: staff は visit / assignment で担当する患者を含む link のみ見える."""
    from datetime import date, time

    from app.models.staff import Staff
    from app.models.visit import Visit

    admin = await _make_user(db, email="link-staff-list-admin@example.com", role="admin")

    # staff (= staff_id 紐付き user)
    s = Staff(code="LST-S", name="L-staff", status="active")
    db.add(s)
    await db.commit()
    await db.refresh(s)
    staff_user = User(
        email="link-staff-list-stf@example.com",
        password_hash=hash_password("pw"),
        role="staff",
        staff_id=s.id,
    )
    db.add(staff_user)
    await db.commit()
    await db.refresh(staff_user)

    # 担当患者 (= visit.primary_staff_id) + 非担当患者を 2 ペア用意
    p_assigned = await _make_patient(db, code="LST-MINE")
    p_partner = await _make_patient(db, code="LST-PART")
    p_other = await _make_patient(db, code="LST-OTH")
    p_other2 = await _make_patient(db, code="LST-OTH2")

    # 担当 visit を 1 件挿入
    v = Visit(
        patient_id=p_assigned.id,
        primary_staff_id=s.id,
        visit_date=date(2026, 5, 5),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="visit",
        status="planned",
        source="manual",
    )
    db.add(v)
    await db.commit()

    # link を 2 件作る (1 件は担当患者を含む / 1 件は無関係)
    a1, b1 = sorted([p_assigned.id, p_partner.id], key=lambda u: str(u))
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a1),
            "patient_b_id": str(b1),
            "pair_mode": "blocked",
        },
    )
    assert res.status_code == 200, res.text

    a2, b2 = sorted([p_other.id, p_other2.id], key=lambda u: str(u))
    res = await client.post(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
        json={
            "patient_a_id": str(a2),
            "patient_b_id": str(b2),
            "pair_mode": "required",
        },
    )
    assert res.status_code == 200, res.text

    # staff の GET は担当 link 1 件のみ
    res = await client.get(
        "/api/v1/patient-same-address-links",
        headers=_bearer(staff_user),
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    returned = {UUID(rows[0]["patient_a_id"]), UUID(rows[0]["patient_b_id"])}
    assert p_assigned.id in returned

    # admin は 2 件見える (= staff 以外には絞り込みかからない)
    res = await client.get(
        "/api/v1/patient-same-address-links",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 2


# ---------------------------------------------------------------------------
# T2.15 (M3): delete_patient で同住所 link が物理削除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t2_15_delete_patient_cascades_links(client, db) -> None:
    """M3: patient soft-delete で a 側 / b 側両方の link 行を物理削除."""
    admin = await _make_user(db, email="link-cas-admin@example.com", role="admin")
    p1 = await _make_patient(db, code="CAS-1")
    p2 = await _make_patient(db, code="CAS-2")
    p3 = await _make_patient(db, code="CAS-3")

    # p1-p2 ペア + p1-p3 ペア (= p1 を a/b 側両方に登場させる)
    a12, b12 = sorted([p1.id, p2.id], key=lambda u: str(u))
    a13, b13 = sorted([p1.id, p3.id], key=lambda u: str(u))
    for a, b in [(a12, b12), (a13, b13)]:
        res = await client.post(
            "/api/v1/patient-same-address-links",
            headers=_bearer(admin),
            json={
                "patient_a_id": str(a),
                "patient_b_id": str(b),
                "pair_mode": "blocked",
            },
        )
        assert res.status_code == 200, res.text

    # 削除前: 2 件
    rows = (await db.scalars(select(PatientSameAddressLink))).all()
    assert {(r.patient_a_id, r.patient_b_id) for r in rows} == {
        (a12, b12),
        (a13, b13),
    }

    # p1 を soft delete
    res = await client.delete(
        f"/api/v1/patients/{p1.id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 204, res.text

    # 削除後: p1 に紐付く link は 0 件
    rows = (
        await db.scalars(
            select(PatientSameAddressLink).where(
                (PatientSameAddressLink.patient_a_id == p1.id)
                | (PatientSameAddressLink.patient_b_id == p1.id)
            )
        )
    ).all()
    assert rows == []
