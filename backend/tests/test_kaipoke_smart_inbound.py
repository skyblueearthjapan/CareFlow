"""smart-inbound (日単位ハイブリッド自動判別) のテスト — 2026-07-26 PO確定.

handoff session-2026-07-26 §6-b:
  * 判別信号 = visit_checkins の日別有無 (打刻あり日=差分 / なし日=置換)
  * export 1回の結果を差分計算と置換計画の両方に渡す
  * 打刻あり日は訪問の行を保存したまま直す (checkin の紐付き visit id 不変)
  * dry_run は無書込・実適用は単一トランザクション
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.visit import Visit
from app.models.visit_checkin import VisitCheckin
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.csv_builder import KaipokeCsvRow, build_csv
from tests.test_kaipoke_inbound import (  # 共有フィクスチャを流用
    FUTURE_MONDAY,
    MONTH,
    WEEK_START,
    _kp_row,
    _seed_course,
    _seed_week,
)

PREVIEW_URL = "/api/v1/integrations/smart-inbound-preview"
APPLY_URL = "/api/v1/integrations/smart-inbound-apply"


class StubKaipokeClient:
    """export を月別レスポンスで差し替えるスタブ (replace テストと同型)。"""

    def __init__(self) -> None:
        self.by_month: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return {"result": {"csv_content": self.by_month.get(str(payload.get("month")), "")}}


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_admin(db) -> User:
    user = User(
        email="smart-admin@example.com",
        password_hash=hash_password("does-not-matter-here"),
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


def _csv(*rows: KaipokeCsvRow) -> str:
    return build_csv(list(rows), encoding="utf-8-sig").decode("utf-8-sig")


async def _add_checkin(db, seeded, visit_key: str) -> None:
    db.add(
        VisitCheckin(
            visit_id=seeded[visit_key].id,
            patient_id=seeded["patient"].id,
            staff_id=seeded["staff"].id,
            kind="arrival",
            scanned_at=datetime(2026, 7, 7, 10, 1, tzinfo=UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()


async def _preview(client, admin):
    return await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": WEEK_START.isoformat()}
    )


async def _apply(client, admin, *, sheet_id: str | None, dry_run: bool):
    return await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "weekStart": WEEK_START.isoformat(),
            "sheetId": sheet_id,
            "dryRun": dry_run,
        },
    )


@pytest.mark.asyncio
async def test_smart_future_week_open(client, db, stub_kaipoke) -> None:
    """未来週も smart 取り込み可 (2026-08-09 改訂: 時間ゲート撤廃)。

    カイポケが空の未来週は空CSV拒否 422 が働く (ゲート通過の証明 = ④反映の文言ではない)。
    カイポケに入力がある未来週はプレビューが返る。
    """
    await _seed_week(db)
    admin = await _make_admin(db)

    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": FUTURE_MONDAY.isoformat()}
    )
    assert res.status_code == 422, res.text
    assert "0件" in res.json()["detail"]
    assert "④反映" not in res.json()["detail"]

    stub_kaipoke.by_month["2100-01"] = _csv(_kp_row(date(2100, 1, 5), time(10, 0), time(10, 35)))
    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": FUTURE_MONDAY.isoformat()}
    )
    assert res.status_code in (200, 202), res.text


@pytest.mark.asyncio
async def test_smart_clean_week_is_pure_replace(client, db, stub_kaipoke) -> None:
    """打刻ゼロの週 = 全日が置換担当 (差分シートは作られない)。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
    )

    res = await _preview(client, admin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["protectedDays"] == []
    assert len(body["replaceDays"]) == 6
    assert body["sheetId"] is None
    assert body["replace"]["wiped"] == 3
    assert body["replace"]["inserted"] == 1
    assert len(stub_kaipoke.calls) == 1  # export は1回だけ

    res2 = await _apply(client, admin, sheet_id=None, dry_run=False)
    assert res2.status_code == 200, res2.text
    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits) == 1
    assert visits[0].start_time == time(14, 0)


@pytest.mark.asyncio
async def test_smart_mixed_week_diff_protects_checked_day(client, db, stub_kaipoke) -> None:
    """火曜に打刻あり → 火曜=差分 (行を保存して修正)・他の日=置換。

    - 火曜の訪問はカイポケの時刻変更が「同じ行のまま」反映される (checkin 紐付け不変)
    - 水/木の訪問はカイポケに無いため置換で白紙化・金曜の新規はカイポケから挿入
    """
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_checkin(db, seeded, "tue")  # 7/7(火) に打刻
    tue_visit_id = seeded["tue"].id
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),  # 火: 時刻変更
        _kp_row(date(2026, 7, 10), time(9, 0), time(9, 30)),  # 金: 新規
    )

    res = await _preview(client, admin)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["protectedDays"] == [date(2026, 7, 7).isoformat()]
    assert len(body["replaceDays"]) == 5
    assert body["sheetId"] is not None
    assert body["diffSummary"].get("edit", 0) >= 1
    # 置換パートは火曜を触らない (wiped = 水+木 の2件のみ)
    assert body["replace"]["wiped"] == 2
    assert body["replace"]["inserted"] == 1  # 金曜
    assert len(stub_kaipoke.calls) == 1  # export は1回だけ (差分は注入CSVを使う)

    res2 = await _apply(client, admin, sheet_id=body["sheetId"], dry_run=False)
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["diff"]["updated"] >= 1
    assert body2["replace"]["wiped"] == 2
    assert body2["replace"]["inserted"] == 1

    # 火曜: 同じ行のまま 14:00 へ更新 (checkin の紐付け先が生きている)
    tue = await db.get(Visit, tue_visit_id)
    assert tue is not None
    await db.refresh(tue)  # API 側セッションの更新を読み直す
    assert tue.deleted_at is None
    assert tue.start_time == time(14, 0)
    n_checkins = (
        await db.scalars(select(VisitCheckin).where(VisitCheckin.visit_id == tue_visit_id))
    ).all()
    assert len(n_checkins) == 1

    # 週全体: 火(保存) + 金(挿入) の2件・水/木は白紙化済み
    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert sorted(v.visit_date for v in visits) == [date(2026, 7, 7), date(2026, 7, 10)]


@pytest.mark.asyncio
async def test_smart_apply_dry_run_writes_nothing(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_checkin(db, seeded, "tue")
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
    )

    res = await _preview(client, admin)
    sheet_id = res.json()["sheetId"]
    res2 = await _apply(client, admin, sheet_id=sheet_id, dry_run=True)
    assert res2.status_code == 200, res2.text
    assert res2.json()["dryRun"] is True

    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits) == 3  # 何も変わっていない
    assert all(v.source == "auto" for v in visits)
    tue = await db.get(Visit, seeded["tue"].id)
    assert tue.start_time == time(10, 0)
