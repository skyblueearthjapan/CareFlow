"""Tests for /api/v1/integrations/* — Wave 4-A (kaipoke relay).

Stubs `KaipokeClient` via `set_test_client()` so we never hit the network.
Covers: HTTP shape, RBAC boundary (admin only), 409 busy, 502 upstream
error, diff coalesce (delete+add ±1d → companion_change), apply selection
filtering, bulk update, and correction-sheet listing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.security import create_access_token, hash_password
from app.models import User
from app.services import kaipoke_client as kc_module

# --- helpers ---------------------------------------------------------------


async def _make_user(db, email: str, role: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("does-not-matter-here"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


class StubKaipokeClient:
    """Drop-in stub: each method returns whatever is queued in `responses`."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, Exception] = {}
        self.calls: list[tuple[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover — interface stub
        pass

    async def status(self) -> dict[str, Any]:
        return self._dispatch("status", None)

    async def expand(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("expand", payload)

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("export", payload)

    async def diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch("diff", payload)

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch("apply", payload)

    async def export_result(self) -> dict[str, Any]:
        return self._dispatch("export_result", None)

    async def apply_result(self) -> dict[str, Any]:
        return self._dispatch("apply_result", None)

    async def logs(self, tail: int = 200) -> dict[str, Any]:
        return self._dispatch("logs", tail)

    async def stop(self) -> dict[str, Any]:
        return self._dispatch("stop", None)

    def _dispatch(self, name: str, payload: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        if name in self.errors:
            raise self.errors[name]
        return self.responses.get(name, {})


@pytest.fixture
def stub_kaipoke():
    """Install a StubKaipokeClient as the module-level override for the test."""
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


# --- 1. status -------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_admin_combines_kaipoke_and_db(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-admin@example.com", "admin")
    stub_kaipoke.responses["status"] = {
        "loginRemainSec": 1200,
        "lastSyncAt": "2026-05-05T01:00:00Z",
    }

    res = await client.get("/api/v1/integrations/status", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reachable"] is True
    assert body["kaipoke"]["loginRemainSec"] == 1200
    assert body["loginRemainSec"] == 1200


@pytest.mark.asyncio
async def test_status_manager_returns_403(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave4-manager@example.com", "manager")
    res = await client.get("/api/v1/integrations/status", headers=_bearer(manager))
    assert res.status_code == 403, res.text


# --- 2. expand -------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_creates_job_and_returns_202(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-expand@example.com", "admin")
    stub_kaipoke.responses["expand"] = {"jobId": "kp-123"}

    res = await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-05", "dryRun": False},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "running"
    assert body["kaipokeJobId"] == "kp-123"
    assert body["jobId"]


@pytest.mark.asyncio
async def test_expand_busy_returns_409(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-busy@example.com", "admin")
    stub_kaipoke.errors["expand"] = kc_module.KaipokeBusyError({"detail": "busy"})

    res = await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-05"},
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_expand_upstream_failure_returns_502(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-fail@example.com", "admin")
    stub_kaipoke.errors["expand"] = kc_module.KaipokeApiError(500, {"err": "boom"})

    res = await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-05"},
    )
    assert res.status_code == 502, res.text


@pytest.mark.asyncio
async def test_expand_timeout_is_tolerated_as_running(client, db, stub_kaipoke) -> None:
    """展開の長時間ブロックによる 504 タイムアウトは running 扱い (エラーにしない)。"""
    admin = await _make_user(db, "wave4-expand-timeout@example.com", "admin")
    stub_kaipoke.errors["expand"] = kc_module.KaipokeApiError(504, {"error": "timeout"})

    res = await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-10"},
    )
    assert res.status_code == 202, res.text
    assert res.json()["status"] == "running"


# --- 3. diff + correction sheet -------------------------------------------


@pytest.mark.asyncio
async def test_diff_persists_correction_sheet_and_coalesces(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-diff@example.com", "admin")
    pid = "11111111-2222-3333-4444-555555555555"
    stub_kaipoke.responses["diff"] = {
        "items": [
            # Should coalesce delete+add for same patient into companion_change.
            {"action": "delete", "patient_id": pid, "date": "2026-05-10", "before": {"k": "v"}},
            {"action": "add", "patient_id": pid, "date": "2026-05-10", "after": {"k": "v2"}},
            # Standalone update.
            {
                "action": "update",
                "patient_id": pid,
                "date": "2026-05-12",
                "before": {"x": 1},
                "after": {"x": 2},
            },
        ],
    }

    res = await client.post(
        "/api/v1/integrations/diff",
        headers=_bearer(admin),
        json={"month": "2026-05"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["sheetId"]
    assert body["summary"]["companion_change"] == 1
    assert body["summary"]["update"] == 1
    assert body["summary"]["total"] == 2

    # Latest endpoint should now find the sheet.
    latest = await client.get(
        "/api/v1/integrations/correction-sheets/latest?month=2026-05",
        headers=_bearer(admin),
    )
    assert latest.status_code == 200
    sheet = latest.json()
    assert sheet["target_month"] == "2026-05"
    assert len(sheet["items"]) == 2


# --- 4. apply --------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_requires_selected_items(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-apply@example.com", "admin")
    # First create a sheet via diff
    stub_kaipoke.responses["diff"] = {
        "items": [
            {
                "action": "update",
                "patient_id": "00000000-0000-0000-0000-000000000001",
                "before": {"x": 1},
                "after": {"x": 2},
            },
        ],
    }
    diff_res = await client.post(
        "/api/v1/integrations/diff",
        headers=_bearer(admin),
        json={"month": "2026-06"},
    )
    sheet_id = diff_res.json()["sheetId"]

    # Bulk-deselect everything → apply must 422
    items_res = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet_id}/items",
        headers=_bearer(admin),
    )
    item_id = items_res.json()["items"][0]["id"]
    await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet_id}/items/bulk",
        headers=_bearer(admin),
        json={"ids": [item_id], "patch": {"include": False}},
    )

    stub_kaipoke.responses["apply"] = {"jobId": "kp-apply"}
    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": True},
    )
    assert res.status_code == 422, res.text


# --- 4b. apply payload bridge (K-2d) --------------------------------------


@pytest.mark.asyncio
async def test_apply_sends_flat_correction_data(client, db, stub_kaipoke) -> None:
    """apply が CorrectionSheet を カイポケ平坦 correction_data 形式へ橋渡しする。"""
    admin = await _make_user(db, "wave-apply-bridge@example.com", "admin")
    # diff-local で sheet を作る (現況に1件・CareFlow空 → delete 差分)。
    current = (
        _KAIPOKE_18COL_HEADER
        + "\n"
        + "看護A,看護師,,,,,,,よりより,3,金,患者Z,医療保険,精神基本療養費Ⅰ・正看,"
        + "09:00,09:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": current}}
    diff = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    sheet_id = diff.json()["sheetId"]

    stub_kaipoke.responses["apply"] = {"async": True}
    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": True},
    )
    assert res.status_code == 202, res.text

    # apply 呼び出しの payload を検証。
    apply_calls = [p for (name, p) in stub_kaipoke.calls if name == "apply"]
    assert apply_calls, "apply が呼ばれていない"
    body = apply_calls[-1]
    assert body["dry_run"] is True
    assert "correction_data" in body and "items" not in body
    cd = body["correction_data"]
    assert cd, "correction_data が空"
    first = cd[0]
    # カイポケ Correction(**item) が復元できるフィールド名が揃っていること。
    for key in ("user_name", "date_from", "date_to", "staff1_to", "action", "business_type"):
        assert key in first, f"欠落: {key}"
    assert first["action"] == "delete"
    assert first["date_from"] == "3"  # delete は before(現況) の日付


@pytest.mark.asyncio
async def test_apply_rejects_already_applied_sheet(client, db, stub_kaipoke) -> None:
    """二重書込ガード: applied 済みシートへの apply は 409。"""
    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_user(db, "wave-apply-dup@example.com", "admin")
    sheet = CorrectionSheet(target_month="2026-07", status="applied", created_by_user_id=admin.id)
    db.add(sheet)
    await db.flush()
    db.add(
        CorrectionSheetItem(
            sheet_id=sheet.id, action="delete", before={"date": "1"}, after=None, include=True
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": False},
    )
    assert res.status_code == 409, res.text


# --- 4c. 週空間C2 (2026-08-21): 部分適用 (itemIds) + 過去日ガード -------------


def _future_week_sheet_days() -> tuple:
    """未来週の月曜と、その週の日 (day-of-month) 2つを返す (過去日ガード非発火)。"""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo

    today = _dt.now(ZoneInfo("Asia/Tokyo")).date()
    next_monday = today + _td(days=(7 - today.weekday()))
    return next_monday, str(next_monday.day), str((next_monday + _td(days=1)).day)


@pytest.mark.asyncio
async def test_apply_partial_item_ids_does_not_lock_sheet(client, db, stub_kaipoke) -> None:
    """部分適用: itemIds で1件だけ送れる・シートはロックされず連続送信できる."""
    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_user(db, "c2-partial@example.com", "admin")
    week_start, d1, d2 = _future_week_sheet_days()
    sheet = CorrectionSheet(
        target_month="2026-07",
        status="ready",
        direction="outbound",
        week_start=week_start,
        created_by_user_id=admin.id,
    )
    db.add(sheet)
    await db.flush()
    it1 = CorrectionSheetItem(
        sheet_id=sheet.id,
        action="delete",
        before={"date": d1, "user_name": "患者A"},
        after=None,
        include=True,
    )
    it2 = CorrectionSheetItem(
        sheet_id=sheet.id,
        action="delete",
        before={"date": d2, "user_name": "患者B"},
        after=None,
        include=True,
    )
    db.add_all([it1, it2])
    await db.commit()

    stub_kaipoke.responses["apply"] = {"async": True}
    res1 = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": False, "itemIds": [str(it1.id)]},
    )
    assert res1.status_code == 202, res1.text
    # 1件だけ送られている
    body1 = [p for (name, p) in stub_kaipoke.calls if name == "apply"][-1]
    assert len(body1["correction_data"]) == 1
    assert body1["correction_data"][0]["user_name"] == "患者A"

    # シートはロックされない (ready のまま) → 2件目も 202
    await db.refresh(sheet)
    assert sheet.status == "ready"
    res2 = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": False, "itemIds": [str(it2.id)]},
    )
    assert res2.status_code == 202, res2.text
    body2 = [p for (name, p) in stub_kaipoke.calls if name == "apply"][-1]
    assert body2["correction_data"][0]["user_name"] == "患者B"


@pytest.mark.asyncio
async def test_apply_week_scope_skips_past_days(client, db, stub_kaipoke) -> None:
    """過去日ガード: 週スコープの送信は過去日〜当日を除外 (実績保護)."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo

    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_user(db, "c2-pastguard@example.com", "admin")
    today = _dt.now(ZoneInfo("Asia/Tokyo")).date()
    week_start = today - _td(days=2)  # 過去2日〜未来4日を含む「週」
    past_day = str((today - _td(days=1)).day)
    future_day = str((today + _td(days=2)).day)
    sheet = CorrectionSheet(
        target_month="2026-07",
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
                before={"date": past_day, "user_name": "過去日патA"},
                after=None,
                include=True,
            ),
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="delete",
                before={"date": future_day, "user_name": "未来日患B"},
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
        json={"sheetId": str(sheet.id), "dryRun": True},
    )
    assert res.status_code == 202, res.text
    body = [p for (name, p) in stub_kaipoke.calls if name == "apply"][-1]
    # 過去日は除外され未来日 1 件だけが送られる
    assert len(body["correction_data"]) == 1
    assert body["correction_data"][0]["user_name"] == "未来日患B"


@pytest.mark.asyncio
async def test_apply_week_scope_all_past_returns_422(client, db, stub_kaipoke) -> None:
    """過去日のみ選択 → 422 (明確なメッセージで拒否)."""
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    from zoneinfo import ZoneInfo

    from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem

    admin = await _make_user(db, "c2-pastonly@example.com", "admin")
    today = _dt.now(ZoneInfo("Asia/Tokyo")).date()
    week_start = today - _td(days=3)
    sheet = CorrectionSheet(
        target_month="2026-07",
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
            before={"date": str((today - _td(days=1)).day), "user_name": "過去のみ"},
            after=None,
            include=True,
        )
    )
    await db.commit()

    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": str(sheet.id), "dryRun": True},
    )
    assert res.status_code == 422, res.text
    assert "過去日" in res.text


# --- 5. correction items PATCH ---------------------------------------------


@pytest.mark.asyncio
async def test_patch_correction_item_updates_include(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-patch@example.com", "admin")
    stub_kaipoke.responses["diff"] = {
        "items": [
            {"action": "update", "before": {"a": 1}, "after": {"a": 2}},
        ],
    }
    diff_res = await client.post(
        "/api/v1/integrations/diff",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    sheet_id = diff_res.json()["sheetId"]
    items_res = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet_id}/items",
        headers=_bearer(admin),
    )
    item_id = items_res.json()["items"][0]["id"]

    res = await client.patch(
        f"/api/v1/integrations/correction-items/{item_id}",
        headers=_bearer(admin),
        json={"include": False, "comment": "skip"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["include"] is False
    assert body["comment"] == "skip"


# --- 6. jobs alias --------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_listing_alias_returns_paginated(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-jobs@example.com", "admin")
    stub_kaipoke.responses["expand"] = {"jobId": "kp-list-1"}
    await client.post(
        "/api/v1/integrations/expand",
        headers=_bearer(admin),
        json={"month": "2026-05"},
    )

    res = await client.get(
        "/api/v1/integrations/jobs?limit=5",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    assert body["limit"] == 5
    assert isinstance(body["items"], list)


# --- 6b. live snapshot (monitor) ------------------------------------------


@pytest.mark.asyncio
async def test_live_running_apply_exposes_progress(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-live-run@example.com", "admin")
    stub_kaipoke.responses["status"] = {
        "current_task": {"running": True, "command": "apply"},
    }
    stub_kaipoke.responses["apply_result"] = {
        "status": "running",
        "progress": {"processed": 12, "total": 40, "current_name": "山田 太郎"},
    }
    stub_kaipoke.responses["logs"] = {"lines": ["10:00:00 apply 開始"], "total": 1}

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reachable"] is True
    assert body["running"] is True
    assert body["command"] == "apply"
    assert body["processed"] == 12
    assert body["total"] == 40
    assert body["currentName"] == "山田 太郎"
    assert body["monitorUrl"].endswith("/vnc.html")
    assert body["logs"] == ["10:00:00 apply 開始"]


@pytest.mark.asyncio
async def test_live_idle_reconciles_running_job_to_completed(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-live-idle@example.com", "admin")
    # Seed a running export job via the relay.
    stub_kaipoke.responses["export"] = {"ok": True, "async": True}
    await client.post(
        "/api/v1/integrations/export",
        headers=_bearer(admin),
        json={"month": "2026-07", "format": "csv"},
    )

    # Worker now idle and export result completed → job should settle.
    stub_kaipoke.responses["status"] = {"current_task": {"running": False, "command": None}}
    stub_kaipoke.responses["apply_result"] = {"status": "no_result"}
    stub_kaipoke.responses["export_result"] = {
        "status": "completed",
        "result": {"success": True, "row_count": 578, "csv_content": "SECRET,rows"},
    }
    stub_kaipoke.responses["logs"] = {"lines": [], "total": 0}

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["running"] is False
    assert body["latestJob"]["status"] == "completed"
    # csv_content must be stripped from the persisted summary.
    summary = body["latestJob"]["result_summary"]
    assert "csv_content" not in summary.get("result", {})
    assert summary["result"]["row_count"] == 578


@pytest.mark.asyncio
async def test_live_unreachable_returns_reachable_false(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave4-live-down@example.com", "admin")
    stub_kaipoke.errors["status"] = kc_module.KaipokeApiError(502, {"error": "network"})

    res = await client.get("/api/v1/integrations/live", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["reachable"] is False
    assert body["error"]


@pytest.mark.asyncio
async def test_live_requires_admin(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave4-live-mgr@example.com", "manager")
    res = await client.get("/api/v1/integrations/live", headers=_bearer(manager))
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_reconcile_jobs_unreachable(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-recon@example.com", "admin")
    stub_kaipoke.errors["status"] = kc_module.KaipokeApiError(502, {"error": "network"})
    res = await client.post("/api/v1/integrations/reconcile-jobs", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["reachable"] is False


@pytest.mark.asyncio
async def test_reconcile_jobs_requires_admin(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave-recon-mgr@example.com", "manager")
    res = await client.post("/api/v1/integrations/reconcile-jobs", headers=_bearer(manager))
    assert res.status_code == 403, res.text


# --- 6d. local diff (K-2b) ------------------------------------------------

# 18列カイポケCSV (ヘッダ + 1行)。現況として stub export が返す。
_KAIPOKE_18COL_HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)


@pytest.mark.asyncio
async def test_diff_local_builds_sheet_from_current_vs_generated(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-difflocal@example.com", "admin")
    # 現況(kaipoke): 山田 太郎 が 09:00-09:35 で入っている。
    current = (
        _KAIPOKE_18COL_HEADER
        + "\n"
        + "看護A,看護師,,,,,,,よりより,1,水,山田　太郎,医療保険,精神基本療養費Ⅰ・正看,"
        + "09:00,09:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": current}}

    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["sheetId"]
    # CareFlow 側 visits は空 → 現況の1件は delete 差分になるはず。
    assert body["summary"]["total"] >= 1
    assert body["summary"]["delete"] >= 1
    # 山田太郎 は patient マスタに無い → 未解決としてカウント。
    assert body["summary"]["unresolved_patient"] >= 1


@pytest.mark.asyncio
async def test_diff_local_week_scope_excludes_other_weeks(client, db, stub_kaipoke) -> None:
    """週スコープ: 対象週外のカイポケ既存予定を delete 差分にしない (核心の安全保証)。"""
    admin = await _make_user(db, "wave-difflocal-week@example.com", "admin")
    # 現況: 1日(週A・対象外) と 8日(週B・対象) に1件ずつ。CareFlow visits は空。
    current = (
        _KAIPOKE_18COL_HEADER
        + "\n"
        + "看護A,看護師,,,,,,,よりより,1,水,患者A,医療保険,精神基本療養費Ⅰ・正看,"
        + "09:00,09:35,35,\n"
        + "看護B,看護師,,,,,,,よりより,8,水,患者B,医療保険,精神基本療養費Ⅰ・正看,"
        + "10:00,10:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": current}}
    # 対象週 = 7/6(月)〜7/12(日) → 日 6..12。8日は含む、1日は含まない。
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07", "weekStart": "2026-07-06"},
    )
    assert res.status_code == 202, res.text
    summary = res.json()["summary"]
    # 8日の1件のみ delete。1日(週外)は触らない → total=1。
    assert summary["total"] == 1
    assert summary["delete"] == 1


@pytest.mark.asyncio
async def test_diff_local_before_carries_user_name(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-difflocal-un@example.com", "admin")
    current = (
        _KAIPOKE_18COL_HEADER
        + "\n"
        + "看護A,看護師,,,,,,,よりより,3,金,佐藤　花子,医療保険,精神基本療養費Ⅰ・正看,"
        + "13:00,13:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": current}}
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    assert res.status_code == 202, res.text
    sheet_id = res.json()["sheetId"]
    items = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet_id}/items",
        headers=_bearer(admin),
    )
    # patient 未解決でも before に利用者名が残る (可視化の完全性)。
    first = items.json()["items"][0]
    assert first["before"]["user_name"] == "佐藤　花子"


@pytest.mark.asyncio
async def test_diff_local_office_filter_excludes_other_office(client, db, stub_kaipoke) -> None:
    from app.models.office import Office

    admin = await _make_user(db, "wave-difflocal-off@example.com", "admin")
    office = Office(name="稲毛", code="INAGE", kaipoke_name="よりより本店")
    db.add(office)
    await db.commit()
    await db.refresh(office)

    # 現況に2拠点の行。office_id=INAGE を指定 → 都賀支店の行は差分対象外。
    current = (
        _KAIPOKE_18COL_HEADER
        + "\n"
        + "看護A,看護師,,,,,,,よりより本店,1,水,患者甲,医療保険,精神基本療養費Ⅰ・正看,"
        + "09:00,09:35,35,\n"
        + "看護B,看護師,,,,,,,都賀支店,1,水,患者乙,医療保険,精神基本療養費Ⅰ・正看,"
        + "10:00,10:35,35,\n"
    )
    stub_kaipoke.responses["export"] = {"result": {"csv_content": current}}
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07", "officeId": str(office.id)},
    )
    assert res.status_code == 202, res.text
    # 本店の1件のみ delete 差分 (都賀支店は現況フィルタで除外)。
    assert res.json()["summary"]["total"] == 1


@pytest.mark.asyncio
async def test_diff_local_requires_admin(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave-difflocal-mgr@example.com", "manager")
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(manager),
        json={"month": "2026-07"},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_diff_local_upstream_error_returns_502(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-difflocal-err@example.com", "admin")
    stub_kaipoke.errors["export"] = kc_module.KaipokeApiError(500, {"err": "boom"})
    res = await client.post(
        "/api/v1/integrations/diff-local",
        headers=_bearer(admin),
        json={"month": "2026-07"},
    )
    assert res.status_code == 502, res.text


# --- 6c. generated CSV (K-2a) ---------------------------------------------


@pytest.mark.asyncio
async def test_generated_csv_empty_month_returns_header_only(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-gencsv@example.com", "admin")
    res = await client.get(
        "/api/v1/integrations/generated-csv?month=2026-07",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-07"
    assert body["rowCount"] == 0  # visits 無し → ヘッダーのみ
    assert body["csvContent"].startswith("職員名")


@pytest.mark.asyncio
async def test_generated_csv_requires_admin(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave-gencsv-mgr@example.com", "manager")
    res = await client.get(
        "/api/v1/integrations/generated-csv?month=2026-07",
        headers=_bearer(manager),
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_generated_csv_rejects_bad_month(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "wave-gencsv-bad@example.com", "admin")
    res = await client.get(
        "/api/v1/integrations/generated-csv?month=2026-7",
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


# --- 7. RBAC: anonymous on every relay endpoint ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/v1/integrations/status", None),
        ("GET", "/api/v1/integrations/live", None),
        ("GET", "/api/v1/integrations/monitor-url", None),
        ("GET", "/api/v1/integrations/generated-csv?month=2026-07", None),
        ("POST", "/api/v1/integrations/diff-local", {"month": "2026-07"}),
        ("POST", "/api/v1/integrations/expand", {"month": "2026-05"}),
        ("POST", "/api/v1/integrations/diff", {"month": "2026-05"}),
    ],
)
async def test_relay_endpoints_require_auth(client, method, path, body) -> None:
    if method == "GET":
        res = await client.get(path)
    else:
        res = await client.post(path, json=body)
    assert res.status_code == 401, res.text


# ---------------------------------------------------------------------------
# week-schedule: 担当は visit_staff_assignments / courses.assigned_staff_id が正典。
# visits.primary_staff_id が未同期(NULL)でも予定を落とさない (実データ W28 の再現)。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_week_schedule_shows_visits_without_legacy_primary_staff(
    client, db, stub_kaipoke
) -> None:
    """primary_staff_id が NULL でも、割当(visit_staff_assignments)/コース担当から解決して表示する。

    回帰: 旧実装は `v.primary_staff_id is None` の visit を全スキップしていたため、
    自動割当済みでも週の予定がほぼ空になっていた (W28 で 127 件中 1 件しか出ない)。
    """
    from datetime import date, time

    from app.models.course import Course
    from app.models.office import Office
    from app.models.patient import Patient
    from app.models.staff import Staff
    from app.models.visit import VISIT_STATUS_PLANNED, Visit
    from app.models.visit_staff_assignment import VisitStaffAssignment

    admin = await _make_user(db, "wave-weeksched@example.com", "admin")

    office = Office(name="稲毛")
    db.add(office)
    await db.flush()
    staff = Staff(name="担当 花子", role="staff", is_trainee=False, primary_office_id=office.id)
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="W28-1",
        name="患者 一郎",
        status="active",
        lat=35.6,
        lng=140.1,
        primary_office_id=office.id,
        sex="female",
    )
    db.add(patient)
    await db.flush()
    # 対象週 = 2026-07-06(月)〜07-12(日)。visit は 07-08(水)。
    course = Course(
        iso_year=2026,
        iso_week=28,
        weekday=2,
        code="A",
        course_status="staff_assigned",
        assigned_staff_id=staff.id,
        office_id=office.id,
    )
    db.add(course)
    await db.flush()
    visit = Visit(
        patient_id=patient.id,
        course_id=course.id,
        visit_date=date(2026, 7, 8),
        start_time=time(9, 0),
        end_time=time(9, 40),
        type="regular",
        status=VISIT_STATUS_PLANNED,
        source="auto_alloc",
        required_staff_count=1,
        # primary_staff_id は敢えて未設定 (レガシー欄が NULL = W28 の状態)。
    )
    db.add(visit)
    await db.flush()
    db.add(VisitStaffAssignment(visit_id=visit.id, staff_id=staff.id))
    await db.commit()

    res = await client.get(
        "/api/v1/integrations/week-schedule?weekStart=2026-07-06&weekEnd=2026-07-12",
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["rows"]) == 1, body
    row = body["rows"][0]
    # レスポンスは alias (camelCase) でシリアライズされる。
    assert row["patientName"] == "患者 一郎"
    # 週ビューのカード意匠統一 (性別ウォッシュ) 用に patient.sex を additive で載せる。
    assert row["patientSex"] == "female"
    assert row["courseCode"] == "A"
    # 担当は primary_staff_id ではなく割当/コース担当から解決される。
    assert row["staff1"] == "担当 花子"
    assert row["startTime"] == "09:00"


@pytest.mark.asyncio
async def test_week_schedule_requires_admin(client, db, stub_kaipoke) -> None:
    manager = await _make_user(db, "wave-weeksched-mgr@example.com", "manager")
    res = await client.get(
        "/api/v1/integrations/week-schedule?weekStart=2026-07-06&weekEnd=2026-07-12",
        headers=_bearer(manager),
    )
    assert res.status_code == 403, res.text
