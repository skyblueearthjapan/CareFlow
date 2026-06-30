"""QR 訪問チェックイン Phase 5-3 — 訪問「確認済み」(visit review) のテスト.

- POST /visits/{id}/review: 確認済み upsert (admin 200 / staff 403)。
- DELETE /visits/{id}/review: undo (行削除・冪等)。
- build_monitor が reviewed を返し、alert_level を抑制する (要対応トレイから外す)。
- 未訪問 (checkin 無し) の visit も review 可 → missing の alert 抑制。
- undo で alert が復活する。
- comment の保存と upsert 更新。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Patient, Staff, User, Visit
from app.models.visit_review import VisitReview
from app.services.checkin.monitor import (
    ALERT_MISSING,
    ALERT_NONE,
    PHASE_MISSING,
    build_monitor,
)

JST = ZoneInfo("Asia/Tokyo")
TARGET = date(2026, 6, 30)


def _utc(h: int, mi: int) -> datetime:
    return datetime(TARGET.year, TARGET.month, TARGET.day, h, mi, tzinfo=JST).astimezone(UTC)


async def _make_staff(db, name: str) -> Staff:
    staff = Staff(name=name)
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    return staff


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_patient(db, code: str, *, lat=None, lng=None) -> Patient:
    p = Patient(code=code, name=f"患者{code}", lat=lat, lng=lng)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(db, patient, staff, *, start=time(9, 0), end=time(10, 0)) -> Visit:
    v = Visit(
        patient_id=patient.id,
        primary_staff_id=staff.id,
        visit_date=TARGET,
        start_time=start,
        end_time=end,
        type="regular",
        status="planned",
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


def _find(resp, visit_id):
    for row in resp.staff:
        for v in row.visits:
            if v.visit_id == visit_id:
                return v
    return None


# ---------------------------------------------------------------------------
# API: RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_rbac_staff_forbidden(client, db) -> None:
    staff = await _make_staff(db, "S-rbac")
    staff_user = await _make_user(db, "rev-staff@example.com", "staff", staff_id=staff.id)
    p = await _make_patient(db, "RV-RBAC")
    v = await _make_visit(db, p, staff)

    res = await client.post(
        f"/api/v1/visits/{v.id}/review", json={"comment": "x"}, headers=_bearer(staff_user)
    )
    assert res.status_code == 403, res.text

    res_del = await client.delete(f"/api/v1/visits/{v.id}/review", headers=_bearer(staff_user))
    assert res_del.status_code == 403, res_del.text


@pytest.mark.asyncio
async def test_review_unknown_visit_404(client, db) -> None:
    admin = await _make_user(db, "rev-admin404@example.com", "admin")
    from uuid import uuid4

    res = await client.post(
        f"/api/v1/visits/{uuid4()}/review", json={}, headers=_bearer(admin)
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# API: upsert + monitor suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_suppresses_missing_alert(client, db) -> None:
    """未訪問 (checkin 無し) の visit も review でき、alert が抑制される."""
    admin = await _make_user(db, "rev-admin@example.com", "admin")
    staff = await _make_staff(db, "S-miss")
    p = await _make_patient(db, "RV-MISS")
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))

    # review 前: missing + alert missing。
    before = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(before, v.id)
    assert mv.phase == PHASE_MISSING
    assert mv.alert_level == ALERT_MISSING
    assert mv.reviewed is False

    # 確認済みにする。
    res = await client.post(
        f"/api/v1/visits/{v.id}/review",
        json={"comment": "電話で在宅確認済み"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reviewed_by"] == str(admin.id)
    assert body["comment"] == "電話で在宅確認済み"
    assert body["reviewed_at"]

    # DB に 1 行 (reviewed_by=admin)。
    row = await db.scalar(select(VisitReview).where(VisitReview.visit_id == v.id))
    assert row is not None
    assert row.reviewed_by == admin.id

    # review 後: phase は missing のままだが alert は none (要対応トレイから外す)。
    after = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv2 = _find(after, v.id)
    assert mv2.phase == PHASE_MISSING
    assert mv2.alert_level == ALERT_NONE
    assert mv2.reviewed is True
    assert mv2.review_comment == "電話で在宅確認済み"
    assert mv2.reviewed_by_name == "S-miss" or mv2.reviewed_by_name == admin.email


@pytest.mark.asyncio
async def test_review_upsert_updates_comment(client, db) -> None:
    admin = await _make_user(db, "rev-upsert@example.com", "admin")
    staff = await _make_staff(db, "S-up")
    p = await _make_patient(db, "RV-UP")
    v = await _make_visit(db, p, staff)

    r1 = await client.post(
        f"/api/v1/visits/{v.id}/review", json={"comment": "一回目"}, headers=_bearer(admin)
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.post(
        f"/api/v1/visits/{v.id}/review", json={"comment": "二回目"}, headers=_bearer(admin)
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["comment"] == "二回目"

    # 行は 1 件のまま (unique upsert)。
    rows = (await db.scalars(select(VisitReview).where(VisitReview.visit_id == v.id))).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_unreview_revives_alert(client, db) -> None:
    """undo (DELETE) で行が消え、alert が復活する."""
    admin = await _make_user(db, "rev-undo@example.com", "admin")
    staff = await _make_staff(db, "S-undo")
    p = await _make_patient(db, "RV-UNDO")
    v = await _make_visit(db, p, staff, start=time(9, 0), end=time(10, 0))

    await client.post(f"/api/v1/visits/{v.id}/review", json={}, headers=_bearer(admin))
    suppressed = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(suppressed, v.id).alert_level == ALERT_NONE

    # undo。
    res = await client.delete(f"/api/v1/visits/{v.id}/review", headers=_bearer(admin))
    assert res.status_code == 204, res.text
    assert (await db.scalar(select(VisitReview).where(VisitReview.visit_id == v.id))) is None

    revived = await build_monitor(db, TARGET, now=_utc(13, 30))
    mv = _find(revived, v.id)
    assert mv.alert_level == ALERT_MISSING
    assert mv.reviewed is False

    # 二度目の undo は冪等 (204)。
    res2 = await client.delete(f"/api/v1/visits/{v.id}/review", headers=_bearer(admin))
    assert res2.status_code == 204, res2.text


@pytest.mark.asyncio
async def test_two_staff_group_review_is_per_visit(client, db) -> None:
    """2 名体制 (同一 visit_group_id の 2 visit) は visit 単位で review される.

    片方だけ review → グループの未訪問はトレイに残る。両方 review → 消える。
    """
    from uuid import uuid4

    admin = await _make_user(db, "rev-grp@example.com", "admin")
    staff_a = await _make_staff(db, "S-grpA")
    staff_b = await _make_staff(db, "S-grpB")
    p = await _make_patient(db, "RV-GRP")
    group_id = uuid4()
    v_a = Visit(
        patient_id=p.id,
        primary_staff_id=staff_a.id,
        visit_date=TARGET,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        visit_group_id=group_id,
    )
    v_b = Visit(
        patient_id=p.id,
        primary_staff_id=staff_b.id,
        visit_date=TARGET,
        start_time=time(9, 0),
        end_time=time(10, 0),
        type="regular",
        status="planned",
        visit_group_id=group_id,
    )
    db.add_all([v_a, v_b])
    await db.commit()
    await db.refresh(v_a)
    await db.refresh(v_b)

    # 初期: 両 visit とも未訪問 alert。
    before = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(before, v_a.id).alert_level == ALERT_MISSING
    assert _find(before, v_b.id).alert_level == ALERT_MISSING

    # 片方 (A) のみ review → A は抑制、B はトレイ残存。
    res = await client.post(
        f"/api/v1/visits/{v_a.id}/review", json={}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    partial = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(partial, v_a.id).alert_level == ALERT_NONE
    assert _find(partial, v_a.id).reviewed is True
    assert _find(partial, v_b.id).alert_level == ALERT_MISSING
    assert _find(partial, v_b.id).reviewed is False

    # 両方 review → グループ全体が消える。
    res_b = await client.post(
        f"/api/v1/visits/{v_b.id}/review", json={}, headers=_bearer(admin)
    )
    assert res_b.status_code == 200, res_b.text
    both = await build_monitor(db, TARGET, now=_utc(13, 30))
    assert _find(both, v_a.id).alert_level == ALERT_NONE
    assert _find(both, v_b.id).alert_level == ALERT_NONE


@pytest.mark.asyncio
async def test_review_empty_comment_stored_as_null(client, db) -> None:
    admin = await _make_user(db, "rev-empty@example.com", "admin")
    staff = await _make_staff(db, "S-empty")
    p = await _make_patient(db, "RV-EMPTY")
    v = await _make_visit(db, p, staff)

    res = await client.post(
        f"/api/v1/visits/{v.id}/review", json={"comment": "   "}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["comment"] is None
