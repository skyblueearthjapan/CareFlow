"""連携結果レポートの明細保存 (kaipoke_job_items) のテスト — 設計 §2 / §6。

検証観点:
  1. 純関数: content の形・理由ラベル・並び (日付/時刻/利用者)・辞書の網羅
  2. 突合: RPA details[] と (日, 正規化氏名, action) で確定 / 未突合は no_rpa_result
  3. API: apply(除外+pending → reconcile で確定) / apply-inbound / replace-inbound /
     イベント取込・送信 が明細を残すこと
  4. 回帰: _reconcile_latest_job の先取りクローズ除外が実 op 名 "events-outbound"
     (旧: "events-outbound-apply") で効くこと
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.kaipoke_job import KaipokeJob, KaipokeJobItem
from app.services import kaipoke_client as kc_module
from app.services.kaipoke import sync_report_items as sri

# --- helpers ---------------------------------------------------------------


async def _make_admin(db, email: str = "sri-admin@example.com", **kw) -> User:
    user = User(email=email, password_hash=hash_password("x"), role="admin", **kw)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


class _FakeItem:
    """CorrectionSheetItem のダック型 (純関数テスト用)。"""

    def __init__(self, action: str, before: dict | None, after: dict | None) -> None:
        self.id = uuid.uuid4()
        self.action = action
        self.before = before
        self.after = after


@dataclass
class _FakeInboundResult:
    item_id: str
    action: str
    outcome: str
    detail: str = ""
    patient_name: str = ""
    date: str = ""


@dataclass
class _FakeSkip:
    reason: str
    user_name: str
    staff_name: str
    date: str
    start: str


@dataclass
class _FakeReplaceResult:
    week_start: date
    sunday_skipped: int = 0
    skipped: list[_FakeSkip] = field(default_factory=list)
    trainee_solo: dict[str, int] = field(default_factory=dict)
    per_day: dict[date, dict[str, int]] = field(default_factory=dict)


async def _items_of(db, job_id) -> list[KaipokeJobItem]:
    return list(
        (
            await db.scalars(
                select(KaipokeJobItem)
                .where(KaipokeJobItem.job_id == job_id)
                .order_by(KaipokeJobItem.seq)
            )
        ).all()
    )


# --- 1. 純関数 -------------------------------------------------------------


def test_reason_label_known_unknown_and_empty() -> None:
    assert sri.reason_label("past") == "過去日（実績保護のため送信対象外）"
    assert (
        sri.reason_label("old_row_remains_duplicate")
        == "追加成功・旧行の削除失敗（二重・要手動削除）"
    )
    # 未知コードは原文をそのまま返す (情報を捨てない)
    assert sri.reason_label("brand_new_code") == "brand_new_code"
    assert sri.reason_label(None) is None
    assert sri.reason_label("") is None


def test_op_labels_cover_every_reportable_op() -> None:
    assert sri.REPORTABLE_OPS == {
        "apply",
        "apply-inbound",
        "smart-apply",
        "replace-inbound",
        "apply-events",
        "events-outbound",
    }
    assert sri.REPORTABLE_OPS <= set(sri.OP_LABELS)
    for op in ("diff-local", "diff-inbound", "events-preview", "smart-preview"):
        assert op in sri.OP_LABELS
    assert sri.op_direction("apply") == "outbound"
    assert sri.op_direction("events-outbound") == "outbound"
    assert sri.op_direction("replace-inbound") == "inbound"
    # 未知 op はラベルを原文にフォールバック (辞書の穴で表紙が空にならない)
    assert sri.op_label("mystery") == "mystery"


def test_row_content_shape_from_sheet_item() -> None:
    item = _FakeItem(
        "edit",
        {
            "date": "9",
            "start_time": "09:00",
            "end_time": "09:35",
            "staff1": "熊澤　妙子",
            "staff2": "",
            "service_type": "精神基本療養費Ⅰ・正看",
            "user_name": "山田　太郎",
        },
        {
            "date": "9",
            "start_time": "10:00",
            "end_time": "10:35",
            "staff1": "高岡　花子",
            "staff2": "",
            "service_type": "精神基本療養費Ⅰ・正看",
            "user_name": "山田　太郎",
        },
    )
    content = sri.row_content_from_sheet_item(
        item, direction="outbound", resolved_date=date(2026, 9, 9), outcome="pending"
    )
    assert content["kind"] == "row"
    assert content["direction"] == "outbound"
    assert content["date"] == "2026-09-09"
    assert content["start"] == "10:00" and content["end"] == "10:35"  # after 優先
    assert content["user_name"] == "山田　太郎"
    assert content["action"] == "edit"
    assert content["outcome"] == "pending"
    assert content["reason"] is None and content["reason_label"] is None
    assert set(content["before"]) == {"date", "start", "end", "staff1", "staff2", "service"}
    assert content["before"]["staff1"] == "熊澤　妙子"
    assert content["after"]["staff1"] == "高岡　花子"
    assert content["ref"] == {"sheet_item_id": str(item.id), "visit_id": None}


def test_row_content_excluded_carries_japanese_reason() -> None:
    item = _FakeItem("delete", {"date": "1", "user_name": "過去　太郎"}, None)
    content = sri.row_content_from_sheet_item(
        item, direction="outbound", resolved_date=None, outcome="excluded", reason="past"
    )
    assert content["date"] is None  # 週外/解決不能は null
    assert content["after"] is None
    assert content["outcome"] == "excluded"
    assert content["reason"] == "past"
    assert content["reason_label"] == sri.REASON_LABELS["past"]


@pytest.mark.parametrize(
    ("outcome", "action", "expect_outcome", "expect_action"),
    [
        ("cancelled", "delete", "success", "cancel"),
        ("updated", "edit", "success", "update"),
        ("added", "add", "success", "add"),
        ("skipped", "delete", "skipped", "cancel"),
        ("failed", "date_change", "failed", "date_change"),
    ],
)
def test_row_content_from_inbound_result(outcome, action, expect_outcome, expect_action) -> None:
    item = _FakeItem(action, {"date": "8", "start_time": "10:00", "user_name": "山田　太郎"}, None)
    result = _FakeInboundResult(
        item_id=str(item.id),
        action=action,
        outcome=outcome,
        detail="既にキャンセル済み",
        patient_name="山田　太郎",
        date="2026-07-08",
    )
    content = sri.row_content_from_inbound_result(result, item)
    assert content["kind"] == "row" and content["direction"] == "inbound"
    assert content["outcome"] == expect_outcome
    assert content["action"] == expect_action
    assert content["date"] == "2026-07-08"
    # 取込側の detail は既に日本語 → そのままラベルになる
    assert content["reason"] == "既にキャンセル済み"
    assert content["reason_label"] == "既にキャンセル済み"


def test_replace_contents_days_skips_and_trainee_solo() -> None:
    week = date(2026, 7, 6)
    result = _FakeReplaceResult(
        week_start=week,
        sunday_skipped=2,
        skipped=[_FakeSkip("患者を名寄せできません", "未知　患者", "看護A", "2026-07-08", "11:00")],
        trainee_solo={"髙梨　桂子": 1},
        per_day={
            week: {"wiped": 3, "inserted": 2},
            week + timedelta(days=1): {"wiped": 0, "inserted": 1},
        },
    )
    contents = sri.replace_contents(result)
    kinds = [c["kind"] for c in contents]
    assert kinds.count("day") == 3  # 月・火 + 日曜スキップの 1 件
    assert kinds.count("skip") == 1 and kinds.count("trainee_solo") == 1

    days = {c["date"]: c for c in contents if c["kind"] == "day"}
    assert days["2026-07-06"] == {
        "kind": "day",
        "direction": "inbound",
        "date": "2026-07-06",
        "wiped": 3,
        "inserted": 2,
        "sunday_skipped": False,
    }
    assert days["2026-07-12"]["sunday_skipped"] is True

    skip = next(c for c in contents if c["kind"] == "skip")
    assert skip["user_name"] == "未知　患者" and skip["staff_name"] == "看護A"
    assert skip["reason_label"] == "患者を名寄せできません"
    solo = next(c for c in contents if c["kind"] == "trainee_solo")
    assert solo == {
        "kind": "trainee_solo",
        "direction": "inbound",
        "staff_name": "髙梨　桂子",
        "count": 1,
    }


def test_sort_contents_orders_by_date_start_user_and_kind() -> None:
    def row(d: str | None, start: str, user: str) -> dict[str, Any]:
        return {"kind": "row", "date": d, "start": start, "user_name": user}

    ordered = sri.sort_contents(
        [
            {"kind": "trainee_solo", "staff_name": "新人"},
            row(None, "", "日付なし"),
            row("2026-09-09", "10:00", "後の人"),
            {"kind": "skip", "date": "2026-09-08", "start": "09:00", "user_name": "対象外"},
            row("2026-09-08", "09:00", "先の人"),
            row("2026-09-09", "09:00", "早い時刻"),
        ]
    )
    assert [(c["kind"], c.get("date"), c.get("start")) for c in ordered] == [
        ("row", "2026-09-08", "09:00"),
        ("row", "2026-09-09", "09:00"),
        ("row", "2026-09-09", "10:00"),
        ("row", None, ""),
        ("skip", "2026-09-08", "09:00"),
        ("trainee_solo", None, None),
    ]


def test_executor_name_prefers_username_then_staff_then_email() -> None:
    class _U:
        def __init__(self, username=None, staff=None, email=None):
            self.username = username
            self.staff = staff
            self.email = email

    class _S:
        def __init__(self, name):
            self.name = name

    assert sri.executor_name(_U(username="s001")) == "s001"
    assert sri.executor_name(_U(staff=_S("川名　太郎"))) == "川名　太郎"
    assert sri.executor_name(_U(email="admin-01@example.com")) == "admin-01"
    assert sri.executor_name(_U()) == ""
    assert sri.executor_name(None) == ""


# --- 2. 保存・突合 (DB) ------------------------------------------------------


async def _seed_job(db, op: str = "apply") -> KaipokeJob:
    job = KaipokeJob(
        job_type="push",
        week_start=date(2026, 9, 7),
        params={"op": op},
        status="running",
    )
    db.add(job)
    await db.flush()
    return job


@pytest.mark.asyncio
async def test_write_job_items_is_idempotent_and_numbers_seq(db) -> None:
    job = await _seed_job(db)
    contents = [
        sri.row_content(
            direction="outbound",
            action="add",
            user_name="遅い　人",
            before=None,
            after={"date": "9", "start_time": "13:00"},
            resolved_date=date(2026, 9, 9),
            outcome="pending",
        ),
        sri.row_content(
            direction="outbound",
            action="delete",
            user_name="早い　人",
            before={"date": "8", "start_time": "09:00"},
            after=None,
            resolved_date=date(2026, 9, 8),
            outcome="excluded",
            reason="past",
        ),
    ]
    assert await sri.write_job_items(db, job.id, contents) == 2
    items = await _items_of(db, job.id)
    assert [i.seq for i in items] == [1, 2]
    assert [i.content["user_name"] for i in items] == ["早い　人", "遅い　人"]  # 日付順
    assert items[0].status == "excluded"
    assert items[0].error_msg == sri.REASON_LABELS["past"]
    assert items[1].status == "pending" and items[1].error_msg is None

    # 二度書きしても重複しない (冪等)
    assert await sri.write_job_items(db, job.id, contents) == 2
    assert len(await _items_of(db, job.id)) == 2


@pytest.mark.asyncio
async def test_finalize_apply_items_matches_day_name_and_action(db) -> None:
    job = await _seed_job(db)
    await sri.write_job_items(
        db,
        job.id,
        [
            sri.row_content(
                direction="outbound",
                action="add",
                user_name="山田　太郎",  # RPA 側は空白なしで返ってくる
                before=None,
                after={"date": "9", "start_time": "09:00"},
                resolved_date=date(2026, 9, 9),
                outcome="pending",
            ),
            sri.row_content(
                direction="outbound",
                action="delete",
                user_name="木村　花子",
                before={"date": "7", "start_time": "16:45"},
                after=None,
                resolved_date=date(2026, 9, 7),
                outcome="pending",
            ),
            sri.row_content(
                direction="outbound",
                action="edit",
                user_name="返事なし　三郎",
                before={"date": "10", "start_time": "11:00"},
                after={"date": "10", "start_time": "12:00"},
                resolved_date=date(2026, 9, 10),
                outcome="pending",
            ),
            sri.row_content(
                direction="outbound",
                action="delete",
                user_name="過去　四郎",
                before={"date": "1", "start_time": "08:00"},
                after=None,
                resolved_date=date(2026, 9, 1),
                outcome="excluded",
                reason="past",
            ),
        ],
    )

    settled = await sri.finalize_apply_items(
        db,
        job,
        [
            {"status": "success", "action": "add", "date": "9", "user": "山田太郎"},
            {
                "status": "failed",
                "action": "delete",
                "date": "7",
                "user": "木村　花子",
                "reason": "old_row_remains_duplicate",
            },
        ],
    )
    assert settled == 2

    by_name = {i.content["user_name"]: i for i in await _items_of(db, job.id)}
    assert by_name["山田　太郎"].content["outcome"] == "success"
    assert by_name["山田　太郎"].status == "success"
    assert by_name["木村　花子"].content["outcome"] == "failed"
    assert by_name["木村　花子"].content["reason"] == "old_row_remains_duplicate"
    assert by_name["木村　花子"].error_msg == "追加成功・旧行の削除失敗（二重・要手動削除）"
    # 突合できなかった行は pending のままにせず「不明 (要目視)」へ倒す
    assert by_name["返事なし　三郎"].content["outcome"] == "unknown"
    assert by_name["返事なし　三郎"].status == "unknown"
    assert by_name["返事なし　三郎"].content["reason"] == "no_rpa_result"
    assert by_name["返事なし　三郎"].error_msg == "RPA から結果が返らなかった（要目視）"
    # excluded 行は突合対象外 (理由が past のまま)
    assert by_name["過去　四郎"].content["reason"] == "past"


@pytest.mark.asyncio
async def test_finalize_apply_items_ignores_unknown_details(db) -> None:
    """details に居ない行・action 違いの行は突合しない (取り違え防止)。"""
    job = await _seed_job(db)
    await sri.write_job_items(
        db,
        job.id,
        [
            sri.row_content(
                direction="outbound",
                action="add",
                user_name="山田　太郎",
                before=None,
                after={"date": "9", "start_time": "09:00"},
                resolved_date=date(2026, 9, 9),
                outcome="pending",
            )
        ],
    )
    # action 違い (delete) は同じ日・同じ人でも突合しない
    assert (
        await sri.finalize_apply_items(
            db, job, [{"status": "success", "action": "delete", "date": "9", "user": "山田太郎"}]
        )
        == 0
    )
    item = (await _items_of(db, job.id))[0]
    assert item.content["outcome"] == "unknown"
    assert item.content["reason"] == "no_rpa_result"


# --- 3. API: apply (除外 + pending) → reconcile で確定 ------------------------


class _ApplyStub:
    """apply / status / *_result / logs を返す最小スタブ。"""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []
        self.export_by_month: dict[str, str] = {}
        self.async_job_id: str | None = None
        self.sent_items: list[dict[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover — interface stub
        pass

    def _dispatch(self, name: str, payload: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        return self.responses.get(name, {})

    async def status(self) -> dict[str, Any]:
        return self._dispatch("status", None)

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch("apply", payload)

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(("export", dict(payload)))
        return {"result": {"csv_content": self.export_by_month.get(str(payload.get("month")), "")}}

    async def individual_tasks_apply(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(("individual_tasks_apply", dict(payload)))
        self.async_job_id = str(payload.get("job_id") or "") or None
        self.sent_items = list(payload.get("items") or [])
        return {"success": True, "async": True, "job_id": self.async_job_id}

    async def apply_result(self) -> dict[str, Any]:
        return self._dispatch("apply_result", None)

    async def export_result(self) -> dict[str, Any]:
        return self._dispatch("export_result", None)

    async def individual_tasks_apply_result(self) -> dict[str, Any]:
        return self._dispatch("individual_tasks_apply_result", None)

    async def logs(self, tail: int = 200) -> dict[str, Any]:
        return self._dispatch("logs", tail)


@pytest.fixture
def stub_kaipoke():
    stub = _ApplyStub()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


@pytest.mark.asyncio
async def test_apply_writes_excluded_and_pending_items_then_reconcile_settles(
    client, db, stub_kaipoke
) -> None:
    """送信は「除外した行 (理由つき)」と「送った行 (pending)」を明細に残し、
    RPA が決着した時点で details[] から成否が入る (設計 §2)。"""
    from zoneinfo import ZoneInfo

    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_admin(db, "sri-apply@example.com", username="s001")
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    week_start = today - timedelta(days=2)
    past_day = str((today - timedelta(days=1)).day)
    future_day = str((today + timedelta(days=2)).day)
    sheet = CorrectionSheet(
        target_month=f"{week_start.year}-{week_start.month:02d}",
        status="ready",
        direction="outbound",
        week_start=week_start,
        created_by_user_id=admin.id,
    )
    db.add(sheet)
    await db.flush()
    db.add_all(
        [
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="delete",
                before={"date": past_day, "start_time": "08:00", "user_name": "過去　太郎"},
                after=None,
                include=True,
            ),
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="delete",
                before={"date": future_day, "start_time": "16:45", "user_name": "木村　花子"},
                after=None,
                include=True,
            ),
        ]
    )
    await db.commit()

    stub_kaipoke.responses["apply"] = {"async": True}
    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": False},
    )
    assert res.status_code == 202, res.text
    job_id = res.json()["jobId"]

    items = await _items_of(db, uuid.UUID(job_id))
    assert len(items) == 2
    by_name = {i.content["user_name"]: i for i in items}
    assert by_name["過去　太郎"].content["outcome"] == "excluded"
    assert by_name["過去　太郎"].content["reason"] == "past"
    assert by_name["過去　太郎"].error_msg == sri.REASON_LABELS["past"]
    assert by_name["木村　花子"].content["outcome"] == "pending"
    assert by_name["木村　花子"].content["direction"] == "outbound"

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == uuid.UUID(job_id)))
    assert job.result_summary["report_meta"] == {
        "direction": "outbound",
        "op_label": "訪問をカイポケへ送信",
        "executor_name": "s001",
    }

    # RPA が idle + details 付きで完了 → /live の reconcile で明細が確定する。
    stub_kaipoke.responses["status"] = {"current_task": {"running": False, "command": None}}
    stub_kaipoke.responses["apply_result"] = {
        "status": "completed",
        "result": {
            "success": 0,
            "failed": 1,
            "skipped": 0,
            "details": [
                {
                    "status": "failed",
                    "action": "delete",
                    "date": future_day,
                    "user": "木村花子",
                    "reason": "delete_not_verified",
                }
            ],
        },
    }
    stub_kaipoke.responses["logs"] = {"lines": [], "total": 0}

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["latestJob"]["status"] == "completed"

    db.expire_all()
    items = await _items_of(db, uuid.UUID(job_id))
    by_name = {i.content["user_name"]: i for i in items}
    assert by_name["木村　花子"].content["outcome"] == "failed"
    assert by_name["木村　花子"].content["reason"] == "delete_not_verified"
    assert by_name["木村　花子"].error_msg == "削除の反映を確認できない"
    assert by_name["木村　花子"].status == "failed"
    # 除外行は RPA の結果に関係なく excluded のまま
    assert by_name["過去　太郎"].content["outcome"] == "excluded"


# --- 4. 回帰: events-outbound の op 名 --------------------------------------


@pytest.mark.asyncio
async def test_events_outbound_job_is_not_preclosed_by_reconcile(client, db, stub_kaipoke) -> None:
    """回帰 (設計 §2 の既知の穴): 除外リストが "events-outbound-apply" と
    書かれており実 op "events-outbound" と一致せず、イベント送信ジョブが
    result_unknown の completed で先取りクローズされ結果を失っていた。"""
    admin = await _make_admin(db, "sri-eo@example.com")
    job = KaipokeJob(
        job_type="push",
        week_start=date(2026, 9, 7),
        params={"op": "events-outbound", "async": True},
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(job)
    await db.commit()

    stub_kaipoke.responses["status"] = {"current_task": {"running": False, "command": None}}
    stub_kaipoke.responses["apply_result"] = {"status": "no_result"}
    stub_kaipoke.responses["export_result"] = {"status": "no_result"}
    stub_kaipoke.responses["logs"] = {"lines": [], "total": 0}

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    # 30 分以内の events-outbound は自分の status エンドポイントに任せる
    assert res.json()["latestJob"]["status"] == "running"

    job_id = job.id
    db.expire_all()
    refreshed = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    assert refreshed.status == "running"
    assert not (refreshed.result_summary or {}).get("result_unknown")


@pytest.mark.asyncio
async def test_stale_events_outbound_job_is_still_closed(client, db, stub_kaipoke) -> None:
    """放置残骸 (30 分超) は従来どおり閉じる (除外は「新しいうちだけ」)。"""
    admin = await _make_admin(db, "sri-eo-old@example.com")
    job = KaipokeJob(
        job_type="push",
        week_start=date(2026, 9, 7),
        params={"op": "events-outbound", "async": True},
        status="running",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db.add(job)
    await db.commit()
    job.created_at = datetime.now(UTC) - timedelta(hours=2)
    await db.commit()

    stub_kaipoke.responses["status"] = {"current_task": {"running": False, "command": None}}
    stub_kaipoke.responses["apply_result"] = {"status": "no_result"}
    stub_kaipoke.responses["export_result"] = {"status": "no_result"}
    stub_kaipoke.responses["logs"] = {"lines": [], "total": 0}

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["latestJob"]["status"] == "completed"


@pytest.mark.asyncio
async def test_apply_result_unknown_marks_pending_rows_unknown(client, db, stub_kaipoke) -> None:
    """RPA の結果が失われたまま決着した (result_unknown) 場合、送った行を
    pending のまま放置せず「不明 (要目視)」にする。"""
    from zoneinfo import ZoneInfo

    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_admin(db, "sri-unknown@example.com")
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    week_start = today - timedelta(days=2)
    future_day = str((today + timedelta(days=2)).day)
    sheet = CorrectionSheet(
        target_month=f"{week_start.year}-{week_start.month:02d}",
        status="ready",
        direction="outbound",
        week_start=week_start,
        created_by_user_id=admin.id,
    )
    db.add(sheet)
    await db.flush()
    db.add(
        CorrectionSheetItem(
            sheet_id=sheet.id,
            action="delete",
            before={"date": future_day, "start_time": "16:45", "user_name": "結果不明　子"},
            after=None,
            include=True,
        )
    )
    await db.commit()

    stub_kaipoke.responses["apply"] = {"async": True}
    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": False},
    )
    assert res.status_code == 202, res.text
    job_id = uuid.UUID(res.json()["jobId"])

    # worker は idle だが結果が取れない → result_unknown で completed。
    stub_kaipoke.responses["status"] = {"current_task": {"running": False, "command": None}}
    stub_kaipoke.responses["apply_result"] = {"status": "no_result"}
    stub_kaipoke.responses["export_result"] = {"status": "no_result"}
    stub_kaipoke.responses["logs"] = {"lines": [], "total": 0}
    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["latestJob"]["result_summary"]["result_unknown"] is True

    db.expire_all()
    item = (await _items_of(db, job_id))[0]
    assert item.content["outcome"] == "unknown"
    assert item.status == "unknown"
    assert item.content["reason"] == "no_rpa_result"
    assert item.error_msg == "RPA から結果が返らなかった（要目視）"


# --- 5. API: 取込 (差分 / 置換) --------------------------------------------


@pytest.mark.asyncio
async def test_apply_inbound_writes_row_items(client, db, stub_kaipoke) -> None:
    """差分取込は行単位の明細 (direction=inbound) を残す。"""
    from tests.test_kaipoke_inbound import (
        MONTH,
        WEEK_START,
        _default_kaipoke_state,
        _seed_real_apply,
        _seed_week,
    )

    await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db, "sri-inbound@example.com")

    stub_kaipoke.responses["export"] = {"result": {"csv_content": _default_kaipoke_state()}}
    stub_kaipoke.export_by_month[MONTH] = _default_kaipoke_state()
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 202, res.text
    sheet_id = res.json()["sheetId"]

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": False},
    )
    assert res.status_code == 200, res.text
    job_id = uuid.UUID(res.json()["jobId"])

    items = await _items_of(db, job_id)
    assert len(items) == 2  # edit(火) + delete(水)
    assert [i.seq for i in items] == [1, 2]
    by_action = {i.content["action"]: i for i in items}
    assert set(by_action) == {"update", "cancel"}
    assert all(i.content["kind"] == "row" for i in items)
    assert all(i.content["direction"] == "inbound" for i in items)
    assert by_action["update"].content["outcome"] == "success"
    assert by_action["update"].content["date"] == "2026-07-07"
    assert by_action["update"].content["after"]["start"] == "14:00"
    assert by_action["update"].content["before"]["start"] == "10:00"
    assert by_action["cancel"].content["date"] == "2026-07-08"
    assert by_action["cancel"].status == "success"

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    assert job.result_summary["report_meta"]["op_label"] == "カイポケの差分を取込"
    assert job.result_summary["report_meta"]["direction"] == "inbound"


@pytest.mark.asyncio
async def test_replace_inbound_writes_day_and_skip_items(client, db, stub_kaipoke) -> None:
    """置換取込は日ごとの内訳 + 対象外 + 新人単独を明細に残す。"""
    from app.models.staff import Staff
    from tests.test_kaipoke_inbound import (
        MONTH,
        WEEK_START,
        _kaipoke_csv,
        _kp_row,
        _seed_course,
        _seed_week,
    )

    seeded = await _seed_week(db)
    await _seed_course(db, office=seeded["office"], staff=seeded["staff"], weekday=1, code="A")
    trainee = Staff(name="髙梨　桂子", role="staff", primary_office_id=seeded["office"].id)
    trainee.is_trainee = True
    db.add(trainee)
    await db.commit()
    admin = await _make_admin(db, "sri-replace@example.com")

    stub_kaipoke.export_by_month[MONTH] = _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),  # 火: 正常
        _kp_row(date(2026, 7, 8), time(11, 0), time(11, 35), patient_name="未知　患者"),
        _kp_row(date(2026, 7, 9), time(12, 0), time(12, 35), staff_name="髙梨　桂子"),
        _kp_row(date(2026, 7, 12), time(9, 0), time(9, 30)),  # 日曜 → 対象外
    )

    res = await client.post(
        "/api/v1/integrations/replace-inbound",
        headers=_bearer(admin),
        json={"weekStart": WEEK_START.isoformat(), "dryRun": False},
    )
    assert res.status_code == 200, res.text
    job_id = uuid.UUID(res.json()["jobId"])

    items = await _items_of(db, job_id)
    kinds = [i.content["kind"] for i in items]
    assert kinds.count("skip") == 1 and kinds.count("trainee_solo") == 1
    days = {i.content["date"]: i.content for i in items if i.content["kind"] == "day"}
    # 月〜土の 6 日 + 日曜スキップの 1 件
    assert len(days) == 7
    assert days["2026-07-07"]["wiped"] == 1 and days["2026-07-07"]["inserted"] == 1
    assert days["2026-07-08"]["wiped"] == 1 and days["2026-07-08"]["inserted"] == 0
    assert days["2026-07-09"]["wiped"] == 1 and days["2026-07-09"]["inserted"] == 1
    assert days["2026-07-06"] == {
        "kind": "day",
        "direction": "inbound",
        "date": "2026-07-06",
        "wiped": 0,
        "inserted": 0,
        "sunday_skipped": False,
    }
    assert days["2026-07-12"]["sunday_skipped"] is True

    skip = next(i for i in items if i.content["kind"] == "skip")
    assert skip.content["user_name"] == "未知　患者"
    assert skip.status == "skipped" and skip.error_msg == skip.content["reason_label"]
    solo = next(i for i in items if i.content["kind"] == "trainee_solo")
    assert solo.content["staff_name"] == "髙梨　桂子" and solo.content["count"] == 1

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    assert job.result_summary["report_meta"]["op_label"] == "カイポケから置換取込"


# --- 6. API: イベント (取込 / 送信) ----------------------------------------


@pytest.mark.asyncio
async def test_apply_events_writes_event_items(client, db, stub_kaipoke) -> None:
    from app.models.staff import Staff

    admin = await _make_admin(db, "sri-events-in@example.com")
    staff = Staff(name="川名　太郎", role="staff")
    db.add(staff)
    await db.commit()
    await db.refresh(staff)

    res = await client.post(
        "/api/v1/integrations/events-inbound-apply",
        headers=_bearer(admin),
        json={
            "weekStart": "2026-09-07",
            "dryRun": False,
            "changes": [
                {
                    "action": "add",
                    "externalId": "111:222:2026-09-09",
                    "staffId": str(staff.id),
                    "staffName": "川名　太郎",
                    "date": "2026-09-09",
                    "start": "09:00",
                    "end": "09:15",
                    "title": "朝会",
                },
                {
                    "action": "update",
                    "externalId": "111:333:2026-09-10",
                    "staffId": str(staff.id),
                    "staffName": "川名　太郎",
                    "date": "2026-09-10",
                    "start": "10:00",
                    "end": "11:00",
                    "title": "面談(変更後)",
                    "beforeStart": "09:30",
                    "beforeEnd": "10:30",
                    "beforeTitle": "面談",
                },
            ],
        },
    )
    assert res.status_code == 200, res.text
    job_id = uuid.UUID(res.json()["jobId"])

    items = await _items_of(db, job_id)
    assert [i.content["kind"] for i in items] == ["event", "event"]
    assert all(i.content["direction"] == "inbound" for i in items)
    first = items[0].content
    assert first["date"] == "2026-09-09" and first["start"] == "09:00"
    assert first["title"] == "朝会" and first["action"] == "add"
    assert first["external_id"] == "111:222:2026-09-09"
    assert first["outcome"] == "success" and first["before"] is None
    second = items[1].content
    assert second["before"] == {"start": "09:30", "end": "10:30", "title": "面談"}

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    assert job.result_summary["report_meta"]["op_label"] == "イベントを取込"


@pytest.mark.asyncio
async def test_events_outbound_writes_and_finalizes_event_items(client, db, stub_kaipoke) -> None:
    """イベント送信は起動時に pending で明細を書き、status 完了で確定させる。"""
    from app.models.staff import Staff, StaffEvent

    admin = await _make_admin(db, "sri-events-out@example.com")
    staff = Staff(name="紐付　済子", role="staff")
    db.add(staff)
    await db.flush()
    # 取込済み行 = 職員内部IDの逆引き供給源
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime(2026, 8, 3, 9, 0),
            ends_at=datetime(2026, 8, 3, 10, 0),
            title="過去取込",
            source="kaipoke",
            external_id="111:4601519:2026-08-03",
        )
    )
    ev = StaffEvent(
        staff_id=staff.id,
        event_type="event",
        starts_at=datetime(2026, 9, 9, 9, 0),
        ends_at=datetime(2026, 9, 9, 9, 15),
        title="朝会",
        source="manual",
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    event_id = str(ev.id)

    res = await client.post(
        "/api/v1/integrations/events-outbound-apply/start",
        headers=_bearer(admin),
        json={"weekStart": "2026-09-07"},
    )
    assert res.status_code == 202, res.text
    job_id = uuid.UUID(res.json()["jobId"])

    items = await _items_of(db, job_id)
    assert len(items) == 1
    assert items[0].content["kind"] == "event"
    assert items[0].content["direction"] == "outbound"
    assert items[0].content["outcome"] == "pending"
    assert items[0].content["external_id"] == event_id
    assert items[0].content["date"] == "2026-09-09"
    assert items[0].content["start"] == "09:00" and items[0].content["end"] == "09:15"
    assert items[0].content["title"] == "朝会"

    stub_kaipoke.responses["individual_tasks_apply_result"] = {
        "status": "completed",
        "job_id": str(job_id),
        "result": {
            "success": True,
            "total": 1,
            "ok": 1,
            "results": [
                {
                    "external_ref": event_id,
                    "outcome": "added",
                    "external_key": "900001:4601519:2026-09-09",
                }
            ],
        },
    }
    res = await client.get(
        f"/api/v1/integrations/events-outbound-apply/status/{job_id}",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"

    db.expire_all()
    items = await _items_of(db, job_id)
    assert items[0].content["outcome"] == "success"
    assert items[0].status == "success"
    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == job_id))
    assert job.result_summary["report_meta"] == {
        "direction": "outbound",
        "op_label": "イベントをカイポケへ送信",
        "executor_name": "sri-events-out",
    }
