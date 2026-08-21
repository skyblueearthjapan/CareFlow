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
from uuid import UUID

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
    """individual_tasks / individual_tasks_result だけ差し替える最小スタブ。"""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.busy = False
        # async 起動時に渡された相関ID (result の既定応答にエコーバックする)
        self.async_job_id: str | None = None
        # individual_tasks_result の応答キュー (先頭から消費・尽きたら completed 既定)
        self.result_responses: list[dict[str, Any]] = []
        self.result_calls = 0

    async def aclose(self) -> None:  # pragma: no cover
        pass

    def _sync_result(self) -> dict[str, Any]:
        return {
            "success": True,
            "week_start": WEEK_START.isoformat(),
            "week_end": SUNDAY.isoformat(),
            "tasks": list(self.tasks),
        }

    async def individual_tasks(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        if self.busy:
            from app.services.kaipoke_client import KaipokeBusyError

            raise KaipokeBusyError({"error": "busy"})
        if payload.get("async"):
            self.async_job_id = str(payload.get("job_id") or "") or None
            return {"success": True, "async": True, "job_id": self.async_job_id}
        return {"success": True, "result": self._sync_result()}

    async def individual_tasks_result(self) -> dict[str, Any]:
        self.result_calls += 1
        if self.result_responses:
            return self.result_responses.pop(0)
        return {
            "success": True,
            "status": "completed",
            "job_id": self.async_job_id,
            "result": self._sync_result(),
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


# --- 5. 非同期プレビュー (kaipoke-events-async-preview-design.md) -------------

START_URL = "/api/v1/integrations/events-inbound-preview/start"


def _status_url(job_id: str) -> str:
    return f"/api/v1/integrations/events-inbound-preview/status/{job_id}"


async def _start(client, admin) -> str:
    res = await client.post(
        START_URL, headers=_bearer(admin), json={"weekStart": WEEK_START.isoformat()}
    )
    assert res.status_code == 202, res.text
    return res.json()["jobId"]


@pytest.mark.asyncio
async def test_async_preview_running_then_completed(client, db, stub_kaipoke) -> None:
    """start(202) → running → completed。プレビューは同期版と同一内容・冪等再取得可。"""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()
    job_id = await _start(client, admin)

    # RPA へ async:true + 相関ID (= KaipokeJob.id) が渡っている
    assert stub_kaipoke.calls[-1]["async"] is True
    assert stub_kaipoke.calls[-1]["job_id"] == job_id
    assert stub_kaipoke.calls[-1]["date"] == WEEK_START.isoformat()

    # 1回目: RPA まだ実行中
    stub_kaipoke.result_responses = [
        {"success": True, "status": "running", "job_id": job_id},
    ]
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "running", "error": None, "preview": None}

    # 2回目: 完了 (キューが尽きて completed 既定応答)
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    preview = body["preview"]
    assert preview["weekStart"] == WEEK_START.isoformat()
    assert preview["fetchedTotal"] == 5
    assert preview["sundaySkipped"] == 1
    assert len(preview["changes"]) == 3
    assert preview["adds"] == 3
    assert preview["unmatched"] == [{"staffName": UNKNOWN_STAFF, "count": 1}]

    job = await db.get(KaipokeJob, UUID(job_id))
    assert job is not None and job.status == "completed"
    assert job.result_summary["preview"]["fetchedTotal"] == 5

    # 3回目: 完了後は DB から返す (RPA は照会しない = 別ジョブ開始後も安全)
    calls_before = stub_kaipoke.result_calls
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["preview"] == preview
    assert stub_kaipoke.result_calls == calls_before


@pytest.mark.asyncio
async def test_async_preview_start_busy_409(client, db, stub_kaipoke) -> None:
    """RPA 単一スロット使用中は 409 (ジョブ記録も残さない)。"""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.busy = True
    res = await client.post(
        START_URL, headers=_bearer(admin), json={"weekStart": WEEK_START.isoformat()}
    )
    assert res.status_code == 409, res.text
    jobs = await db.scalars(select(KaipokeJob))
    assert [j for j in jobs.all() if j.params.get("op") == "events-preview"] == []


@pytest.mark.asyncio
async def test_async_preview_start_requires_monday(client, db, stub_kaipoke) -> None:
    await _seed_staff(db)
    admin = await _make_admin(db)
    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": "2026-07-21"})
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_async_preview_status_rpa_error(client, db, stub_kaipoke) -> None:
    """RPA がエラー終了 → failed + ジョブ failed 化。"""
    await _seed_staff(db)
    admin = await _make_admin(db)
    job_id = await _start(client, admin)
    stub_kaipoke.result_responses = [
        {"success": False, "status": "error", "job_id": job_id, "error": "networkidle timeout"},
    ]
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "failed"
    assert "networkidle timeout" in body["error"]
    job = await db.get(KaipokeJob, UUID(job_id))
    assert job is not None and job.status == "failed"

    # failed 後の再ポーリングも failed のまま (RPA 再照会なし)
    calls_before = stub_kaipoke.result_calls
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.json()["status"] == "failed"
    assert stub_kaipoke.result_calls == calls_before


@pytest.mark.asyncio
async def test_async_preview_status_lost_result(client, db, stub_kaipoke) -> None:
    """RPA 再起動 (no_result) や別ジョブの結果 → 取り違えず failed。"""
    await _seed_staff(db)
    admin = await _make_admin(db)

    # no_result (RPA 再起動でストア喪失)
    job_id = await _start(client, admin)
    stub_kaipoke.result_responses = [
        {"success": False, "status": "no_result", "job_id": None},
    ]
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.json()["status"] == "failed"
    assert "失われました" in res.json()["error"]

    # 別ジョブがスロットを実行中 (job_id 不一致の running)
    job_id2 = await _start(client, admin)
    stub_kaipoke.result_responses = [
        {"success": True, "status": "running", "job_id": "someone-else"},
    ]
    res = await client.get(_status_url(job_id2), headers=_bearer(admin))
    assert res.json()["status"] == "failed"
    assert "別のジョブ" in res.json()["error"] or "失われました" in res.json()["error"]

    # 別ジョブの完了結果 (job_id 不一致の completed) も取り込まない
    job_id3 = await _start(client, admin)
    stub_kaipoke.result_responses = [
        {
            "success": True,
            "status": "completed",
            "job_id": "someone-else",
            "result": stub_kaipoke._sync_result(),
        },
    ]
    res = await client.get(_status_url(job_id3), headers=_bearer(admin))
    assert res.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_async_preview_status_unknown_job_404(client, db, stub_kaipoke) -> None:
    await _seed_staff(db)
    admin = await _make_admin(db)
    res = await client.get(
        _status_url("00000000-0000-0000-0000-000000000000"), headers=_bearer(admin)
    )
    assert res.status_code == 404, res.text


# --- 6. 訪問との時間重なり警告 (案A・2026-08-21 ユーザー確定) -----------------


async def _seed_visit(db, staff_id, *, d, start_h, end_h, status_val="planned"):
    from datetime import time as _time

    from app.models import Patient
    from app.models.visit import Visit

    p = Patient(code=f"P-EC-{start_h}{status_val[:2]}", name="朝倉　美夢", status="active")
    db.add(p)
    await db.flush()
    v = Visit(
        patient_id=p.id,
        primary_staff_id=staff_id,
        visit_date=d,
        start_time=_time(start_h, 0),
        end_time=_time(end_h, 0),
        type="regular",
        status=status_val,
        source="manual",
    )
    db.add(v)
    await db.commit()
    return v


@pytest.mark.asyncio
async def test_preview_reports_visit_conflicts(client, db, stub_kaipoke) -> None:
    """取込イベントが担当訪問と重なる場合、プレビューに conflicts が載る (取込は妨げない)。"""
    seeded = await _seed_staff(db)
    admin = await _make_admin(db)

    # 宇田川の月曜: 9:00-18:00 休みイベント × 10:00-11:00 の訪問 = 重なり
    await _seed_visit(db, seeded["a"].id, d=WEEK_START, start_h=10, end_h=11)
    # キャンセル済み訪問は対象外
    await _seed_visit(
        db, seeded["a"].id, d=WEEK_START, start_h=14, end_h=15, status_val="cancelled"
    )
    stub_kaipoke.tasks = [
        _task(STAFF_A, "4601519", WEEK_START, "09:00", "18:00", "休み", "690499216"),
        # メモ系 (start==end) は衝突判定の対象外
        _task(STAFF_A, "4601519", WEEK_START, "00:00", "00:00", "申し送りメモ", "690499300"),
    ]

    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": WEEK_START.isoformat()}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["conflicts"]) == 1
    c = body["conflicts"][0]
    assert c["staffName"] == STAFF_A
    assert c["date"] == WEEK_START.isoformat()
    assert c["eventTitle"] == "休み"
    assert c["patientName"] == "朝倉　美夢"
    assert c["visitStart"] == "10:00"
    assert c["visitEnd"] == "11:00"


@pytest.mark.asyncio
async def test_apply_reports_visit_conflicts(client, db, stub_kaipoke) -> None:
    """実適用の結果にも conflicts が載る (取り込み自体は成功する)。"""
    seeded = await _seed_staff(db)
    admin = await _make_admin(db)
    await _seed_visit(db, seeded["a"].id, d=WEEK_START, start_h=10, end_h=11)

    result = await _apply(
        client,
        admin,
        [
            {
                "action": "add",
                "externalId": f"690499216:4601519:{WEEK_START.isoformat()}",
                "staffId": str(seeded["a"].id),
                "date": WEEK_START.isoformat(),
                "start": "09:00",
                "end": "18:00",
                "title": "休み",
            }
        ],
        dry_run=False,
    )
    assert result["added"] == 1  # 取り込みは行われる
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["patientName"] == "朝倉　美夢"


# --- 7. 汎用 reconcile との競合 (週空間C1・2026-08-21 の 409 障害) -------------


@pytest.mark.asyncio
async def test_reconcile_skips_fresh_events_preview_job(client, db, stub_kaipoke) -> None:
    """_reconcile_latest_job は新しい events-preview ジョブを先取りクローズしない.

    突合パネルの live 2秒ポーリングが idle を先に観測すると、汎用 reconcile が
    「result_unknown の completed」でジョブを閉じてしまい、status 側がプランを
    構築できず 409 になっていた (実障害 2026-08-21 job 1bf9a5bc)。
    """
    from app.api.v1.integrations import _reconcile_latest_job

    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()
    job_id = await _start(client, admin)

    job = await _reconcile_latest_job(db, kaipoke_idle=True, result_payload=None)
    await db.commit()
    assert job is not None and str(job.id) == job_id
    assert job.status in ("pending", "running")  # 閉じられていない
    assert "result_unknown" not in (job.result_summary or {})


@pytest.mark.asyncio
async def test_reconcile_still_closes_stale_events_preview_job(client, db, stub_kaipoke) -> None:
    """放置残骸 (30分超) は従来どおり閉じる (残骸が running のまま残らない)."""
    import datetime as _dt

    from app.api.v1.integrations import _reconcile_latest_job

    stale = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        params={"op": "events-preview", "week_start": WEEK_START.isoformat()},
        status="running",
    )
    db.add(stale)
    await db.flush()
    stale.created_at = _dt.datetime.now(_dt.UTC) - _dt.timedelta(minutes=31)
    await db.commit()

    job = await _reconcile_latest_job(db, kaipoke_idle=True, result_payload=None)
    await db.commit()
    assert job is not None and job.id == stale.id
    assert job.status == "completed"
    assert (job.result_summary or {}).get("result_unknown") is True


@pytest.mark.asyncio
async def test_async_status_recovers_from_premature_close(client, db, stub_kaipoke) -> None:
    """preview 無し completed (先取りクローズ痕) でも RPA result から自己回復する."""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()
    job_id = await _start(client, admin)

    # 汎用 reconcile に先取りクローズされた状態を再現
    job = await db.get(KaipokeJob, UUID(job_id))
    assert job is not None
    job.status = "completed"
    job.result_summary = {"result_unknown": True}
    await db.commit()

    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["preview"]["fetchedTotal"] == 5

    await db.refresh(job)
    assert job.result_summary["preview"]["fetchedTotal"] == 5


@pytest.mark.asyncio
async def test_async_status_premature_close_result_lost(client, db, stub_kaipoke) -> None:
    """先取りクローズ + RPA result も他ジョブ → 409 でなく failed で丁寧に返す."""
    await _seed_staff(db)
    admin = await _make_admin(db)
    stub_kaipoke.tasks = _default_tasks()
    job_id = await _start(client, admin)

    job = await db.get(KaipokeJob, UUID(job_id))
    assert job is not None
    job.status = "completed"
    job.result_summary = {"result_unknown": True}
    await db.commit()

    stub_kaipoke.result_responses = [
        {"success": True, "status": "completed", "job_id": "someone-else"},
    ]
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "failed"
    assert "再実行" in (body["error"] or "")
