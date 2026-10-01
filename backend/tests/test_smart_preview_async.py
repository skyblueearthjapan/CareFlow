"""統合プレビュー (smart-preview) のバックグラウンド実行のテスト — 2026-10-01.

docs/plans/smart-preview-async-2026-10-01.md:
  * 月を跨ぐ週は export 2 本 (~100s) で Cloudflare の ~100s 制限 (524) に当たる
  * POST /smart-inbound-preview/start は 202 + jobId をすぐ返し、実体はバックグラウンド
  * GET /smart-inbound-preview/status/{job_id} で状況と結果 (同期版と同じ形) を返す
  * plan-actual-compare と同じ守り: RPA busy / 二重起動 / 残骸の掃除
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.kaipoke_job import KaipokeJob
from app.models.visit import Visit
from app.services import kaipoke_client as kc_module
from tests.test_kaipoke_inbound import MONTH, WEEK_START, _kp_row, _seed_course, _seed_week
from tests.test_kaipoke_smart_inbound import (
    _add_checkin,
    _apply,
    _bearer,
    _csv,
    _make_admin,
    _preview,
)

START_URL = "/api/v1/integrations/smart-inbound-preview/start"
STATUS_URL = "/api/v1/integrations/smart-inbound-preview/status/{job_id}"
ACTIVE_URL = "/api/v1/integrations/smart-inbound-preview/active"

#: 月を跨ぐ週 (10/26〜11/1)。export は 10 月と 11 月の 2 本になる。
CROSS_MONDAY = date(2026, 10, 26)


class StubAsyncKaipoke:
    """export を月別に返し、status / 失敗 / 遅延を差し込めるスタブ。"""

    def __init__(self) -> None:
        self.by_month: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.rpa_running = False
        self.error: Exception | None = None
        self.delay = 0.0

    async def aclose(self) -> None:  # pragma: no cover
        pass

    async def status(self) -> dict[str, Any]:
        return {"current_task": {"running": self.rpa_running}}

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return {"result": {"csv_content": self.by_month.get(str(payload.get("month")), "")}}


@pytest.fixture
def stub():
    s = StubAsyncKaipoke()
    kc_module.set_test_client(s)  # type: ignore[arg-type]
    try:
        yield s
    finally:
        kc_module.set_test_client(None)


async def _start(client, admin, week_start: date = WEEK_START):
    return await client.post(
        START_URL, headers=_bearer(admin), json={"weekStart": week_start.isoformat()}
    )


async def _status(client, admin, job_id: str):
    return await client.get(STATUS_URL.format(job_id=job_id), headers=_bearer(admin))


async def _seed_job(db, admin, *, week_start: date = WEEK_START, minutes_ago: int = 0):
    job = KaipokeJob(
        job_type="fetch",
        week_start=week_start,
        params={"op": "smart-preview", "week_start": week_start.isoformat(), "async": True},
        status="running",
        started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        created_by_user_id=admin.id,
    )
    db.add(job)
    await db.commit()
    return job


# --- 202 → 完了 → apply ------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_202_and_completes_with_the_sync_payload(client, db, stub) -> None:
    """202 + jobId が返り、完了後の preview は同期版と同じ形 (sheetId で apply できる)。"""
    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    await _add_checkin(db, seeded, "tue")
    admin = await _make_admin(db)
    stub.by_month[MONTH] = _csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),  # 火: 時刻変更 (打刻日=差分)
        _kp_row(date(2026, 7, 10), time(9, 0), time(9, 30)),  # 金: 新規 (置換)
    )

    res = await _start(client, admin)
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "running"
    job_id = body["jobId"]

    # ASGITransport はバックグラウンドの終了まで待つので、ここでは決着済み。
    st = await _status(client, admin, job_id)
    assert st.status_code == 200, st.text
    st_body = st.json()
    assert st_body["status"] == "completed"
    assert st_body["jobId"] == job_id
    assert st_body["weekStart"] == WEEK_START.isoformat()
    preview = st_body["preview"]
    assert preview["protectedDays"] == [date(2026, 7, 7).isoformat()]
    assert len(preview["replaceDays"]) == 5
    assert preview["sheetId"] is not None
    assert preview["diffSummary"].get("edit", 0) >= 1
    assert preview["replace"]["wiped"] == 2
    assert preview["replace"]["inserted"] == 1

    # ジョブ履歴: 同期版と同じ要約 + preview 全体。
    job = await db.get(KaipokeJob, UUID(job_id))
    await db.refresh(job)
    assert job.status == "completed"
    assert job.params["op"] == "smart-preview"
    assert job.params["async"] is True
    assert job.result_summary["replace_wiped"] == 2
    assert job.result_summary["preview"]["sheetId"] == preview["sheetId"]

    # 同期版の応答と、sheetId 以外は一致する (画面は今までどおりに表示できる)。
    sync = await _preview(client, admin)
    assert sync.status_code == 200, sync.text
    sync_body = sync.json()
    assert {k: v for k, v in sync_body.items() if k != "sheetId"} == {
        k: v for k, v in preview.items() if k != "sheetId"
    }

    # apply は preview の sheetId をそのまま使える (契約は変えない)。
    res2 = await _apply(client, admin, sheet_id=preview["sheetId"], dry_run=False)
    assert res2.status_code == 200, res2.text
    assert res2.json()["diff"]["updated"] >= 1
    visits = (await db.scalars(select(Visit).where(Visit.deleted_at.is_(None)))).all()
    assert sorted(v.visit_date for v in visits) == [date(2026, 7, 7), date(2026, 7, 10)]


@pytest.mark.asyncio
async def test_start_rejects_non_monday_synchronously(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    res = await _start(client, admin, WEEK_START + timedelta(days=1))
    assert res.status_code == 422, res.text
    assert "月曜日" in res.json()["detail"]
    assert stub.calls == []
    assert (await db.scalars(select(KaipokeJob))).all() == []


# --- 失敗 -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_failure_settles_the_job_as_failed(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    stub.error = kc_module.KaipokeApiError(500, {"error": "boom"}, message="export exploded")

    res = await _start(client, admin)
    assert res.status_code == 202, res.text
    st = (await _status(client, admin, res.json()["jobId"])).json()
    assert st["status"] == "failed"
    assert "読み込みに失敗" in st["error"]
    assert "export exploded" in st["error"]
    assert st["preview"] is None


@pytest.mark.asyncio
async def test_empty_kaipoke_csv_fails_with_the_same_message(client, db, stub) -> None:
    """0 件は同期版と同じ文言で failed (0 件での取り込みは安全のため拒否)。"""
    await _seed_week(db)
    admin = await _make_admin(db)

    res = await _start(client, admin)
    assert res.status_code == 202, res.text
    st = (await _status(client, admin, res.json()["jobId"])).json()
    assert st["status"] == "failed"
    assert "0件" in st["error"]


@pytest.mark.asyncio
async def test_busy_midway_fails_with_japanese_message(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    stub.error = kc_module.KaipokeBusyError({"error": "busy"})

    res = await _start(client, admin)
    assert res.status_code == 202, res.text
    st = (await _status(client, admin, res.json()["jobId"])).json()
    assert st["status"] == "failed"
    assert "別の処理を実行中" in st["error"]
    job = await db.get(KaipokeJob, UUID(res.json()["jobId"]))
    await db.refresh(job)
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_unexpected_error_still_settles_the_job(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    stub.error = RuntimeError("boom")

    res = await _start(client, admin)
    st = (await _status(client, admin, res.json()["jobId"])).json()
    assert st["status"] == "failed"
    assert "boom" in st["error"]


# --- 守り (busy / 二重起動 / 残骸) ---------------------------------------------


@pytest.mark.asyncio
async def test_start_rejects_when_rpa_is_busy(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    stub.rpa_running = True

    res = await _start(client, admin)
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == "kaipoke busy"
    assert stub.calls == []
    assert (await db.scalars(select(KaipokeJob))).all() == []


@pytest.mark.asyncio
async def test_start_rejects_a_second_run_while_one_is_running(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    await _seed_job(db, admin, week_start=WEEK_START + timedelta(days=7))

    res = await _start(client, admin)
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    assert "既に実行中" in detail
    assert "7/13 の週" in detail
    assert "取消" in detail
    assert stub.calls == []


@pytest.mark.asyncio
async def test_start_clears_an_orphaned_running_job(client, db, stub) -> None:
    """プロセス再起動で残った running (10 分超) は failed に倒して新しい実行を通す。"""
    await _seed_week(db)
    admin = await _make_admin(db)
    orphan = await _seed_job(db, admin, minutes_ago=30)
    stub.by_month[MONTH] = _csv(_kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)))

    res = await _start(client, admin)
    assert res.status_code == 202, res.text
    assert res.json()["jobId"] != str(orphan.id)
    await db.refresh(orphan)
    assert orphan.status == "failed"
    assert "中断されました" in orphan.result_summary["error"]


@pytest.mark.asyncio
async def test_status_reports_an_orphaned_job_as_failed(client, db, stub) -> None:
    """画面が残骸をポーリングし続けないよう、status でも failed に倒す。"""
    await _seed_week(db)
    admin = await _make_admin(db)
    orphan = await _seed_job(db, admin, minutes_ago=30)

    st = (await _status(client, admin, str(orphan.id))).json()
    assert st["status"] == "failed"
    assert "中断されました" in st["error"]


@pytest.mark.asyncio
async def test_status_of_a_cancelled_job_is_failed(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    job = await _seed_job(db, admin)
    job.status = "cancelled"
    await db.commit()

    st = (await _status(client, admin, str(job.id))).json()
    assert st["status"] == "failed"
    assert "取り消されました" in st["error"]


@pytest.mark.asyncio
async def test_status_404_for_other_ops(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    other = KaipokeJob(
        job_type="fetch",
        week_start=WEEK_START,
        params={"op": "plan-actual-compare", "month": MONTH},
        status="running",
    )
    db.add(other)
    await db.commit()
    res = await _status(client, admin, str(other.id))
    assert res.status_code == 404, res.text


# --- 再開 (画面へ戻ったとき) ---------------------------------------------------


@pytest.mark.asyncio
async def test_active_returns_the_running_job_for_the_week(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    job = await _seed_job(db, admin)

    res = await client.get(
        ACTIVE_URL, headers=_bearer(admin), params={"weekStart": WEEK_START.isoformat()}
    )
    assert res.status_code == 200, res.text
    assert res.json()["jobId"] == str(job.id)
    assert res.json()["status"] == "running"

    other = await client.get(
        ACTIVE_URL,
        headers=_bearer(admin),
        params={"weekStart": (WEEK_START + timedelta(days=7)).isoformat()},
    )
    assert other.status_code == 200, other.text
    assert other.json() is None


@pytest.mark.asyncio
async def test_active_ignores_orphans_and_sync_jobs(client, db, stub) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    orphan = await _seed_job(db, admin, minutes_ago=30)
    db.add(
        KaipokeJob(
            job_type="fetch",
            week_start=WEEK_START,
            params={"op": "smart-preview", "week_start": WEEK_START.isoformat()},
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    await db.commit()

    res = await client.get(
        ACTIVE_URL, headers=_bearer(admin), params={"weekStart": WEEK_START.isoformat()}
    )
    assert res.status_code == 200, res.text
    assert res.json() is None
    await db.refresh(orphan)
    assert orphan.status == "failed"


@pytest.mark.asyncio
async def test_endpoints_require_admin(client, db, stub) -> None:
    from app.core.security import hash_password
    from app.models import User

    staff = User(email="sp-staff@example.com", password_hash=hash_password("x"), role="staff")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)
    res = await _start(client, staff)
    assert res.status_code == 403, res.text


# --- 計測: 月を跨ぐ週でも即応答する ------------------------------------------


@pytest.mark.asyncio
async def test_month_crossing_week_returns_immediately(client, db, stub, monkeypatch) -> None:
    """10/26 の週 (10 月+11 月の export 2 本) でも、リクエストは export を待たない。

    本番の RPA は 1 本 ~50s。スタブの export に 1 本 2 秒の遅延を入れ、
    リクエストが export を 1 本も撃たずに返ることを確かめる。ASGITransport は
    バックグラウンドの終了まで待つため、ここでは実行本体を記録用に差し替え、
    本体は後から直接 await して 2 本分の export と完了を確かめる。
    """
    from app.api.v1 import integrations as integrations_module

    await _seed_week(db)
    admin = await _make_admin(db)
    stub.delay = 2.0
    stub.by_month["2026-10"] = _csv(_kp_row(date(2026, 10, 27), time(9, 0), time(9, 30)))
    stub.by_month["2026-11"] = _csv(_kp_row(date(2026, 11, 1), time(9, 0), time(9, 30)))

    scheduled: list[dict[str, Any]] = []

    async def _record(**kwargs: Any) -> None:
        scheduled.append(kwargs)

    monkeypatch.setattr(integrations_module, "_run_smart_preview_job", _record)

    t0 = _time.perf_counter()
    res = await _start(client, admin, CROSS_MONDAY)
    elapsed = _time.perf_counter() - t0
    assert res.status_code == 202, res.text
    assert elapsed < 1.0, f"start took {elapsed:.2f}s"
    assert stub.calls == []  # リクエスト中は export を撃たない
    assert len(scheduled) == 1

    monkeypatch.undo()
    t1 = _time.perf_counter()
    await integrations_module._run_smart_preview_job(**scheduled[0])
    run_elapsed = _time.perf_counter() - t1
    # 実体は export 2 本 (10 月 → 11 月) を直列に撃つ = ここで時間がかかる。
    assert [c["month"] for c in stub.calls] == ["2026-10", "2026-11"]
    assert run_elapsed >= 4.0

    st = (await _status(client, admin, res.json()["jobId"])).json()
    assert st["status"] == "completed", st
    assert st["preview"]["weekStart"] == CROSS_MONDAY.isoformat()
    # 置換で 10/27 (火) の 1 件を挿入する (日曜 11/1 は置換の対象外)。
    assert st["preview"]["replace"]["inserted"] == 1
