"""QR ディープリンク解決 API (GET /visits/resolve-qr/{token}) のテスト (v2).

汎用カメラで患者宅 QR を読むと `/q/{token}` へ遷移する (従来は 404)。FE の
ランディングページが本 API でトークンを「本日 (JST) のその患者の visit」へ解決し、
担当なら訪問詳細へ、担当外なら代行 / 予定外の選択画面へ進む。

v2 (凍結コントラクト 2026-08-16 / `docs/plans/qr-open-checkin-design.md` §4-1) で
**担当限定を撤廃**した: 候補はその患者の当日 visit 全件で、担当かどうかは
`is_mine` が表す。`patient_name` / `planned_staff_name` / `is_unplanned` も追加。

- 正常系: 候補 1 件 / 複数件 (start_time 昇順) / 空配列。
- 担当外の visit も候補に出る (is_mine=False + 予定スタッフ名つき)。
- 未知トークン = 404 / 失効 (ローテ済) = 410 (氏名を出さない汎用文)。
- staff 未紐付けユーザー = 403。
- 当日外 / 取消 / 削除済み visit は候補に入らない。
- 患者氏名はトップレベルで返す (住所等の患者属性は返さない)。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from app.core.security import create_access_token, hash_password
from app.models import Patient, RevokedQrToken, Staff, User, Visit, VisitStaffAssignment
from app.services.checkin.judge import JST


def _today_jst():
    return datetime.now(JST).date()


async def _make_staff_user(
    db, email: str, *, staff_name: str = "担当ヘルパー"
) -> tuple[Staff, User]:
    staff = Staff(name=staff_name)
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


async def _make_patient(db, code: str, *, qr_token=None, name: str = "利用者") -> Patient:
    p = Patient(code=code, name=name, address="千葉県テスト市1-2-3", qr_token=qr_token)
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
    is_unplanned=False,
) -> Visit:
    visit = Visit(
        patient_id=patient_id,
        primary_staff_id=staff_id,
        visit_date=visit_date or _today_jst(),
        start_time=start,
        end_time=end,
        type="regular",
        status=status,
        is_unplanned=is_unplanned,
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@pytest.mark.asyncio
async def test_resolve_qr_single_candidate(client, db) -> None:
    """自分の担当 1 件: v2 の全項目 (患者名 / 予定担当名 / is_mine / is_unplanned)."""
    staff, user = await _make_staff_user(db, "rq-1@example.com", staff_name="山田 花子")
    p = await _make_patient(db, "RQ-1", qr_token="rq-tok-1", name="田中 太郎")
    visit = await _make_visit(db, p.id, staff.id)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-1", headers=_bearer(user))
    assert res.status_code == 200, res.text
    body = res.json()
    # v2: 患者氏名はトップレベル (誤った利用者への記録を防ぐ確認表示に必須)。
    assert body["patient_name"] == "田中 太郎"
    assert len(body["candidates"]) == 1
    cand = body["candidates"][0]
    assert cand["visit_id"] == str(visit.id)
    assert cand["status"] == "planned"
    assert cand["start_time"] == "09:00:00"
    assert cand["end_time"] == "10:00:00"
    assert cand["planned_staff_name"] == "山田 花子"
    assert cand["is_mine"] is True
    assert cand["is_unplanned"] is False
    # 患者属性 (住所等) は候補にも本体にも載せない。
    assert "address" not in cand
    assert "address" not in body
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_candidate_without_staff_returns_null_planned_name(client, db) -> None:
    """予定担当が未割当の visit は planned_staff_name=None (is_mine も False)."""
    _, user = await _make_staff_user(db, "rq-1b@example.com")
    p = await _make_patient(db, "RQ-1B", qr_token="rq-tok-1b")
    await _make_visit(db, p.id, None)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-1b", headers=_bearer(user))
    assert res.status_code == 200, res.text
    cand = res.json()["candidates"][0]
    assert cand["planned_staff_name"] is None
    assert cand["is_mine"] is False
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_marks_existing_unplanned_visit(client, db) -> None:
    """既存の予定外 visit は is_unplanned=true で返る (二重生成の抑止・退出導線)."""
    staff, user = await _make_staff_user(db, "rq-1c@example.com")
    p = await _make_patient(db, "RQ-1C", qr_token="rq-tok-1c")
    await _make_visit(db, p.id, staff.id, status="in_progress", is_unplanned=True)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-1c", headers=_bearer(user))
    assert res.status_code == 200, res.text
    cand = res.json()["candidates"][0]
    assert cand["is_unplanned"] is True
    assert cand["is_mine"] is True
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
async def test_resolve_qr_other_staffs_visit_is_returned_as_not_mine(client, db) -> None:
    """v2 の仕様変更: 担当外の visit も候補に出る (is_mine=False + 予定担当名つき).

    第 1 弾は「200 + 空配列」で担当関係を秘匿していたが、代行打刻を開放したため
    「QR 所持 = 現地に居る」を認可の鍵として当日全件を開示する (設計 §4-1 決定#1)。
    """
    other_staff, _ = await _make_staff_user(db, "rq-4-other@example.com", staff_name="他人ヘルパー")
    _, user = await _make_staff_user(db, "rq-4-me@example.com", staff_name="自分ヘルパー")
    p = await _make_patient(db, "RQ-4", qr_token="rq-tok-4", name="佐藤 花")
    visit = await _make_visit(db, p.id, other_staff.id)

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-4", headers=_bearer(user))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["patient_name"] == "佐藤 花"
    assert len(body["candidates"]) == 1
    cand = body["candidates"][0]
    assert cand["visit_id"] == str(visit.id)
    assert cand["is_mine"] is False
    assert cand["planned_staff_name"] == "他人ヘルパー"
    await db.rollback()


@pytest.mark.asyncio
async def test_resolve_qr_is_mine_covers_staff_assignments(client, db) -> None:
    """is_mine は既存の可視性条件と同じ担当集合 (assignments 経由でも true)."""
    other_staff, _ = await _make_staff_user(db, "rq-4b-other@example.com")
    me, user = await _make_staff_user(db, "rq-4b-me@example.com")
    p = await _make_patient(db, "RQ-4B", qr_token="rq-tok-4b")
    visit = await _make_visit(db, p.id, other_staff.id)
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=me.id))
    await db.commit()

    res = await client.get("/api/v1/visits/resolve-qr/rq-tok-4b", headers=_bearer(user))
    assert res.status_code == 200, res.text
    assert res.json()["candidates"][0]["is_mine"] is True
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
