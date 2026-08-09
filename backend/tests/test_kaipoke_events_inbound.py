"""カイポケ個別業務(イベント)取り込み (events-inbound-preview / apply) のテスト — E-1.

docs/plans/kaipoke-event-inbound-design.md:
  * apply実績ゲート (訪問取り込みと同一・実apply した週だけ取り込み可)
  * プレビュー差分 (add/update/delete)・名寄せ未解決の可視化・日曜スキップ・メモ系
  * dry-run は無書込・実適用は source='kaipoke' + external_id で冪等 upsert
  * 手動イベント (source='manual') には決して触れない
  * apply はエコーバック changes を検証 (external_id 形式・週レンジ・日曜拒否)
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.kaipoke_job import KaipokeJob
from app.models.office import Office
from app.models.staff import Staff, StaffEvent
from app.services import kaipoke_client as kc_module

# 対象週: 2026-07-20(月) 〜 2026-07-25(土)。7/26 = 日曜。
# 過去週のため時間ゲート (週開始<=今日) で無条件開放される (2026-07-26 改訂)。
WEEK_START = date(2026, 7, 20)
SUNDAY = date(2026, 7, 26)
# 未来週 (ゲートブロックの検証用)。2100-01-04 = 月曜。
FUTURE_MONDAY = date(2100, 1, 4)

STAFF_A = "宇田川　優莉"
STAFF_B = "川名　千恵"
UNKNOWN_STAFF = "菅野　頼子"  # 楽スケ未登録

PREVIEW_URL = "/api/v1/integrations/events-inbound-preview"
APPLY_URL = "/api/v1/integrations/events-inbound-apply"


# --- stub / helpers ----------------------------------------------------------


class StubKaipokeClient:
    """individual_tasks だけ差し替える最小スタブ。"""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    async def individual_tasks(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return {
            "success": True,
            "result": {
                "success": True,
                "week_start": WEEK_START.isoformat(),
                "week_end": SUNDAY.isoformat(),
                "tasks": list(self.tasks),
            },
        }


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


def _task(
    staff_name: str,
    staff_kid: str,
    d: date,
    start: str,
    end: str,
    title: str,
    task_id: str,
) -> dict[str, Any]:
    """RPA /api/individual-tasks の1行 (lib/individual_tasks_parser の出力形式)。"""
    return {
        "staff_kaipoke_id": staff_kid,
        "staff_name": staff_name,
        "date": d.isoformat(),
        "start": start,
        "end": end,
        "title": title,
        "kaipoke_task_id": task_id,
        "external_key": f"{task_id}:{staff_kid}:{d.isoformat()}",
    }


async def _make_admin(db) -> User:
    user = User(
        email="events-admin@example.com",
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


async def _seed_staff(db) -> dict[str, Staff]:
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    a = Staff(name=STAFF_A, role="staff", primary_office_id=office.id)
    b = Staff(name=STAFF_B, role="manager", primary_office_id=office.id)
    db.add_all([a, b])
    await db.commit()
    await db.refresh(a)
    await db.refresh(b)
    return {"a": a, "b": b}


async def _seed_real_apply(db, week_start: date = WEEK_START) -> None:
    db.add(
        KaipokeJob(
            job_type="push",
            week_start=week_start,
            params={
                "op": "apply",
                "sheet_id": "dummy",
                "dry_run": False,
                "week_start": week_start.isoformat(),
            },
            status="completed",
        )
    )
    await db.commit()


def _default_tasks() -> list[dict[str, Any]]:
    return [
        # 宇田川: 月曜の休み (終日イベント)
        _task(STAFF_A, "4601519", WEEK_START, "09:00", "18:00", "休み", "690499216"),
        # 川名: 火曜のケア会議
        _task(
            STAFF_B,
            "4465191",
            date(2026, 7, 21),
            "09:30",
            "10:30",
            "ケア会議：青栁あい様",
            "695430472",
        ),
        # 川名: 土曜のメモ系 (00:00〜00:00・PO確定で取り込む)
        _task(
            STAFF_B,
            "4465191",
            date(2026, 7, 25),
            "00:00",
            "00:00",
            "清水様：歯科薬お渡し",
            "674969993",
        ),
        # 楽スケ未登録職員 → unmatched
        _task(UNKNOWN_STAFF, "9999001", date(2026, 7, 22), "10:00", "11:00", "面談", "700000001"),
        # 日曜 → スキップ
        _task(STAFF_A, "4601519", SUNDAY, "09:00", "18:00", "休み", "690499216"),
    ]


async def _preview(client, admin) -> dict[str, Any]:
    res = await client.post(
        PREVIEW_URL,
        headers=_bearer(admin),
        json={"weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _apply(client, admin, changes, *, dry_run: bool) -> dict[str, Any]:
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "weekStart": WEEK_START.isoformat(),
            "dryRun": dry_run,
            "changes": changes,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _kaipoke_rows(db) -> list[StaffEvent]:
    rows = await db.scalars(
        select(StaffEvent).where(StaffEvent.source == "kaipoke").order_by(StaffEvent.starts_at)
    )
    return list(rows.all())


# --- 1. ゲート ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_future_week_open(client, db, stub_kaipoke) -> None:
    """未来週も実apply記録なしで開放 (2026-08-09 改訂: 時間ゲート撤廃)。"""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = []
    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": FUTURE_MONDAY.isoformat()}
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_preview_future_week_opens_with_real_apply(client, db, stub_kaipoke) -> None:
    """実apply記録があっても当然開放 (record は表示用として維持)。"""
    await _seed_staff(db)
    await _seed_real_apply(db, FUTURE_MONDAY)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = []
    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": FUTURE_MONDAY.isoformat()}
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_apply_future_week_open(client, db, stub_kaipoke) -> None:
    """未来週の apply も開放 (2026-08-09 改訂)。dryRun なので書込は無い。"""
    seeded = await _seed_staff(db)
    admin = await _make_admin(db)
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={
            "weekStart": FUTURE_MONDAY.isoformat(),
            "dryRun": True,
            "changes": [
                {
                    "action": "add",
                    "externalId": f"1:1:{FUTURE_MONDAY.isoformat()}",
                    "staffId": str(seeded["a"].id),
                    "date": FUTURE_MONDAY.isoformat(),
                    "start": "09:00",
                    "end": "10:00",
                    "title": "x",
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_preview_requires_monday(client, db, stub_kaipoke) -> None:
    await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    res = await client.post(
        PREVIEW_URL,
        headers=_bearer(admin),
        json={"weekStart": date(2026, 7, 21).isoformat()},
    )
    assert res.status_code == 422, res.text
    assert "月曜日" in res.json()["detail"]


# --- 2. プレビュー -----------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_detects_adds_unmatched_sunday_memo(client, db, stub_kaipoke) -> None:
    """過去週は実apply記録なしで開放される (時間ゲート・2026-07-26 改訂)。"""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()

    body = await _preview(client, admin)
    assert body["weekStart"] == WEEK_START.isoformat()
    assert body["weekEnd"] == date(2026, 7, 25).isoformat()
    assert body["fetchedTotal"] == 5
    assert body["adds"] == 3
    assert body["updates"] == 0
    assert body["deletes"] == 0
    assert body["sundaySkipped"] == 1
    assert body["memoCount"] == 1
    assert body["unmatched"] == [{"staffName": UNKNOWN_STAFF, "count": 1}]

    memo = [c for c in body["changes"] if c["isMemo"]]
    assert len(memo) == 1
    assert memo[0]["title"] == "清水様：歯科薬お渡し"

    # RPA へは週の月曜を渡す
    assert stub_kaipoke.calls[0]["date"] == WEEK_START.isoformat()


# --- 3. 適用 (dry-run / 実適用 / 収束) ---------------------------------------


@pytest.mark.asyncio
async def test_apply_dry_run_writes_nothing(client, db, stub_kaipoke) -> None:
    await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()

    body = await _preview(client, admin)
    result = await _apply(client, admin, body["changes"], dry_run=True)
    assert result["dryRun"] is True
    assert result["added"] == 3
    assert result["jobId"] is None
    assert await _kaipoke_rows(db) == []


@pytest.mark.asyncio
async def test_apply_real_then_converges(client, db, stub_kaipoke) -> None:
    seeded = await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()

    body = await _preview(client, admin)
    result = await _apply(client, admin, body["changes"], dry_run=False)
    assert result["added"] == 3
    assert result["failed"] == 0
    assert result["jobId"] is not None

    rows = await _kaipoke_rows(db)
    assert len(rows) == 3
    for row in rows:
        assert row.source == "kaipoke"
        assert row.external_id
        assert row.event_type == "event"
        assert row.note and "カイポケ個別業務取込" in row.note
    yasumi = next(r for r in rows if r.title == "休み")
    assert yasumi.staff_id == seeded["a"].id
    assert yasumi.starts_at.time() == time(9, 0)
    assert yasumi.ends_at.time() == time(18, 0)
    memo = next(r for r in rows if "歯科薬" in (r.title or ""))
    assert memo.starts_at == memo.ends_at  # メモ系はゼロ長でそのまま保存

    # 再プレビュー → 差分ゼロに収束 (冪等)
    body2 = await _preview(client, admin)
    assert body2["adds"] == 0
    assert body2["updates"] == 0
    assert body2["deletes"] == 0


@pytest.mark.asyncio
async def test_update_delete_flow_and_manual_untouched(client, db, stub_kaipoke) -> None:
    seeded = await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    # 手動イベント (source='manual') — 取り込みは決して触れない
    from datetime import datetime

    manual = StaffEvent(
        staff_id=seeded["b"].id,
        event_type="training",
        starts_at=datetime(2026, 7, 21, 13, 0),
        ends_at=datetime(2026, 7, 21, 14, 0),
        title="手動研修",
    )
    db.add(manual)
    await db.commit()

    # 初回取り込み
    stub_kaipoke.tasks = _default_tasks()
    body = await _preview(client, admin)
    assert body["deletes"] == 0  # manual 行は delete 候補にならない
    await _apply(client, admin, body["changes"], dry_run=False)

    # カイポケ側の変化: ケア会議の時刻変更 + 休みの削除
    tasks2 = [t for t in _default_tasks() if t["title"] != "休み"]
    for t in tasks2:
        if t["title"].startswith("ケア会議"):
            t["start"], t["end"] = "10:00", "11:00"
    stub_kaipoke.tasks = tasks2

    body2 = await _preview(client, admin)
    assert body2["adds"] == 0
    assert body2["updates"] == 1
    assert body2["deletes"] == 1
    upd = next(c for c in body2["changes"] if c["action"] == "update")
    assert upd["beforeStart"] == "09:30"
    assert upd["start"] == "10:00"

    result = await _apply(client, admin, body2["changes"], dry_run=False)
    assert result["updated"] == 1
    assert result["deleted"] == 1

    rows = await _kaipoke_rows(db)
    assert len(rows) == 2  # ケア会議(更新済) + メモ
    meeting = next(r for r in rows if (r.title or "").startswith("ケア会議"))
    assert meeting.starts_at.time() == time(10, 0)
    assert not any(r.title == "休み" for r in rows)

    # 手動イベントは無傷
    manual_row = await db.get(StaffEvent, manual.id)
    assert manual_row is not None
    assert manual_row.title == "手動研修"
    assert manual_row.source == "manual"


@pytest.mark.asyncio
async def test_apply_stale_add_is_idempotent(client, db, stub_kaipoke) -> None:
    """同じ changes を2回実適用しても二重挿入しない (upsert 意味論)。"""
    await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()

    body = await _preview(client, admin)
    await _apply(client, admin, body["changes"], dry_run=False)
    result2 = await _apply(client, admin, body["changes"], dry_run=False)

    assert result2["added"] == 0
    assert result2["skipped"] == 3  # 変更なし
    assert len(await _kaipoke_rows(db)) == 3


# --- 4. apply エコーバックの検証 ---------------------------------------------


@pytest.mark.asyncio
async def test_apply_rejects_invalid_items(client, db, stub_kaipoke) -> None:
    seeded = await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    def _change(external_id: str, d: date) -> dict[str, Any]:
        return {
            "action": "add",
            "externalId": external_id,
            "staffId": str(seeded["a"].id),
            "date": d.isoformat(),
            "start": "09:00",
            "end": "10:00",
            "title": "x",
        }

    result = await _apply(
        client,
        admin,
        [
            _change("bad-format", WEEK_START),  # 形式不正
            _change(f"1:1:{SUNDAY.isoformat()}", SUNDAY),  # 日曜
            _change(f"1:1:{WEEK_START.isoformat()}", date(2026, 7, 13)),  # ID と date 不一致
        ],
        dry_run=False,
    )
    assert result["failed"] == 3
    assert result["added"] == 0
    assert await _kaipoke_rows(db) == []
    details = [r["detail"] for r in result["results"]]
    assert any("形式" in d for d in details)
    assert any("日曜" in d for d in details)


@pytest.mark.asyncio
async def test_apply_empty_changes_rejected(client, db, stub_kaipoke) -> None:
    await _seed_staff(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    res = await client.post(
        APPLY_URL,
        headers=_bearer(admin),
        json={"weekStart": WEEK_START.isoformat(), "dryRun": True, "changes": []},
    )
    assert res.status_code == 422, res.text
