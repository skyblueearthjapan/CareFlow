"""Tests for improvement-suggestions endpoints (P2-B).

  - GET  /api/v1/schedule/v2/improvement-suggestions
  - POST /api/v1/schedule/v2/improvement-suggestions/dismiss

検証内容:
    - dismiss: 作成 / 同一指紋 upsert / promote で movability 更新 /
      promote 時に is_pinned=true 行が locked のまま / RBAC.
    - GET: 0 件時 200 + filtered_summary / 存在しない patient_id は 404.

ローカル SQLite のみ (本番 DB 禁止).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, time

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.office import Office
from app.models.patient import Patient
from app.models.patient_fixed_visit import PatientFixedVisit
from app.models.suggestion_dismissal import SuggestionDismissal
from app.models.user import User

ISO_YEAR = 2026
ISO_WEEK = 20

BASE = (35.6000, 140.1000)

_DISMISS_URL = "/api/v1/schedule/v2/improvement-suggestions/dismiss"
_GET_URL = "/api/v1/schedule/v2/improvement-suggestions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, *, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_patient(db, *, code: str = "TGT") -> Patient:
    office = Office(name="稲", code="INAGE")
    db.add(office)
    await db.flush()
    p = Patient(
        code=code,
        name=f"P-{code}",
        status="active",
        lat=BASE[0],
        lng=BASE[1],
        primary_office_id=office.id,
    )
    db.add(p)
    await db.flush()
    return p


async def _seed_pfv(
    db,
    *,
    patient: Patient,
    weekday: int = 0,
    movability: str = "unknown",
    is_pinned: bool = False,
) -> PatientFixedVisit:
    pfv = PatientFixedVisit(
        patient_id=patient.id,
        mode="normal",
        weekday=weekday,
        slot_index=0,
        start_time=time(10, 30),
        duration_min=30,
        movability=movability,
        is_pinned=is_pinned,
    )
    db.add(pfv)
    await db.flush()
    return pfv


def _dismiss_body(patient: Patient, **overrides) -> dict:
    body = {
        "patient_id": str(patient.id),
        "kind": "time_change",
        "target_weekday": 0,
        "reason": "other",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# dismiss: 作成 / upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_creates_row(client, db) -> None:
    admin = await _make_user(db, email="sd-admin1@example.com", role="admin")
    p = await _seed_patient(db)
    await db.commit()

    res = await client.post(_DISMISS_URL, headers=_bearer(admin), json=_dismiss_body(p))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dismissal_id"]
    assert body["movability_updated"] is False
    assert body["new_movability"] is None

    rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id == p.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].reason == "other"
    assert rows[0].dismissed_by == admin.id


@pytest.mark.asyncio
async def test_dismiss_same_fingerprint_upserts(client, db) -> None:
    admin = await _make_user(db, email="sd-admin2@example.com", role="admin")
    p = await _seed_patient(db)
    await db.commit()

    r1 = await client.post(
        _DISMISS_URL, headers=_bearer(admin), json=_dismiss_body(p, reason="other")
    )
    assert r1.status_code == 200, r1.text
    r2 = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="staff_relation", reason_note="担当変えたくない"),
    )
    assert r2.status_code == 200, r2.text
    # 同一指紋 → 同一行 (id 一致) で reason 更新.
    assert r1.json()["dismissal_id"] == r2.json()["dismissal_id"]

    rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id == p.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].reason == "staff_relation"
    assert rows[0].reason_note == "担当変えたくない"


# ---------------------------------------------------------------------------
# dismiss: promote_movability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_day_immovable_sets_time_flexible(client, db) -> None:
    admin = await _make_user(db, email="sd-admin3@example.com", role="admin")
    p = await _seed_patient(db)
    pfv = await _seed_pfv(db, patient=p, movability="unknown")
    await db.commit()

    res = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="day_immovable", promote_movability=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["movability_updated"] is True
    assert body["new_movability"] == "time_flexible"

    await db.refresh(pfv)
    assert pfv.movability == "time_flexible"


@pytest.mark.asyncio
async def test_promote_time_immovable_sets_locked(client, db) -> None:
    admin = await _make_user(db, email="sd-admin4@example.com", role="admin")
    p = await _seed_patient(db)
    pfv = await _seed_pfv(db, patient=p, movability="time_flexible")
    await db.commit()

    res = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="time_immovable", promote_movability=True),
    )
    assert res.status_code == 200, res.text
    assert res.json()["new_movability"] == "locked"

    await db.refresh(pfv)
    assert pfv.movability == "locked"


@pytest.mark.asyncio
async def test_promote_leaves_pinned_row_locked(client, db) -> None:
    """is_pinned=true の行は昇格処理でも locked のまま (movability_updated=False)."""
    admin = await _make_user(db, email="sd-admin5@example.com", role="admin")
    p = await _seed_patient(db)
    pfv = await _seed_pfv(db, patient=p, movability="locked", is_pinned=True)
    await db.commit()

    res = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="day_immovable", promote_movability=True),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["movability_updated"] is False
    assert body["new_movability"] is None

    await db.refresh(pfv)
    assert pfv.movability == "locked"


@pytest.mark.asyncio
async def test_promote_other_reason_no_update(client, db) -> None:
    """reason が昇格対象外 (other/staff_relation) なら promote=true でも更新しない."""
    admin = await _make_user(db, email="sd-admin6@example.com", role="admin")
    p = await _seed_patient(db)
    pfv = await _seed_pfv(db, patient=p, movability="unknown")
    await db.commit()

    res = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="staff_relation", promote_movability=True),
    )
    assert res.status_code == 200, res.text
    assert res.json()["movability_updated"] is False

    await db.refresh(pfv)
    assert pfv.movability == "unknown"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_staff_forbidden(client, db) -> None:
    staff_user = await _make_user(db, email="sd-staff@example.com", role="staff")
    p = await _seed_patient(db)
    await db.commit()
    res = await client.post(_DISMISS_URL, headers=_bearer(staff_user), json=_dismiss_body(p))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_get_requires_auth(client) -> None:
    res = await client.get(
        _GET_URL,
        params={"patient_id": str(uuid.uuid4()), "iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# GET: 0 件 + filtered_summary / 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_empty_returns_200_with_summary(client, db) -> None:
    admin = await _make_user(db, email="sd-get1@example.com", role="admin")
    p = await _seed_patient(db)  # PFV なし → 提案 0 件.
    await db.commit()

    res = await client.get(
        _GET_URL,
        headers=_bearer(admin),
        params={"patient_id": str(p.id), "iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["suggestions"] == []
    assert body["filtered_summary"] == {
        "pinned": 0,
        "locked": 0,
        "no_current_visit": 0,
        "dismissed": 0,
        "below_threshold": 0,
        "day_restricted": 0,
    }


@pytest.mark.asyncio
async def test_get_locked_pfv_counts_in_summary(client, db) -> None:
    admin = await _make_user(db, email="sd-get2@example.com", role="admin")
    p = await _seed_patient(db)
    await _seed_pfv(db, patient=p, movability="locked")
    await db.commit()

    res = await client.get(
        _GET_URL,
        headers=_bearer(admin),
        params={"patient_id": str(p.id), "iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["suggestions"] == []
    assert body["filtered_summary"]["locked"] == 1


@pytest.mark.asyncio
async def test_get_nonexistent_patient_404(client, db) -> None:
    admin = await _make_user(db, email="sd-get3@example.com", role="admin")
    await db.commit()
    res = await client.get(
        _GET_URL,
        headers=_bearer(admin),
        params={"patient_id": str(uuid.uuid4()), "iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# MEDIUM-2: no_current_visit カウンタ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_no_current_visit_counted(client, db) -> None:
    """当週 visit が未展開の PFV は no_current_visit にカウントされる."""
    admin = await _make_user(db, email="sd-get4@example.com", role="admin")
    p = await _seed_patient(db)
    # PFV を追加するが当週 Visit は一切作らない → _find_current_placement が None を返す.
    await _seed_pfv(db, patient=p, weekday=0)
    await db.commit()

    res = await client.get(
        _GET_URL,
        headers=_bearer(admin),
        params={"patient_id": str(p.id), "iso_year": ISO_YEAR, "iso_week": ISO_WEEK},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["suggestions"] == []
    assert body["filtered_summary"]["no_current_visit"] == 1
    # 他カウンタは 0.
    assert body["filtered_summary"]["pinned"] == 0
    assert body["filtered_summary"]["locked"] == 0


# ---------------------------------------------------------------------------
# MEDIUM-3: dismiss upsert で dismissed_at が更新される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_upsert_refreshes_dismissed_at(client, db) -> None:
    """同一指紋を2回 dismiss したとき dismissed_at が2回目の呼び出し以降の時刻になる."""
    admin = await _make_user(db, email="sd-ts@example.com", role="admin")
    p = await _seed_patient(db)
    p_id = p.id  # plain UUID: expire_all 後でも安全に使える
    await db.commit()

    # 1 回目 dismiss
    r1 = await client.post(_DISMISS_URL, headers=_bearer(admin), json=_dismiss_body(p))
    assert r1.status_code == 200, r1.text

    # 1 回目の dismissed_at を記録 (re-SELECT = 必ず DB から取得)
    rows = (
        await db.scalars(
            select(SuggestionDismissal).where(SuggestionDismissal.patient_id == p_id)
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    first_at = row.dismissed_at
    # SQLite は naive, PG は aware: 比較のため naive に正規化
    first_at_naive = first_at.replace(tzinfo=None) if first_at.tzinfo else first_at

    # 10 ms 待って時刻が進むことを保証
    await asyncio.sleep(0.01)
    before_second = datetime.utcnow()

    # 2 回目 dismiss (同一指紋)
    r2 = await client.post(
        _DISMISS_URL,
        headers=_bearer(admin),
        json=_dismiss_body(p, reason="staff_relation"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["dismissal_id"] == r1.json()["dismissal_id"]  # 同一行

    # 更新後の dismissed_at を取得 (await db.refresh で確実に DB から再読み込み)
    await db.refresh(row)
    second_at = row.dismissed_at
    second_at_naive = second_at.replace(tzinfo=None) if second_at.tzinfo else second_at

    # 2 回目呼び出し直前より後の時刻になっているはず (dismissed_at が更新された証拠).
    assert second_at_naive >= before_second
    assert second_at_naive > first_at_naive
