"""QR ディープリンク解決 API (GET /visits/resolve-qr/{token}) のテスト.

汎用カメラで患者宅 QR を読むと `/q/{token}` へ遷移する (従来は 404)。FE の
ランディングページが本 API でトークンを「本日 (JST) の自分の担当 visit」へ
解決する。

- 正常系: 候補 1 件 / 複数件 (start_time 昇順) / 空配列。
- 担当外患者のトークン = 200 + 空配列 (404 と区別させない = 担当関係の秘匿)。
- 未知トークン = 404 / 失効 (ローテ済) = 410 (氏名を出さない汎用文)。
- staff 未紐付けユーザー = 403。
- 当日外 / 取消 / 削除済み visit は候補に入らない。
- レスポンスに患者名・住所を載せない。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, RevokedQrToken, Staff, User, Visit
from app.services.checkin.judge import JST


def _today_jst():
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


async def _make_patient(db, code: str, *, qr_token=None) -> Patient:
    p = Patient(code=code, name="利用者", address="千葉県テスト市1-2-3", qr_token=qr_token)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _make_visit(
    db,
    patient_id,
    staff_id,
    *,
    visit_date=None,
    start=time(9, 0),
    end=time(10, 0),
    status="planned",
) -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=visit_date or _today_jst(),
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@pytest.mark.asyncio
async def test_resolve_qr_single_candidate(client, db) -> None:
    staff, user = await _make_staff_user(db, "rq-1@example.com")
    p = await _make_patient(db, "RQ-1", qr_token="rq-tok-1")
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-1", headers=_bearer(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["candidates"]) == 1
    cand = body["candidates"][0]
    assert cand["visit_id"] == str(visit.id)
    assert cand["status"] == "planned"
    # 患者名・住所は載せない (ディープリンクに必要な最小情報のみ)。
    assert "patient_name" not in cand
    assert "address" not in cand
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_multiple_candidates_sorted_by_start_time(client, db) -> None:
    """同日 2 枠 (朝/夕) は start_time 昇順で返る."""
    staff, user = await _make_staff_user(db, "rq-2@example.com")
    p = await _make_patient(db, "RQ-2", qr_token="rq-tok-2")
    late = await _make_visit(db, p.id, staff.id, start=time(16, 0), end=time(17, 0))
    early = await _make_visit(db, p.id, staff.id, start=time(9, 0), end=time(10, 0))

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-2", headers=_bearer(user))
    assert res.status_code == 200, res.text
    ids = [c["visit_id"] for c in res.json()["candidates"]]
    assert ids == [str(early.id), str(late.id)]
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_no_visit_today_returns_empty(client, db) -> None:
    """有効トークンだが本日の visit が無い → 200 + 空配列."""
    _, user = await _make_staff_user(db, "rq-3@example.com")
    await _make_patient(db, "RQ-3", qr_token="rq-tok-3")

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-3", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["candidates"] == []
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_other_staffs_visit_returns_empty_200(client, db) -> None:
    """担当外患者のトークンも 200 + 空配列 (404 と区別させず担当関係を秘匿)."""
    other_staff, _ = await _make_staff_user(db, "rq-4-other@example.com")
    _, user = await _make_staff_user(db, "rq-4-me@example.com")
    p = await _make_patient(db, "RQ-4", qr_token="rq-tok-4")
    await _make_visit(db, p.id, other_staff.id)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-4", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["candidates"] == []
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_unknown_token_returns_404(client, db) -> None:
    _, user = await _make_staff_user(db, "rq-5@example.com")

    res = await client.get("/api/v1/visits/resolve-qr/no-such-token", headers=_bearer(user))
    assert res.status_code == 404, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_revoked_token_returns_410_generic_message(client, db) -> None:
    """失効トークンは 410。visit 文脈が無いので氏名を出さない汎用文."""
    _, user = await _make_staff_user(db, "rq-6@example.com")
    p = await _make_patient(db, "RQ-6", qr_token="rq-tok-6-new")
    # リクエスト前に失効履歴を seed する (リクエスト間 INSERT はフレーク源のため
    # 避ける — 2026-08-11 引き継ぎ教訓 6)。
    db.add(RevokedQrToken(patient_id=p.id, token="rq-tok-6-old"))
    await db.commit()

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-6-old", headers=_bearer(user))
    assert res.status_code == 410, res.text
    detail = res.json()["detail"]
    assert "QRは更新" in detail
    # 患者名は含まない。
    assert "利用者" not in detail
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_user_without_staff_link_returns_403(client, db) -> None:
    user = User(
        email="rq-7@example.com",
        password_hash=hash_password("does-not-matter"),
        role="staff",
        staff_id=None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _make_patient(db, "RQ-7", qr_token="rq-tok-7")

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-7", headers=_bearer(user))
    assert res.status_code == 403, res.text
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_excludes_other_day_cancelled_and_deleted(client, db) -> None:
    """当日外 / 取消 / 削除済みの visit は候補に入らない."""
    staff, user = await _make_staff_user(db, "rq-8@example.com")
    p = await _make_patient(db, "RQ-8", qr_token="rq-tok-8")
    await _make_visit(db, p.id, staff.id, visit_date=_today_jst() + timedelta(days=1))
    await _make_visit(db, p.id, staff.id, status="cancelled")
    deleted = await _make_visit(db, p.id, staff.id, start=time(11, 0), end=time(12, 0))
    deleted.deleted_at = datetime.now(JST)
    keep = await _make_visit(db, p.id, staff.id, start=time(14, 0), end=time(15, 0))
    await db.commit()

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-8", headers=_bearer(user))
    assert res.status_code == 200, res.text
    ids = [c["visit_id"] for c in res.json()["candidates"]]
    assert ids == [str(keep.id)]
    await db.rollback()
