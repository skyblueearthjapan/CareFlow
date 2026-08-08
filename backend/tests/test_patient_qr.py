"""QR 発行 / 再発行 (Phase 5-1) のテスト.

- GET /patients/{id}/qr: 遅延発行 (NULL → token 生成・保存)、再取得で同値。
- POST /patients/{id}/qr/regenerate: qr_version+1・旧トークンが revoked 入り・
  audit_logs に回転記録・新トークンに置換。
- judge: 旧 (失効) トークンで 410、未知トークンで 404、有効トークンで従来通り。
- RBAC: staff は 403。
"""

from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.security import create_access_token, hash_password
from app.models import AuditLog, Patient, RevokedQrToken, Staff, User, Visit
from app.services.checkin.judge import JST


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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(
    db, code: str, *, lat=None, lng=None, qr_token=None, name="利用者"
) -> Patient:
    p = Patient(code=code, name=name, lat=lat, lng=lng, qr_token=qr_token)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


# ---------------------------------------------------------------------------
# GET /patients/{id}/qr — 遅延発行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_qr_lazy_generates_token(client, db) -> None:
    admin = await _make_user(db, "qr-admin@example.com", "admin")
    p = await _make_patient(db, "QR-GEN")
    assert p.qr_token is None

    res = await client.get(f"/api/v1/patients/{p.id}/qr", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert isinstance(body["token"], str) and body["token"]
    assert body["version"] == 1

    # 永続化されている (再取得で同じ token)。
    res2 = await client.get(f"/api/v1/patients/{p.id}/qr", headers=_bearer(admin))
    assert res2.status_code == 200, res2.text
    assert res2.json()["token"] == body["token"]


@pytest.mark.asyncio
async def test_get_qr_returns_existing_token(client, db) -> None:
    admin = await _make_user(db, "qr-admin2@example.com", "admin")
    p = await _make_patient(db, "QR-EXIST", qr_token="preset-token-abc")

    res = await client.get(f"/api/v1/patients/{p.id}/qr", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["token"] == "preset-token-abc"


@pytest.mark.asyncio
async def test_get_qr_unknown_patient_returns_404(client, db) -> None:
    admin = await _make_user(db, "qr-admin3@example.com", "admin")
    res = await client.get(f"/api/v1/patients/{uuid4()}/qr", headers=_bearer(admin))
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_get_qr_staff_returns_403(client, db) -> None:
    staff_user = await _make_user(db, "qr-staff@example.com", "staff")
    p = await _make_patient(db, "QR-RBAC")
    res = await client.get(f"/api/v1/patients/{p.id}/qr", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# POST /patients/{id}/qr/regenerate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_rotates_token_and_records(client, db) -> None:
    admin = await _make_user(db, "qr-regen@example.com", "admin")
    p = await _make_patient(db, "QR-REGEN", qr_token="old-token-xyz")
    assert p.qr_version == 1
    pid = p.id  # API は別 session でコミットするため PK を先に確保。

    res = await client.post(f"/api/v1/patients/{pid}/qr/regenerate", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token"] != "old-token-xyz"
    assert body["version"] == 2

    # 旧トークンが revoked 入り (fresh オブジェクトなので identity map の影響なし)。
    revoked = await db.scalar(select(RevokedQrToken).where(RevokedQrToken.token == "old-token-xyz"))
    assert revoked is not None
    assert revoked.patient_id == pid

    # patients.qr_token / qr_version が更新済み (populate_existing で最新を読む)。
    refreshed = await db.scalar(
        select(Patient).where(Patient.id == pid).execution_options(populate_existing=True)
    )
    assert refreshed.qr_token == body["token"]
    assert refreshed.qr_version == 2

    # audit_logs に回転記録。
    audit = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "patient_qr_regenerate",
            AuditLog.target_id == str(pid),
        )
    )
    assert audit is not None
    assert audit.before["qr_token"] == "old-token-xyz"
    assert audit.after["qr_token"] == body["token"]


@pytest.mark.asyncio
async def test_regenerate_from_null_token_does_not_revoke(client, db) -> None:
    """未発行 (qr_token NULL) からの再発行は revoked 行を作らない."""
    admin = await _make_user(db, "qr-regen-null@example.com", "admin")
    p = await _make_patient(db, "QR-REGEN-NULL")

    res = await client.post(f"/api/v1/patients/{p.id}/qr/regenerate", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["version"] == 2

    count = await db.scalar(
        select(func.count()).select_from(RevokedQrToken).where(RevokedQrToken.patient_id == p.id)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_regenerate_staff_returns_403(client, db) -> None:
    staff_user = await _make_user(db, "qr-regen-staff@example.com", "staff")
    p = await _make_patient(db, "QR-REGEN-RBAC", qr_token="tok-rbac")
    res = await client.post(f"/api/v1/patients/{p.id}/qr/regenerate", headers=_bearer(staff_user))
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# judge: 旧トークン 410 / 未知 404 / 有効 従来通り
# ---------------------------------------------------------------------------


async def _make_staff_user(db, email: str) -> tuple[Staff, User]:
    staff = Staff(name="担当ヘルパー")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    user = await _make_user(db, email, "staff", staff_id=staff.id)
    return staff, user


async def _make_visit(db, patient_id, staff_id) -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=datetime.now(JST).date(),
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@pytest.mark.asyncio
async def test_checkin_with_revoked_qr_returns_410(client, db) -> None:
    """再発行で失効した旧トークンでの打刻は 410 Gone."""
    admin = await _make_user(db, "qr-410-admin@example.com", "admin")
    staff, staff_user = await _make_staff_user(db, "qr-410-staff@example.com")
    p = await _make_patient(
        db, "QR-410", lat=35.0, lng=139.0, qr_token="rotate-old", name="山田 太郎"
    )
    visit = await _make_visit(db, p.id, staff.id)

    # admin が再発行 → rotate-old が失効する。
    regen = await client.post(f"/api/v1/patients/{p.id}/qr/regenerate", headers=_bearer(admin))
    assert regen.status_code == 200, regen.text

    # staff が旧ステッカー (rotate-old) で打刻 → 410。
    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(staff_user),
        json={"qr_token": "rotate-old", "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 410, res.text
    assert "山田 太郎" in res.json()["detail"]


@pytest.mark.asyncio
async def test_checkin_with_other_patient_revoked_qr_omits_name_410(client, db) -> None:
    """担当外患者の旧 (失効) トークンでの打刻は 410 だが患者名を漏らさない (氏名スコープ)."""
    admin = await _make_user(db, "qr-410-scope-admin@example.com", "admin")
    staff, staff_user = await _make_staff_user(db, "qr-410-scope-staff@example.com")
    # visit が属する患者 A。
    patient_a = await _make_patient(db, "QR-410-A", lat=35.0, lng=139.0, name="山田 太郎")
    visit = await _make_visit(db, patient_a.id, staff.id)

    # 別患者 B の旧トークンを失効させる。
    patient_b = await _make_patient(db, "QR-410-B", qr_token="other-old", name="鈴木 次郎")
    regen = await client.post(
        f"/api/v1/patients/{patient_b.id}/qr/regenerate", headers=_bearer(admin)
    )
    assert regen.status_code == 200, regen.text

    # staff が B の旧トークンで A の visit に打刻 → 410 だが B の氏名は出ない。
    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(staff_user),
        json={"qr_token": "other-old", "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 410, res.text
    assert "鈴木 次郎" not in res.json()["detail"]


@pytest.mark.asyncio
async def test_checkin_with_unknown_qr_still_404(client, db) -> None:
    """失効履歴にも無い完全な未知トークンは従来どおり 404 (非回帰)."""
    staff, staff_user = await _make_staff_user(db, "qr-404-staff@example.com")
    p = await _make_patient(db, "QR-404", lat=35.0, lng=139.0)
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(staff_user),
        json={"qr_token": "never-existed"},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_checkin_with_new_qr_after_regenerate_succeeds(client, db) -> None:
    """再発行後の新トークンでの打刻は従来通り成立する."""
    admin = await _make_user(db, "qr-new-admin@example.com", "admin")
    staff, staff_user = await _make_staff_user(db, "qr-new-staff@example.com")
    p = await _make_patient(db, "QR-NEW", lat=35.0, lng=139.0, qr_token="old-1")
    visit = await _make_visit(db, p.id, staff.id)

    regen = await client.post(f"/api/v1/patients/{p.id}/qr/regenerate", headers=_bearer(admin))
    assert regen.status_code == 200, regen.text
    new_token = regen.json()["token"]

    res = await client.post(
        f"/api/v1/visits/{visit.id}/checkin",
        headers=_bearer(staff_user),
        json={"qr_token": new_token, "lat": 35.0, "lng": 139.0},
    )
    assert res.status_code == 200, res.text
    assert res.json()["latest_checkin"]["checkin_source"] == "qr"
