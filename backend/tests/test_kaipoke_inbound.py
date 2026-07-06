"""カイポケ → CareFlow 逆反映 (diff-inbound / apply-inbound) のテスト — R-1/R-2.

docs/plans/kaipoke-reverse-sync-design.md:
  * apply実績ゲート (実apply した週だけ取り込み可)
  * inbound 差分 (before=CareFlow / after=カイポケ現況) と visit_id 解決
  * dry-run は無書込・実適用は cancelled / manual_week / note 刻印
  * days (曜日チップ) フィルタ・applied 再適用 409・方向ガード
"""

from __future__ import annotations

from datetime import date, time
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.correction_sheet import CorrectionSheet
from app.models.kaipoke_job import KaipokeJob
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.csv_builder import KaipokeCsvRow, StaffCell, build_csv

# 対象週: 2026-07-06(月) 〜 2026-07-11(土)。
WEEK_START = date(2026, 7, 6)
MONTH = "2026-07"

PATIENT_NAME = "山田　花子"
STAFF_NAME = "田中　看護師"
SERVICE = "精神基本療養費Ⅰ・正看"


# --- stub / helpers ----------------------------------------------------------


class StubKaipokeClient:
    """export だけ差し替える最小スタブ。"""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover
        pass

    def _dispatch(self, name: str, payload: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        return self.responses.get(name, {})

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        return self._dispatch("export", payload)

    async def status(self) -> dict[str, Any]:
        return self._dispatch("status", None)


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
        email="inbound-admin@example.com",
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


async def _seed_week(db) -> dict[str, Any]:
    """office / staff / patient / 対象週の visits (火・水・木) を作る。"""
    office = Office(name="稲毛", code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-INB-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()

    def _visit(d: date, start: time, end: time) -> Visit:
        return Visit(
            patient_id=patient.id,
            visit_date=d,
            start_time=start,
            end_time=end,
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=staff.id,
        )

    visit_tue = _visit(date(2026, 7, 7), time(10, 0), time(10, 35))  # カイポケで 14:00 に変更
    visit_wed = _visit(date(2026, 7, 8), time(11, 0), time(11, 35))  # カイポケで削除
    visit_thu = _visit(date(2026, 7, 9), time(9, 0), time(9, 35))  # 変更なし
    db.add_all([visit_tue, visit_wed, visit_thu])
    await db.commit()
    for v in (visit_tue, visit_wed, visit_thu):
        await db.refresh(v)
    return {
        "office": office,
        "staff": staff,
        "patient": patient,
        "tue": visit_tue,
        "wed": visit_wed,
        "thu": visit_thu,
    }


async def _seed_real_apply(db, week_start: date = WEEK_START) -> None:
    """apply実績ゲートを通すための「実apply 完了」ジョブを作る。"""
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


def _kaipoke_csv(*rows: KaipokeCsvRow) -> str:
    """カイポケ現況CSV (export csv_content) を csv_builder で組み立てる。"""
    return build_csv(list(rows), encoding="utf-8-sig").decode("utf-8-sig")


def _kp_row(d: date, start: time, end: time) -> KaipokeCsvRow:
    return KaipokeCsvRow(
        patient_name=PATIENT_NAME,
        visit_date=d,
        start_time=start,
        end_time=end,
        office_name="稲毛",
        business_type="医療保険",
        service_content=SERVICE,
        primary=StaffCell(name=STAFF_NAME, qualification="看護師"),
    )


def _default_kaipoke_state() -> str:
    """カイポケ現況: 火曜は 14:00 に変更済み・水曜は削除済み・木曜は不変。"""
    return _kaipoke_csv(
        _kp_row(date(2026, 7, 7), time(14, 0), time(14, 35)),
        _kp_row(date(2026, 7, 9), time(9, 0), time(9, 35)),
    )


async def _run_diff_inbound(client, db, stub_kaipoke, admin) -> dict[str, Any]:
    stub_kaipoke.responses["export"] = {"result": {"csv_content": _default_kaipoke_state()}}
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 202, res.text
    return res.json()


# --- 1. apply実績ゲート ------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_inbound_blocked_without_real_apply(client, db, stub_kaipoke) -> None:
    await _seed_week(db)
    admin = await _make_admin(db)
    res = await client.post(
        "/api/v1/integrations/diff-inbound",
        headers=_bearer(admin),
        json={"month": MONTH, "weekStart": WEEK_START.isoformat()},
    )
    assert res.status_code == 422, res.text
    assert "反映されていません" in res.json()["detail"]


@pytest.mark.asyncio
async def test_inbound_eligibility_endpoint(client, db, stub_kaipoke) -> None:
    admin = await _make_admin(db)
    url = f"/api/v1/integrations/inbound-eligibility?weekStart={WEEK_START.isoformat()}"

    res = await client.get(url, headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["eligible"] is False

    await _seed_real_apply(db)
    res = await client.get(url, headers=_bearer(admin))
    assert res.json()["eligible"] is True


@pytest.mark.asyncio
async def test_dry_run_apply_job_does_not_open_gate(client, db, stub_kaipoke) -> None:
    """dry-run の apply 記録では取り込みを開放しない (実apply のみがバトンタッチ)。"""
    admin = await _make_admin(db)
    db.add(
        KaipokeJob(
            job_type="push",
            week_start=WEEK_START,
            params={
                "op": "apply",
                "sheet_id": "dummy",
                "dry_run": True,
                "week_start": WEEK_START.isoformat(),
            },
            status="completed",
        )
    )
    await db.commit()
    res = await client.get(
        f"/api/v1/integrations/inbound-eligibility?weekStart={WEEK_START.isoformat()}",
        headers=_bearer(admin),
    )
    assert res.json()["eligible"] is False


# --- 2. diff-inbound ----------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_inbound_creates_inbound_sheet(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)

    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)
    assert body["summary"]["edit"] == 1
    assert body["summary"]["delete"] == 1
    assert body["summary"].get("add", 0) == 0
    assert body["summary"]["auto_selected"] == 2

    sheet = await db.scalar(
        select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheetId"]))
    )
    assert sheet is not None
    assert sheet.direction == "inbound"
    assert sheet.week_start == WEEK_START
    assert sheet.week_end == date(2026, 7, 12)

    res = await client.get(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/items",
        headers=_bearer(admin),
    )
    items = res.json()["items"] if isinstance(res.json(), dict) else res.json()
    by_action = {it["action"]: it for it in items}
    # edit = 火曜の時刻変更 → visit_id まで解決され include=True。
    assert by_action["edit"]["visit_id"] == str(seeded["tue"].id)
    assert by_action["edit"]["include"] is True
    assert by_action["edit"]["after"]["start_time"] == "14:00"
    # delete = 水曜のカイポケ側削除。
    assert by_action["delete"]["visit_id"] == str(seeded["wed"].id)
    assert by_action["delete"]["include"] is True


# --- 3. apply-inbound ---------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_inbound_dry_run_then_real(client, db, stub_kaipoke) -> None:
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)
    sheet_id = body["sheetId"]

    # dry-run (既定): 予定される結果だけ返し、何も書き込まない。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id},
    )
    assert res.status_code == 200, res.text
    dry = res.json()
    assert dry["dryRun"] is True
    assert dry["cancelled"] == 1 and dry["updated"] == 1 and dry["failed"] == 0

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    assert seeded["tue"].start_time == time(10, 0)
    assert seeded["wed"].status == "planned"

    # 実適用: 火曜=時刻変更 (manual_week + note)・水曜=キャンセル。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": False},
    )
    assert res.status_code == 200, res.text
    real = res.json()
    assert real["cancelled"] == 1 and real["updated"] == 1 and real["failed"] == 0
    assert real["jobId"] is not None

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    await db.refresh(seeded["thu"])
    assert seeded["tue"].start_time == time(14, 0)
    assert seeded["tue"].end_time == time(14, 35)
    assert seeded["tue"].source == "manual_week"
    assert "カイポケ取込" in (seeded["tue"].note or "")
    assert seeded["wed"].status == "cancelled"
    assert "カイポケ取込" in (seeded["wed"].note or "")
    assert seeded["thu"].status == "planned"  # 変更なしの訪問は不変。
    assert seeded["thu"].note is None

    # 適用済みシートの再適用は 409。
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": sheet_id, "dryRun": False},
    )
    assert res.status_code == 409, res.text


@pytest.mark.asyncio
async def test_apply_inbound_day_filter(client, db, stub_kaipoke) -> None:
    """days (曜日チップ) 指定日は適用され、指定外は据え置かれる。"""
    seeded = await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)

    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": False, "days": ["2026-07-08"]},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["cancelled"] == 1 and out["updated"] == 0

    await db.refresh(seeded["tue"])
    await db.refresh(seeded["wed"])
    assert seeded["tue"].start_time == time(10, 0)  # 火曜は選択外 → 据え置き。
    assert seeded["wed"].status == "cancelled"


@pytest.mark.asyncio
async def test_direction_guards(client, db, stub_kaipoke) -> None:
    """inbound シートは /apply で拒否、outbound シートは /apply-inbound で拒否。"""
    await _seed_week(db)
    await _seed_real_apply(db)
    admin = await _make_admin(db)
    body = await _run_diff_inbound(client, db, stub_kaipoke, admin)

    res = await client.post(
        "/api/v1/integrations/apply",
        headers=_bearer(admin),
        json={"sheetId": body["sheetId"], "dryRun": True},
    )
    assert res.status_code == 422, res.text

    outbound = CorrectionSheet(target_month=MONTH, status="ready", direction="outbound")
    db.add(outbound)
    await db.commit()
    res = await client.post(
        "/api/v1/integrations/apply-inbound",
        headers=_bearer(admin),
        json={"sheetId": str(outbound.id)},
    )
    assert res.status_code == 422, res.text
