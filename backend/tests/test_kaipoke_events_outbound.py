"""イベント送信 (outbound・楽スケ→カイポケ・Phase 3) のテスト.

正典 = docs/plans/kaipoke-event-two-way-design.md §3-①/§7-b。

検証観点:
  1. preview: manual のみ対象 / 職員ID逆引き (未対応は送信不可) / 日曜は送信不可
  2. start→status: RPA async 起動 → completed 観測で昇格 (source='kaipoke'+external_id)
     / 既に同 key の kaipoke 行が居れば manual 行を削除 (重複解消) / failed 集計
     / 完了後の再ポーリングは RPA 非依存 (冪等)
  3. RBAC / busy 409 / 送信対象なし 422
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import Staff, User
from app.models.staff import StaffEvent
from app.services import kaipoke_client as kc_module

WEEK = date(2026, 9, 7)  # 月曜
PREVIEW_URL = "/api/v1/integrations/events-outbound-preview"
START_URL = "/api/v1/integrations/events-outbound-apply/start"


def _status_url(job_id: str) -> str:
    return f"/api/v1/integrations/events-outbound-apply/status/{job_id}"


class StubKaipokeClient:
    """individual_tasks_apply / result だけ差し替える最小スタブ。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.busy = False
        self.async_job_id: str | None = None
        self.result_responses: list[dict[str, Any]] = []
        self.result_calls = 0
        # 既定の完了応答: items をそのまま added として返す (外部キーは連番)
        self.outcome_for: dict[str, dict[str, Any]] = {}

    async def aclose(self) -> None:  # pragma: no cover
        pass

    async def individual_tasks_apply(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        if self.busy:
            from app.services.kaipoke_client import KaipokeBusyError

            raise KaipokeBusyError({"error": "busy"})
        self.async_job_id = str(payload.get("job_id") or "") or None
        return {"success": True, "async": True, "job_id": self.async_job_id}

    def _default_result(self) -> dict[str, Any]:
        items = self.calls[-1]["items"] if self.calls else []
        results = []
        for n, it in enumerate(items, start=1):
            override = self.outcome_for.get(it["external_ref"])
            if override:
                results.append({"external_ref": it["external_ref"], **override})
            else:
                results.append(
                    {
                        "external_ref": it["external_ref"],
                        "outcome": "added",
                        "external_key": f"{900000 + n}:{it['staff_internal_id']}:{it['date']}",
                    }
                )
        ok = sum(1 for r in results if r["outcome"] != "failed")
        return {
            "success": True,
            "status": "completed",
            "job_id": self.async_job_id,
            "result": {"success": True, "total": len(results), "ok": ok, "results": results},
        }

    async def individual_tasks_apply_result(self) -> dict[str, Any]:
        self.result_calls += 1
        if self.result_responses:
            return self.result_responses.pop(0)
        return self._default_result()


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_user(db, email: str, role: str, staff_id=None) -> User:
    user = User(email=email, password_hash=hash_password("x"), role=role, staff_id=staff_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _make_staff(db, name: str, *, kaipoke_internal: str | None = None) -> Staff:
    s = Staff(name=name)
    db.add(s)
    await db.flush()
    if kaipoke_internal:
        # 取込済み行 = 職員内部IDの逆引き供給源
        db.add(
            StaffEvent(
                staff_id=s.id,
                event_type="event",
                starts_at=datetime(2026, 8, 3, 9, 0),
                ends_at=datetime(2026, 8, 3, 10, 0),
                title="過去取込",
                source="kaipoke",
                external_id=f"111:{kaipoke_internal}:2026-08-03",
            )
        )
    await db.commit()
    await db.refresh(s)
    return s


async def _manual_event(db, staff: Staff, d: date, start: str, end: str, title: str) -> StaffEvent:
    sh, sm = (int(x) for x in start.split(":"))
    eh, em = (int(x) for x in end.split(":"))
    ev = StaffEvent(
        staff_id=staff.id,
        event_type="event",
        starts_at=datetime(d.year, d.month, d.day, sh, sm),
        ends_at=datetime(d.year, d.month, d.day, eh, em),
        title=title,
        source="manual",
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev


# ---------------------------------------------------------------------------
# 1) preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_preview_classifies_items(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "eo-admin1@example.com", "admin")
    mapped = await _make_staff(db, "紐付 済子", kaipoke_internal="4601519")
    unmapped = await _make_staff(db, "未紐付 太郎")

    ev_ok = await _manual_event(db, mapped, date(2026, 9, 9), "09:00", "09:15", "朝会")
    await _manual_event(db, unmapped, date(2026, 9, 9), "10:00", "11:00", "面談")
    await _manual_event(db, mapped, date(2026, 9, 13), "09:00", "10:00", "日曜予定")

    res = await client.post(
        PREVIEW_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["sendableCount"] == 1
    by_title = {i["title"]: i for i in body["items"]}
    assert by_title["朝会"]["sendable"] is True
    assert by_title["朝会"]["eventId"] == str(ev_ok.id)
    assert by_title["面談"]["sendable"] is False
    assert "職員ID" in by_title["面談"]["reason"]
    assert by_title["日曜予定"]["sendable"] is False
    assert "日曜" in by_title["日曜予定"]["reason"]
    # kaipoke 由来行 (過去取込) は対象外
    assert "過去取込" not in by_title


# ---------------------------------------------------------------------------
# 2) start → status (昇格・重複解消・failed 集計・冪等)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_send_promotes_manual_rows(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "eo-admin2@example.com", "admin")
    staff = await _make_staff(db, "紐付 済子", kaipoke_internal="4601519")
    ev = await _manual_event(db, staff, date(2026, 9, 9), "09:00", "09:15", "朝会")

    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()})
    assert res.status_code == 202, res.text
    job_id = res.json()["jobId"]
    assert res.json()["count"] == 1
    # RPA へ async + 相関ID + items が渡っている
    assert stub_kaipoke.calls[-1]["async"] is True
    assert stub_kaipoke.calls[-1]["job_id"] == job_id
    assert stub_kaipoke.calls[-1]["items"][0]["external_ref"] == str(ev.id)

    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "completed"
    assert body["summary"]["promoted"] == 1
    assert body["summary"]["failed"] == 0

    await db.refresh(ev)
    assert ev.source == "kaipoke"
    assert ev.external_id is not None and ev.external_id.endswith(":4601519:2026-09-09")

    # 完了後の再ポーリングは RPA を見ない (冪等)
    calls_before = stub_kaipoke.result_calls
    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.json()["status"] == "completed"
    assert stub_kaipoke.result_calls == calls_before


@pytest.mark.asyncio
async def test_outbound_dedupes_when_kaipoke_row_exists(client, db, stub_kaipoke) -> None:
    """同 key の kaipoke 行が既に居る (過去の取込) → manual 行を削除して重複解消。"""
    admin = await _make_user(db, "eo-admin3@example.com", "admin")
    staff = await _make_staff(db, "紐付 済子", kaipoke_internal="4601519")
    ev = await _manual_event(db, staff, date(2026, 9, 9), "09:00", "09:15", "朝会")

    key = f"777:{4601519}:2026-09-09"
    db.add(
        StaffEvent(
            staff_id=staff.id,
            event_type="event",
            starts_at=datetime(2026, 9, 9, 9, 0),
            ends_at=datetime(2026, 9, 9, 9, 15),
            title="朝会",
            source="kaipoke",
            external_id=key,
        )
    )
    await db.commit()

    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()})
    job_id = res.json()["jobId"]
    # RPA は「カイポケ側に既にある」= skipped_duplicate + 既存 key を返すシナリオ
    stub_kaipoke.outcome_for[str(ev.id)] = {
        "outcome": "skipped_duplicate",
        "external_key": key,
    }

    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["summary"]["deduped"] == 1

    gone = await db.scalar(select(StaffEvent).where(StaffEvent.id == ev.id))
    assert gone is None  # manual 行は重複として削除された


@pytest.mark.asyncio
async def test_outbound_failed_item_is_counted(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "eo-admin4@example.com", "admin")
    staff = await _make_staff(db, "紐付 済子", kaipoke_internal="4601519")
    ev = await _manual_event(db, staff, date(2026, 9, 9), "09:00", "09:15", "朝会")

    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()})
    job_id = res.json()["jobId"]
    stub_kaipoke.outcome_for[str(ev.id)] = {"outcome": "failed", "error": "popup error"}

    res = await client.get(_status_url(job_id), headers=_bearer(admin))
    assert res.json()["summary"]["failed"] == 1
    await db.refresh(ev)
    assert ev.source == "manual"  # 失敗行は昇格されない


# ---------------------------------------------------------------------------
# 3) ガード
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_start_without_sendable_is_422(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "eo-admin5@example.com", "admin")
    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()})
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_outbound_start_busy_is_409(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "eo-admin6@example.com", "admin")
    staff = await _make_staff(db, "紐付 済子", kaipoke_internal="4601519")
    await _manual_event(db, staff, date(2026, 9, 9), "09:00", "09:15", "朝会")
    stub_kaipoke.busy = True
    res = await client.post(START_URL, headers=_bearer(admin), json={"weekStart": WEEK.isoformat()})
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_outbound_preview_staff_role_403(client, db, stub_kaipoke) -> None:
    staff = await _make_staff(db, "一般 職員")
    user = await _make_user(db, "eo-staff@example.com", "staff", staff_id=staff.id)
    res = await client.post(
        PREVIEW_URL, headers=_bearer(user), json={"weekStart": WEEK.isoformat()}
    )
    assert res.status_code == 403, res.text
