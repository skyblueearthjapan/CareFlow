"""カイポケ置換取り込み (/integrations/replace-inbound) のテスト — 2026-07-26.

docs/plans/kaipoke-event-inbound-design.md 追補 (置換モード):
  * ゲート (未来週 422)・実績(打刻)ガード・空CSV拒否
  * dry-run 無書込 / 実適用 = 週白紙化 + カイポケ全挿入 (単一トランザクション)
  * 名寄せ不可・新人担当1は skipped として可視化 (隠さない)
  * 月跨ぎ週は両月を export して日を月別に絞って結合
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.staff import Staff
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
    _seed_real_apply,
    _seed_week,
)

REPLACE_URL = "/api/v1/integrations/replace-inbound"

# 月跨ぎ検証用の週: 2026-07-27(月) 〜 2026-08-01(土)
CROSS_WEEK_START = date(2026, 7, 27)


class StubKaipokeClient:
    """export を月別レスポンスで差し替えるスタブ。"""

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
        email="replace-admin@example.com",
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


async def _post_replace(client, admin, *, week_start: date, dry_run: bool):
    return await client.post(
        REPLACE_URL,
        headers=_bearer(admin),
        json={"weekStart": week_start.isoformat(), "dryRun": dry_run},
    )


@pytest.mark.asyncio
async def test_replace_blocked_for_future_week(client, db, stub_kaipoke) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    res = await _post_replace(client, admin, week_start=FUTURE_MONDAY, dry_run=True)
    assert res.status_code == 422, res.text
    assert stub_kaipoke.calls == []


@pytest.mark.asyncio
async def test_replace_blocked_when_week_has_checkins(client, db, stub_kaipoke) -> None:
    """実績(打刻)がある週は置換不可 (実績の紐付け先を消さない)。"""
    seeded = await _seed_week(db)
    admin = await _make_admin(db)
    db.add(
        VisitCheckin(
            visit_id=seeded["tue"].id,
            patient_id=seeded["patient"].id,
            staff_id=seeded["staff"].id,
            kind="arrival",
            scanned_at=datetime(2026, 7, 7, 10, 1, tzinfo=UTC),
            match_status="match",
            threshold_snapshot={"v": 1},
        )
    )
    await db.commit()
    stub_kaipoke.by_month[MONTH] = _csv(_kp_row(date(2026, 7, 7), time(10, 0), time(10, 35)))

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 422, res.text
    assert "実績" in res.json()["detail"]
    assert "差分取り込み" in res.json()["detail"]


@pytest.mark.asyncio
async def test_replace_rejects_empty_kaipoke_csv(client, db, stub_kaipoke) -> None:
    """カイポケ現況が0件のときは白紙化を拒否 (週全滅の安全弁)。"""
    await _seed_week(db)
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = ""
    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res.status_code == 422, res.text
    assert "0件" in res.json()["detail"]
    # 白紙化されていない
    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits) == 3


@pytest.mark.asyncio
async def test_replace_dry_run_writes_nothing(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
        _kp_row(date(2026, 7, 10), time(9, 0), time(9, 30)),
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["dryRun"] is True
    assert body["wiped"] == 3
    assert body["inserted"] == 2
    assert body["jobId"] is None

    # 一切変わっていない
    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits) == 3
    assert all(v.source == "auto" for v in visits)


@pytest.mark.asyncio
async def test_replace_real_wipes_and_inserts_then_converges(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    # 火曜(1)はコースあり → そこへ張り付く。金曜(4)はコース無し → 臨時コース新設
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    admin = await _make_admin(db)
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
        _kp_row(date(2026, 7, 10), time(9, 0), time(9, 30)),
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["wiped"] == 3
    assert body["inserted"] == 2
    assert body["tempCourses"] == 1  # 金曜分
    assert body["jobId"] is not None

    visits = (
        await db.scalars(
            select(Visit).where(Visit.deleted_at.is_(None)).order_by(Visit.visit_date)
        )
    ).all()
    assert len(visits) == 2  # 旧3件は soft delete 済み
    assert [v.visit_date for v in visits] == [date(2026, 7, 7), date(2026, 7, 10)]
    assert all(v.source == "import" for v in visits)
    assert all(v.course_id is not None for v in visits)
    assert visits[0].start_time == time(14, 0)
    assert visits[0].note and "カイポケ置換取込" in visits[0].note

    # 2回目 (冪等): 前回挿入分が白紙化対象になり、同じ2件が再挿入される
    res2 = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    assert body2["wiped"] == 2
    assert body2["inserted"] == 2
    visits2 = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert len(visits2) == 2


@pytest.mark.asyncio
async def test_replace_skips_and_trainee_warning(client, db, stub_kaipoke) -> None:
    """名寄せ不可の患者は skipped。新人担当1は取り込み + ⚠traineeSolo 警告
    (方針転換 PO確定 2026-07-26: カイポケの現実を受け入れる)。"""
    seeded = await _seed_week(db)
    trainee = Staff(
        name="髙梨　桂子", role="staff", primary_office_id=seeded["office"].id
    )
    trainee.is_trainee = True
    db.add(trainee)
    await db.commit()
    admin = await _make_admin(db)

    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(10, 0), time(10, 35)),  # 正常
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35), patient_name="未知　患者"),
        _kp_row(date(2026, 7, 9), time(12, 0), time(12, 35), staff_name="髙梨　桂子"),
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 2  # 正常 + 新人担当1 (取り込む)
    reasons = [s["reason"] for s in body["skipped"]]
    assert any("患者を名寄せできません" in r for r in reasons)
    assert not any("新人" in r for r in reasons)  # 新人は skipped に出ない
    assert body["traineeSolo"] == [{"staffName": "髙梨　桂子", "count": 1}]

    # 実適用: 新人が primary の visit が実際に作られる
    res2 = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res2.status_code == 200, res2.text
    trainee_visits = (
        await db.scalars(
            select(Visit).where(
                Visit.deleted_at.is_(None), Visit.primary_staff_id == trainee.id
            )
        )
    ).all()
    assert len(trainee_visits) == 1
    assert trainee_visits[0].course_id is not None  # 臨時コースへ配置


@pytest.mark.asyncio
async def test_replace_cross_month_week_fetches_both_months(client, db, stub_kaipoke) -> None:
    """月跨ぎ週 (7/27〜8/1) は両月を export し、日を月別に絞って結合する。

    7月CSVに紛れた 7/1 の行 (day=1) が 8/1 と誤認されないことも検証。
    """
    seeded = await _seed_week(db)
    # 7/27 週はテスト実行時点で未来週になり得るため、実apply記録でゲートを開ける
    await _seed_real_apply(db, CROSS_WEEK_START)
    admin = await _make_admin(db)
    # 7月分: 7/28 の行 (対象) + 7/1 の行 (対象外・day=1 の誤混入検証)
    stub_kaipoke.by_month["2026-07"] = _csv(
        _kp_row(date(2026, 7, 28), time(10, 0), time(10, 35)),
        _kp_row(date(2026, 7, 1), time(9, 0), time(9, 35)),
    )
    # 8月分: 8/1 の行 (対象)
    stub_kaipoke.by_month["2026-08"] = _csv(
        _kp_row(date(2026, 8, 1), time(11, 0), time(11, 35)),
    )

    res = await _post_replace(client, admin, week_start=CROSS_WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["inserted"] == 2  # 7/28 + 8/1 (7/1 は除外)
    months = [c["month"] for c in stub_kaipoke.calls]
    assert months == ["2026-07", "2026-08"]
    # seeded の 7/6 週の訪問は白紙化対象外 (対象週のみ)
    assert body["wiped"] == 0
    assert seeded is not None


@pytest.mark.asyncio
async def test_replace_reassigns_course_to_kaipoke_staff(client, db, stub_kaipoke) -> None:
    """コース担当をカイポケの現実に付け替える (2026-07-26 改修・臨時コース乱立の根治)。

    らく助では水曜コースAの担当が佐藤だが、カイポケの現実では田中が回っている
    → コースAの担当を田中へ付け替えて訪問を張り付ける (臨時コースを作らない)。
    """
    from tests.test_kaipoke_inbound import _seed_second_staff

    seeded = await _seed_week(db)
    other = await _seed_second_staff(db, seeded["office"])  # 佐藤　次郎
    course_a = await _seed_course(db, office=seeded["office"], staff=other, weekday=2, code="A")
    admin = await _make_admin(db)
    # カイポケ現実: 水曜は田中が担当
    stub_kaipoke.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35)),  # 水曜・田中
    )

    res = await _post_replace(client, admin, week_start=WEEK_START, dry_run=True)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["coursesReassigned"] == 1
    assert body["tempCourses"] == 0

    # dry-run はコース担当を書き換えない
    await db.refresh(course_a)
    assert course_a.assigned_staff_id == other.id

    res2 = await _post_replace(client, admin, week_start=WEEK_START, dry_run=False)
    assert res2.status_code == 200, res2.text
    assert res2.json()["coursesReassigned"] == 1
    assert res2.json()["tempCourses"] == 0

    await db.refresh(course_a)
    assert course_a.assigned_staff_id == seeded["staff"].id  # 田中へ付け替え
    visits = (
        await db.scalars(
            select(Visit).where(Visit.deleted_at.is_(None), Visit.course_id == course_a.id)
        )
    ).all()
    assert len(visits) == 1
    assert visits[0].primary_staff_id == seeded["staff"].id
