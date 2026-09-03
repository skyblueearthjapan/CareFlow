"""●未送信サマリ と ⇧上書き(反転シート) のテスト — week-cockpit Phase E (BE-2).

正典 = docs/plans/week-cockpit-design.md §2-4 / §2-5。

守りたい性質:
  * ``POST /integrations/unsent-summary`` は **RPA (kaipoke client) を一切呼ばない**
    (受け入れ基準)。保存済み現況CSV (kaipoke_csv_snapshots) だけで計算する。
  * 保存CSVが無ければ snapshot=null / items=[] (FE は「🔄突合してください」)。
  * **月をまたぐ週はフェイルクローズ** — 現況CSVは月単位・日付列は「日」だけなので
    片月しか見えず、もう片月が丸ごと偽の未送信(add)に化ける。出さずに warnings。
  * 過去日 (JST当日以前) は送信対象外 = past_count 側に数える (apply の
    自動スキップと同じ ``resolve_item_date`` / ``jst_today`` を共有)。
  * 未送信は毎回再計算されるが、**送信の記録を持つシートは掃除しない**。
  * 送信が決着したら保存CSVを捨てる (送った変更が再び未送信に見えない)。
  * 反転シートは before/after を入替え add↔delete を反転した outbound になる。

日付は JST の「今週/来週」から動的に組む (固定日を書くと時間の経過で
過去日判定がひっくり返るため — KaipokeReconcilePanel テストと同じ作法)。
月跨ぎ週を踏まないよう、週の始端と終端が同月になる月曜を選ぶ。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.correction_sheet import CorrectionSheet, CorrectionSheetItem
from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff, StaffEvent
from app.models.visit import Visit
from app.services import kaipoke_client as kc_module
from app.services.kaipoke.csv_builder import HEADER
from app.services.kaipoke.csv_snapshot import get_latest, save_snapshot
from app.services.kaipoke.local_diff import export_current_week_csv, item_to_kaipoke_correction

UNSENT_URL = "/api/v1/integrations/unsent-summary"

PATIENT_NAME = "山田　花子"
STAFF_NAME = "田中　看護師"
OFFICE_NAME = "稲毛"


# --- 日付ヘルパー -----------------------------------------------------------


def _jst_today() -> date:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def _this_monday() -> date:
    t = _jst_today()
    return t - timedelta(days=t.weekday())


def _same_month_week(monday: date, *, forward: bool) -> date:
    """月をまたがない週の月曜へずらす (またぐ週はフェイルクローズ対象のため)。"""
    step = timedelta(days=7 if forward else -7)
    while (monday + timedelta(days=6)).month != monday.month:
        monday += step
    return monday


def _future_monday() -> date:
    """必ず未来かつ月をまたがない週の月曜。"""
    return _same_month_week(_this_monday() + timedelta(days=7), forward=True)


def _past_monday() -> date:
    """必ず当日以前かつ月をまたがない週の月曜 (今週の月曜は常に当日以前)。"""
    return _same_month_week(_this_monday(), forward=False)


def _crossing_monday() -> date:
    """月をまたぐ未来週の月曜 (フェイルクローズ検証用)。"""
    monday = _this_monday() + timedelta(days=7)
    while (monday + timedelta(days=6)).month == monday.month:
        monday += timedelta(days=7)
    return monday


def _month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _empty_kaipoke_csv() -> str:
    """カイポケ側に 1 件も無い現況CSV (ヘッダーのみ)。

    らく助の訪問はすべて「カイポケに無い」= action='add' の未送信になる。
    """
    return ",".join(HEADER) + "\r\n"


# --- stub / auth ------------------------------------------------------------


class StubKaipokeClient:
    """呼ばれたら記録するだけのスタブ。unsent-summary では calls が空であること。"""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover — interface stub
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

    async def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._dispatch("apply", payload)


@pytest.fixture
def stub_kaipoke():
    stub = StubKaipokeClient()
    kc_module.set_test_client(stub)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        kc_module.set_test_client(None)


async def _make_user(db, email: str, role: str = "admin") -> User:
    user = User(email=email, password_hash=hash_password("does-not-matter-here"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


# --- seed -------------------------------------------------------------------


async def _seed_master(db) -> dict[str, Any]:
    office = Office(name=OFFICE_NAME, code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-UNSENT-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.commit()
    return {"office": office, "staff": staff, "patient": patient}


async def _add_visit(db, seeded, d: date, start: time = time(10, 0)) -> Visit:
    v = Visit(
        patient_id=seeded["patient"].id,
        visit_date=d,
        start_time=start,
        end_time=time(start.hour, 35),
        type="regular",
        status="planned",
        source="auto",
        required_staff_count=1,
        primary_staff_id=seeded["staff"].id,
    )
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def _seed_staff_internal_id(db, staff_id) -> None:
    """カイポケ職員内部IDの逆引き元 (取込済み行) を作る。

    ``build_outbound_plan`` はこれが無いと sendable=False にする
    (= 未送信イベントとして数えない)。
    """
    db.add(
        StaffEvent(
            staff_id=staff_id,
            event_type="event",
            starts_at=datetime(2020, 1, 6, 9, 0),
            ends_at=datetime(2020, 1, 6, 9, 30),
            title="過去の取込済み",
            source="kaipoke",
            external_id="9001:42:2020-01-06",
        )
    )
    await db.commit()


async def _save_empty_snapshot(db, week_start: date, *, week_scoped: bool = False) -> None:
    """「カイポケ側は空」という現況を保存。"""
    await save_snapshot(
        db,
        office_id=None,
        month=_month_of(week_start),
        week_start=week_start if week_scoped else None,
        csv_text=_empty_kaipoke_csv(),
        source_op="test",
    )
    await db.commit()


# --- 1. snapshot が無いとき --------------------------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_without_snapshot_returns_null(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "unsent-a@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot"] is None
    assert body["items"] == []
    assert body["sheet_id"] is None
    assert body["warnings"] == []
    # 何より重要: RPA には触れていない。
    assert stub_kaipoke.calls == []


# --- 2. snapshot があるとき (RPA 未呼出) -------------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_builds_outbound_sheet_without_rpa(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "unsent-b@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    visit = await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert stub_kaipoke.calls == [], "unsent-summary は RPA を呼んではならない"
    assert body["snapshot"] is not None
    assert body["snapshot"]["month"] == _month_of(week_start)
    assert body["snapshot"]["row_count"] == 0  # ヘッダーのみ = カイポケ側0件
    assert body["sheet_id"] is not None

    items = body["items"]
    assert len(items) == 1
    assert items[0]["action"] == "add"
    # 実日付が付く (CSVの日付列は「日」しか持たないので週から復元する)
    assert items[0]["date_iso"] == visit.visit_date.isoformat()
    assert items[0]["patient_id"] == str(seeded["patient"].id)
    assert body["sendable_count"] == 1
    assert body["past_count"] == 0

    sheet = await db.scalar(
        select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheet_id"]))
    )
    assert sheet is not None
    assert sheet.direction == "outbound"
    assert sheet.origin == "cached"
    assert sheet.week_start == week_start


# --- 3. 過去日 / 月跨ぎ ------------------------------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_counts_past_days_as_not_sendable(client, db, stub_kaipoke) -> None:
    """今週の月曜は必ず「当日以前」= 送信対象外 (apply の自動スキップと同基準)。"""
    admin = await _make_user(db, "unsent-c@example.com")
    seeded = await _seed_master(db)
    week_start = _past_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert stub_kaipoke.calls == []
    assert len(body["items"]) == 1
    assert body["items"][0]["date_iso"] == week_start.isoformat()
    assert body["past_count"] == 1
    assert body["sendable_count"] == 0


@pytest.mark.asyncio
async def test_unsent_summary_fails_closed_on_month_crossing_week(client, db, stub_kaipoke) -> None:
    """月跨ぎ週は「判定できません」に倒す (片月しか見えず偽の未送信が出るため)。"""
    admin = await _make_user(db, "unsent-cross@example.com")
    seeded = await _seed_master(db)
    week_start = _crossing_monday()
    await _add_visit(db, seeded, week_start)
    # 両月ぶん保存しておいても出さない (安全側に倒すことの確認)
    await _save_empty_snapshot(db, week_start)
    await _save_empty_snapshot(db, week_start + timedelta(days=6))
    await _seed_staff_internal_id(db, seeded["staff"].id)
    db.add(
        StaffEvent(
            staff_id=seeded["staff"].id,
            event_type="event",
            starts_at=datetime.combine(week_start, time(9, 0)),
            ends_at=datetime.combine(week_start, time(9, 30)),
            title="打合せ",
            source="manual",
        )
    )
    await db.commit()

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["snapshot"] is None
    assert body["items"] == []
    assert body["sheet_id"] is None
    assert len(body["warnings"]) == 1
    assert "月をまたぐ" in body["warnings"][0]
    # イベントは現況CSVに依らないので出し続ける
    assert [e["title"] for e in body["events"]] == ["打合せ"]
    assert stub_kaipoke.calls == []


@pytest.mark.asyncio
async def test_unsent_summary_rejects_non_monday(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "unsent-tue@example.com")
    res = await client.post(
        UNSENT_URL,
        json={"week_start": (_future_monday() + timedelta(days=1)).isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text


def test_resolve_item_date_returns_none_when_day_is_ambiguous() -> None:
    """週内で同じ「日」が複数に解決し得るなら、当てずっぽうで決めない。"""
    from app.api.v1.integrations import resolve_item_date

    week_start = _future_monday()
    item = {"date": str(week_start.day)}
    assert resolve_item_date("add", None, item, week_start) == week_start
    # 週外の日 / 数字でない日は解決しない
    assert resolve_item_date("add", None, {"date": ""}, week_start) is None
    assert resolve_item_date("add", None, item, None) is None


# --- 4. cached シートの掃除 --------------------------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_keeps_one_cached_sheet_per_week(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "unsent-d@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    first = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    second = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["sheet_id"] != second.json()["sheet_id"]

    sheets = (
        await db.scalars(
            select(CorrectionSheet).where(
                CorrectionSheet.origin == "cached", CorrectionSheet.week_start == week_start
            )
        )
    ).all()
    assert len(sheets) == 1
    assert str(sheets[0].id) == second.json()["sheet_id"]
    # 旧シートの item も道連れで消えている (孤児を残さない)
    orphans = (
        await db.scalars(
            select(CorrectionSheetItem).where(CorrectionSheetItem.sheet_id != sheets[0].id)
        )
    ).all()
    assert orphans == []


@pytest.mark.asyncio
async def test_unsent_summary_cleanup_preserves_sent_sheets(client, db, stub_kaipoke) -> None:
    """送信の記録を持つシートは掃除しない (applied / partial / 一部送信済み)。"""
    admin = await _make_user(db, "unsent-keep@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    def _cached(status_: str) -> CorrectionSheet:
        return CorrectionSheet(
            target_month=_month_of(week_start),
            status=status_,
            direction="outbound",
            origin="cached",
            week_start=week_start,
            week_end=week_start + timedelta(days=6),
            created_by_user_id=admin.id,
        )

    applied, partial, part_sent, plain = (
        _cached("applied"),
        _cached("partial"),
        _cached("ready"),
        _cached("ready"),
    )
    db.add_all([applied, partial, part_sent, plain])
    await db.flush()
    # part_sent は「1件だけ送った」証跡として include=False の item を持つ
    db.add(
        CorrectionSheetItem(
            sheet_id=part_sent.id, action="add", before=None, after=None, include=False
        )
    )
    db.add(
        CorrectionSheetItem(sheet_id=plain.id, action="add", before=None, after=None, include=True)
    )
    await db.commit()
    kept_ids = {applied.id, partial.id, part_sent.id}
    plain_id = plain.id

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text

    surviving = {
        sh.id
        for sh in (
            await db.scalars(
                select(CorrectionSheet).where(CorrectionSheet.week_start == week_start)
            )
        ).all()
    }
    assert kept_ids <= surviving
    assert plain_id not in surviving  # 送信記録の無い cached だけが消える
    assert UUID(res.json()["sheet_id"]) in surviving


# --- 5. イベント (現況CSVに依らない未送信) -----------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_lists_unsent_events_only(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "unsent-e@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    staff_id = seeded["staff"].id
    await _seed_staff_internal_id(db, staff_id)

    def _ev(day_offset: int, source: str, external_id: str | None, title: str) -> StaffEvent:
        d = week_start + timedelta(days=day_offset)
        return StaffEvent(
            staff_id=staff_id,
            event_type="event",
            starts_at=datetime.combine(d, time(9, 0)),
            ends_at=datetime.combine(d, time(9, 30)),
            title=title,
            source=source,
            external_id=external_id,
        )

    db.add_all(
        [
            _ev(0, "manual", None, "打合せ"),
            _ev(1, "fixed", None, "朝会"),
            _ev(2, "kaipoke", "1:42:2026-01-01", "送信済み"),
            StaffEvent(
                staff_id=staff_id,
                event_type="event",
                starts_at=datetime.combine(week_start + timedelta(days=8), time(9, 0)),
                ends_at=datetime.combine(week_start + timedelta(days=8), time(9, 30)),
                title="来週以降",
                source="manual",
            ),
        ]
    )
    await db.commit()

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert sorted(e["title"] for e in body["events"]) == ["打合せ", "朝会"]
    assert body["events"][0]["staff_name"] == STAFF_NAME
    assert body["events"][0]["kind"] == "add"
    assert body["events"][0]["start_time"] == "09:00"
    # snapshot が無くてもイベントは数えられる (RPA 不要な判定のため)
    assert body["snapshot"] is None
    assert body["sendable_count"] == 2
    assert stub_kaipoke.calls == []


@pytest.mark.asyncio
async def test_unsent_summary_excludes_cancelled_events(client, db, stub_kaipoke) -> None:
    """「今週だけ外す」(mig 0075) 済みは送らない = 未送信にも数えない。"""
    admin = await _make_user(db, "unsent-f@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _seed_staff_internal_id(db, seeded["staff"].id)
    db.add(
        StaffEvent(
            staff_id=seeded["staff"].id,
            event_type="event",
            starts_at=datetime.combine(week_start, time(9, 0)),
            ends_at=datetime.combine(week_start, time(9, 30)),
            title="今週だけ外した朝会",
            source="fixed",
            cancelled_at=datetime.now(ZoneInfo("Asia/Tokyo")),
        )
    )
    await db.commit()

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    assert res.json()["events"] == []


# --- 6. RBAC ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsent_summary_requires_admin(client, db, stub_kaipoke) -> None:
    staff_user = await _make_user(db, "unsent-staff@example.com", role="staff")
    res = await client.post(
        UNSENT_URL, json={"week_start": _future_monday().isoformat()}, headers=_bearer(staff_user)
    )
    assert res.status_code == 403, res.text


# --- 7. スナップショットの upsert / 保存経路 --------------------------------


@pytest.mark.asyncio
async def test_save_snapshot_replaces_same_key_but_keeps_week_scoped(db) -> None:
    """upsert キーは (office_id, month, week_start) — 月CSVと週CSVは共存する。"""
    month = "2026-09"
    week = date(2026, 9, 7)
    first = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=None,
        csv_text=_empty_kaipoke_csv(),
        source_op="diff-local",
    )
    weekly = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=week,
        csv_text=_empty_kaipoke_csv(),
        source_op="smart-preview",
    )
    second = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=None,
        csv_text=_empty_kaipoke_csv() + "x\r\n",
        source_op="diff-inbound",
    )
    await db.commit()
    assert first is not None and weekly is not None and second is not None

    rows = (
        await db.scalars(select(KaipokeCsvSnapshot).where(KaipokeCsvSnapshot.month == month))
    ).all()
    # 月まるごとは置換され、週限定は生き残る
    assert {r.id for r in rows} == {weekly.id, second.id}


@pytest.mark.asyncio
async def test_get_latest_rejects_other_weeks_snapshot(db) -> None:
    """別の週の週マージCSVは現況として使わない (対象週が全 add に化けるため)。"""
    month = "2026-09"
    week_a, week_b = date(2026, 9, 7), date(2026, 9, 14)
    saved = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=week_a,
        csv_text=_empty_kaipoke_csv(),
        source_op="smart-preview",
    )
    await db.commit()
    assert saved is not None

    assert await get_latest(db, month=month, week_start=week_b) is None
    same = await get_latest(db, month=month, week_start=week_a)
    assert same is not None and same.id == saved.id
    # 月まるごとの行はどの週にも使える
    monthly = await save_snapshot(
        db,
        office_id=None,
        month=month,
        week_start=None,
        csv_text=_empty_kaipoke_csv(),
        source_op="diff-local",
    )
    await db.commit()
    assert monthly is not None
    picked = await get_latest(db, month=month, week_start=week_b)
    assert picked is not None and picked.id == monthly.id


@pytest.mark.asyncio
async def test_save_snapshot_ignores_empty_csv(db) -> None:
    """空CSVを「最後に見た姿」にすると、次の未送信計算が週全滅に化ける。"""
    assert (
        await save_snapshot(
            db, office_id=None, month="2026-09", week_start=None, csv_text="  ", source_op="test"
        )
        is None
    )
    assert await get_latest(db, month="2026-09") is None


@pytest.mark.asyncio
async def test_diff_local_persists_snapshot_then_unsent_summary_uses_it(
    client, db, stub_kaipoke
) -> None:
    """🔄突合 (diff-local) が現況CSVを保存 → 以後 ●未送信 が RPA 無しで出る。"""
    admin = await _make_user(db, "unsent-g@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    stub_kaipoke.responses["export"] = {"result": {"csv_content": _empty_kaipoke_csv()}}

    res = await client.post(
        "/api/v1/integrations/diff-local",
        json={"month": _month_of(week_start), "weekStart": week_start.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 202, res.text
    assert [c[0] for c in stub_kaipoke.calls] == ["export"]

    snap = await get_latest(db, month=_month_of(week_start))
    assert snap is not None
    assert snap.source_op == "diff-local"
    assert snap.week_start is None  # 月まるごとのexport = どの週にも使える

    stub_kaipoke.calls.clear()
    res2 = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res2.status_code == 200, res2.text
    assert stub_kaipoke.calls == []
    assert len(res2.json()["items"]) == 1


@pytest.mark.asyncio
async def test_export_current_week_csv_persists_week_scoped_snapshot(db, stub_kaipoke) -> None:
    """置換/smart 系の週マージ export も保存する (week_start 付き)。"""
    week_start = _future_monday()
    stub_kaipoke.responses["export"] = {
        "result": {"csv_content": _empty_kaipoke_csv() + "\r\n".join([""])}
    }
    # ヘッダーのみ = merged 0 行 → 保存しない
    out = await export_current_week_csv(
        kaipoke=stub_kaipoke, week_start=week_start, db=db, source_op="smart-preview"
    )
    await db.commit()
    assert out.strip() != ""
    assert await get_latest(db, month=_month_of(week_start)) is None

    # 実データ行があるときは保存する
    row = ["-"] * len(HEADER)
    row[8] = OFFICE_NAME
    row[9] = str(week_start.day)
    row[11] = PATIENT_NAME
    stub_kaipoke.responses["export"] = {
        "result": {"csv_content": ",".join(HEADER) + "\r\n" + ",".join(row) + "\r\n"}
    }
    await export_current_week_csv(
        kaipoke=stub_kaipoke, week_start=week_start, db=db, source_op="smart-preview"
    )
    await db.commit()

    snap = await get_latest(db, month=_month_of(week_start), week_start=week_start)
    assert snap is not None
    assert snap.week_start == week_start
    assert snap.source_op == "smart-preview"
    assert snap.row_count == 1


@pytest.mark.asyncio
async def test_smart_inbound_preview_persists_snapshot(client, db, stub_kaipoke) -> None:
    """export_current_week_csv 経路の結線確認 (smart-inbound-preview)。"""
    from app.services.kaipoke.csv_builder import build_csv
    from tests.test_kaipoke_inbound import WEEK_START, _kp_row, _seed_week

    admin = await _make_user(db, "unsent-smart@example.com")
    await _seed_week(db)
    csv_text = build_csv(
        [_kp_row(date(2026, 7, 7), time(10, 0), time(10, 35))], encoding="utf-8-sig"
    ).decode("utf-8-sig")
    stub_kaipoke.responses["export"] = {"result": {"csv_content": csv_text}}

    res = await client.post(
        "/api/v1/integrations/smart-inbound-preview",
        json={"weekStart": WEEK_START.isoformat()},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text

    snap = await get_latest(db, month="2026-07", week_start=WEEK_START)
    assert snap is not None
    assert snap.week_start == WEEK_START
    assert snap.source_op == "smart-preview"


@pytest.mark.asyncio
async def test_master_reconcile_persists_snapshot(client, db, stub_kaipoke) -> None:
    """マスタ突合で回した export も「最後に見た姿」として残す。"""
    admin = await _make_user(db, "unsent-mr@example.com")
    await _seed_master(db)
    row = ["-"] * len(HEADER)
    row[0] = STAFF_NAME
    row[8] = OFFICE_NAME
    row[9] = "7"
    row[11] = PATIENT_NAME
    stub_kaipoke.responses["export"] = {
        "result": {"csv_content": ",".join(HEADER) + "\r\n" + ",".join(row) + "\r\n"}
    }

    res = await client.post(
        "/api/v1/integrations/master-reconcile",
        json={"month": "2026-09"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200, res.text

    snap = await get_latest(db, month="2026-09")
    assert snap is not None
    assert snap.source_op == "master-reconcile"
    assert snap.week_start is None
    assert snap.row_count == 1


@pytest.mark.asyncio
async def test_partial_apply_marks_sheet_partial_and_drops_snapshot(
    client, db, stub_kaipoke
) -> None:
    """部分送信の成功で snapshot を捨て、シートを partial にする。"""
    admin = await _make_user(db, "unsent-partial@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    summary = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    stub_kaipoke.responses["apply"] = {"jobId": "job-1"}

    res = await client.post(
        "/api/v1/integrations/apply",
        json={
            "sheetId": body["sheet_id"],
            "itemIds": [body["items"][0]["id"]],
            "dryRun": False,
        },
        headers=_bearer(admin),
    )
    assert res.status_code == 202, res.text

    sheet = await db.scalar(
        select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheet_id"]))
    )
    assert sheet is not None and sheet.status == "partial"
    # 送信済み = 保存CSVは古い → 「🔄突合してください」へフェイルクローズ
    assert await get_latest(db, month=_month_of(week_start), week_start=week_start) is None

    again = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert again.json()["snapshot"] is None
    # partial シートは掃除で消えない
    assert (
        await db.scalar(select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheet_id"])))
    ) is not None


@pytest.mark.asyncio
async def test_reconcile_applied_sheet_drops_snapshot(db) -> None:
    """applying → applied の決着でも保存CSVを捨てる (送信済みが再び未送信に見えない)。"""
    from app.api.v1.integrations import _reconcile_latest_job
    from app.models.kaipoke_job import KaipokeJob

    admin = await _make_user(db, "unsent-reconcile@example.com")
    week_start = _future_monday()
    await _save_empty_snapshot(db, week_start)
    sheet = CorrectionSheet(
        target_month=_month_of(week_start),
        status="applying",
        direction="outbound",
        origin="cached",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        created_by_user_id=admin.id,
    )
    db.add(sheet)
    await db.flush()
    db.add(
        KaipokeJob(
            job_type="push",
            week_start=week_start,
            params={"op": "apply", "dry_run": False, "sheet_id": str(sheet.id)},
            status="running",
            created_by_user_id=admin.id,
        )
    )
    await db.commit()
    assert await get_latest(db, month=_month_of(week_start), week_start=week_start) is not None

    await _reconcile_latest_job(db, kaipoke_idle=True, result_payload={"result": {"ok": True}})

    await db.refresh(sheet)
    assert sheet.status == "applied"
    assert await get_latest(db, month=_month_of(week_start), week_start=week_start) is None


# --- 8. ⇧上書き: inbound シートの反転 ---------------------------------------


async def _seed_inbound_sheet(db, user: User, week_start: date) -> CorrectionSheet:
    """らく助(before) vs カイポケ(after) の inbound シートを 1 枚作る。"""
    sheet = CorrectionSheet(
        target_month=_month_of(week_start),
        status="ready",
        direction="inbound",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        created_by_user_id=user.id,
    )
    db.add(sheet)
    await db.flush()

    def _day(offset: int) -> str:
        return str((week_start + timedelta(days=offset)).day)

    def _side(day: str, start: str, staff1: str) -> dict[str, Any]:
        return {
            "user_name": PATIENT_NAME,
            "date": day,
            "start_time": start,
            "end_time": "10:35",
            "staff1": staff1,
            "staff2": "",
            "service_type": "精神基本療養費Ⅰ・正看",
            "business_type": "医療保険",
            "remarks": "",
        }

    def _empty() -> dict[str, Any]:
        blank = {k: "" for k in _side("", "", "")}
        blank["user_name"] = PATIENT_NAME
        return blank

    db.add_all(
        [
            # カイポケにだけある予定 (取り込むと add) → 反転すると delete
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="add",
                before=_empty(),
                after=_side(_day(0), "10:00", STAFF_NAME),
                include=True,
            ),
            # らく助にだけある予定 (取り込むと delete) → 反転すると add
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="delete",
                before=_side(_day(1), "11:00", STAFF_NAME),
                after=_empty(),
                include=True,
            ),
            # 時刻違い (edit) → before/after 入替のみ
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="edit",
                before=_side(_day(2), "09:00", STAFF_NAME),
                after=_side(_day(2), "14:00", STAFF_NAME),
                include=True,
            ),
            # 日付違い (date_change) → 反転で date_from=カイポケ日 / date_to=らく助日
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="date_change",
                before=_side(_day(3), "13:00", STAFF_NAME),
                after=_side(_day(4), "13:00", STAFF_NAME),
                include=True,
            ),
            # 未選択は反転対象外
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="edit",
                before=_side(_day(5), "08:00", STAFF_NAME),
                after=_side(_day(5), "16:00", STAFF_NAME),
                include=False,
            ),
        ]
    )
    await db.commit()
    await db.refresh(sheet)
    return sheet


@pytest.mark.asyncio
async def test_reverse_inbound_sheet_flips_actions_and_sides(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-a@example.com")
    week_start = _future_monday()
    sheet = await _seed_inbound_sheet(db, admin, week_start)

    res = await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["item_count"] == 4  # include=False は含めない
    assert stub_kaipoke.calls == []

    new_sheet = await db.scalar(
        select(CorrectionSheet).where(CorrectionSheet.id == UUID(body["sheet_id"]))
    )
    assert new_sheet is not None
    assert new_sheet.direction == "outbound"
    assert new_sheet.origin == "reverse"
    assert new_sheet.week_start == week_start
    assert new_sheet.target_month == sheet.target_month

    items = (
        await db.scalars(
            select(CorrectionSheetItem).where(CorrectionSheetItem.sheet_id == new_sheet.id)
        )
    ).all()
    by_action = {it.action: it for it in items}
    assert sorted(by_action) == ["add", "date_change", "delete", "edit"]

    # inbound の add (カイポケにだけある) → outbound の delete (カイポケから消す)
    assert by_action["delete"].before["start_time"] == "10:00"
    assert by_action["delete"].after["start_time"] == ""

    # inbound の delete (らく助にだけある) → outbound の add (カイポケへ作る)
    assert by_action["add"].before["start_time"] == ""
    assert by_action["add"].after["start_time"] == "11:00"

    # edit は「らく助の値へ戻す」= before/after 入替
    assert by_action["edit"].before["start_time"] == "14:00"  # カイポケの現況
    assert by_action["edit"].after["start_time"] == "09:00"  # らく助が正
    assert all(it.include for it in items)


@pytest.mark.asyncio
async def test_reverse_output_maps_to_kaipoke_correction_direction(
    client, db, stub_kaipoke
) -> None:
    """反転シートを送信形式へ落とすと from=カイポケ日 / to=らく助日 になる。"""
    admin = await _make_user(db, "reverse-map@example.com")
    week_start = _future_monday()
    sheet = await _seed_inbound_sheet(db, admin, week_start)
    kaipoke_day = str((week_start + timedelta(days=4)).day)  # inbound after = カイポケ
    rakusuke_day = str((week_start + timedelta(days=3)).day)  # inbound before = らく助

    res = await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse", headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text

    item = await db.scalar(
        select(CorrectionSheetItem).where(
            CorrectionSheetItem.sheet_id == UUID(res.json()["sheet_id"]),
            CorrectionSheetItem.action == "date_change",
        )
    )
    assert item is not None
    correction = item_to_kaipoke_correction(item.action, item.before, item.after)
    assert correction["date_from"] == kaipoke_day, "現況(カイポケ)の位置から動かす"
    assert correction["date_to"] == rakusuke_day, "らく助が正の位置へ寄せる"
    assert correction["action"] == "date_change"
    assert correction["user_name"] == PATIENT_NAME


@pytest.mark.asyncio
async def test_reverse_replaces_previous_reverse_sheet(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-dup@example.com")
    week_start = _future_monday()
    sheet = await _seed_inbound_sheet(db, admin, week_start)
    url = f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse"

    first = await client.post(url, headers=_bearer(admin))
    second = await client.post(url, headers=_bearer(admin))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["sheet_id"] != second.json()["sheet_id"]

    sheets = (
        await db.scalars(
            select(CorrectionSheet).where(
                CorrectionSheet.origin == "reverse", CorrectionSheet.week_start == week_start
            )
        )
    ).all()
    assert len(sheets) == 1
    assert str(sheets[0].id) == second.json()["sheet_id"]


@pytest.mark.asyncio
async def test_reverse_rejects_applied_inbound_sheet(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-applied@example.com")
    sheet = await _seed_inbound_sheet(db, admin, _future_monday())
    sheet.status = "applied"
    await db.commit()

    res = await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse", headers=_bearer(admin)
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_reverse_rejects_outbound_sheet(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-b@example.com")
    week_start = _future_monday()
    sheet = CorrectionSheet(
        target_month=_month_of(week_start),
        status="ready",
        direction="outbound",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        created_by_user_id=admin.id,
    )
    db.add(sheet)
    await db.commit()

    res = await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse", headers=_bearer(admin)
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_reverse_unknown_sheet_returns_404(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-c@example.com")
    res = await client.post(
        "/api/v1/integrations/correction-sheets/00000000-0000-0000-0000-000000000000/reverse",
        headers=_bearer(admin),
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_reverse_requires_admin(client, db, stub_kaipoke) -> None:
    admin = await _make_user(db, "reverse-d@example.com")
    staff_user = await _make_user(db, "reverse-staff@example.com", role="staff")
    sheet = await _seed_inbound_sheet(db, admin, _future_monday())
    res = await client.post(
        f"/api/v1/integrations/correction-sheets/{sheet.id}/reverse", headers=_bearer(staff_user)
    )
    assert res.status_code == 403, res.text


# ===========================================================================
# H1 (S2 レビュー / 2026-08-23): RPA 未対応ガード
#
# 正典 = docs/plans/kaipoke-service-content-design.md §3。
# S2 でサービス内容が 4 通りに増えたが RPA (auto_apply) はまだ固定値でしか
# 登録できない。准看/一般の add を送るとカイポケに黙って誤った値が入るので、
# S3 が終わるまで送信対象から外す (設定で門を開けられる)。
# ===========================================================================


async def _make_patient_general(db, seeded) -> None:
    """患者を「一般」に切り替える = サービス内容が「基本療養費Ⅰ・正看」になる。"""
    seeded["patient"].visit_category = "general"
    await db.commit()


def _open_rpa_gate(monkeypatch, *, enabled: bool) -> None:
    """S3 完了後の状態 (enabled=True) を再現する。

    ``get_settings`` は lru_cache された単一インスタンスなので、その属性を
    直接差し替える (環境変数 + cache_clear より副作用が小さい)。
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "kaipoke_rpa_service_branch_enabled", enabled)


@pytest.mark.asyncio
async def test_unsent_summary_flags_rpa_unsupported_add(client, db, stub_kaipoke) -> None:
    """一般の患者の add は rpa_unsupported=True で「送れる」から外れる。"""
    admin = await _make_user(db, "unsent-rpa-a@example.com")
    seeded = await _seed_master(db)
    await _make_patient_general(db, seeded)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["action"] == "add"
    assert item["after"]["service_type"] == "基本療養費Ⅰ・正看"
    assert item["rpa_unsupported"] is True
    # 過去日ではないが送れない = rpa_unsupported_count に 1、sendable は 0。
    assert body["rpa_unsupported_count"] == 1
    assert body["past_count"] == 0
    assert body["sendable_count"] == 0


@pytest.mark.asyncio
async def test_unsent_summary_supported_add_stays_sendable(client, db, stub_kaipoke) -> None:
    """既定 (精神科 × 看護師) の add は従来どおり送れる (ガードの巻き添えなし)。"""
    admin = await _make_user(db, "unsent-rpa-b@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    body = res.json()
    assert body["items"][0]["after"]["service_type"] == "精神基本療養費Ⅰ・正看"
    assert body["items"][0]["rpa_unsupported"] is False
    assert body["rpa_unsupported_count"] == 0
    assert body["sendable_count"] == 1


@pytest.mark.asyncio
async def test_unsent_summary_gate_open_after_s3(client, db, stub_kaipoke, monkeypatch) -> None:
    """設定を True (S3 完了) にすれば一般の add も送れるようになる。"""
    admin = await _make_user(db, "unsent-rpa-c@example.com")
    seeded = await _seed_master(db)
    await _make_patient_general(db, seeded)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)
    _open_rpa_gate(monkeypatch, enabled=True)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    body = res.json()
    assert body["items"][0]["rpa_unsupported"] is False
    assert body["rpa_unsupported_count"] == 0
    assert body["sendable_count"] == 1


@pytest.mark.asyncio
async def test_apply_rejects_when_all_items_rpa_unsupported(client, db, stub_kaipoke) -> None:
    """送信対象が RPA 未対応だけなら 422 で止める (RPA は呼ばない)。"""
    admin = await _make_user(db, "unsent-rpa-d@example.com")
    seeded = await _seed_master(db)
    await _make_patient_general(db, seeded)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start)
    await _save_empty_snapshot(db, week_start)

    summary = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    body = summary.json()
    stub_kaipoke.responses["apply"] = {"jobId": "job-rpa"}

    res = await client.post(
        "/api/v1/integrations/apply",
        json={
            "sheetId": body["sheet_id"],
            "itemIds": [body["items"][0]["id"]],
            "dryRun": False,
        },
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
    assert "RPA が准看/一般の登録に未対応(S3)" in res.json()["detail"]
    # 一番大事: 誤った値でカイポケに登録しに行っていない。
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []


@pytest.mark.asyncio
async def test_apply_skips_rpa_unsupported_and_sends_the_rest(client, db, stub_kaipoke) -> None:
    """混在時: 対応済みだけ送り、除外件数と理由を result_summary に残す。"""
    from app.models.kaipoke_job import KaipokeJob

    admin = await _make_user(db, "unsent-rpa-e@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    # 精神科 (送れる) の患者 = 既定シード。一般 (送れない) の患者を足す。
    general = Patient(
        code="PT-UNSENT-GEN",
        name="兼行　様",
        status="active",
        insurance="medical",
        primary_office_id=seeded["office"].id,
        visit_category="general",
    )
    db.add(general)
    await db.commit()
    await _add_visit(db, seeded, week_start)
    db.add(
        Visit(
            patient_id=general.id,
            visit_date=week_start,
            start_time=time(14, 0),
            end_time=time(14, 35),
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=seeded["staff"].id,
        )
    )
    await db.commit()
    await _save_empty_snapshot(db, week_start)

    summary = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    body = summary.json()
    assert len(body["items"]) == 2
    assert body["rpa_unsupported_count"] == 1
    assert body["sendable_count"] == 1

    stub_kaipoke.responses["apply"] = {"jobId": "job-rpa-mix"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": body["sheet_id"], "dryRun": False},
        headers=_bearer(admin),
    )
    assert res.status_code == 202, res.text

    # カイポケへ渡ったのは対応済みの 1 件だけ。
    apply_calls = [payload for name, payload in stub_kaipoke.calls if name == "apply"]
    assert len(apply_calls) == 1
    sent = apply_calls[0]["correction_data"]
    assert len(sent) == 1
    assert sent[0]["service_type"] == "精神基本療養費Ⅰ・正看"

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == UUID(res.json()["jobId"])))
    assert job is not None
    assert job.result_summary["skipped_rpa_unsupported"] == 1
    assert (
        job.result_summary["skipped_rpa_unsupported_reason"] == "RPA が准看/一般の登録に未対応(S3)"
    )
    assert job.result_summary["correction_count"] == 1


# ===========================================================================
# H2 (2026-08-23 レビュー): ペア保護 — 除外した add の相方 delete も送らない
#
# サービス内容のズレは delete + add の 2 行で表現される (設計 §3-1)。add だけ
# 落として delete を送ると **カイポケから予定が丸ごと消える** = 誤った値で
# 登録されるより悪い。同一キー (日, 開始時刻, 正規化患者名) の delete も外す。
# ===========================================================================


async def _seed_general_patient(db, seeded, *, name: str = "兼行　様") -> Patient:
    """一般 (visit_category='general') の患者 = らく助の add が RPA 未対応になる。"""
    p = Patient(
        code=f"PT-PAIR-{name}",
        name=name,
        status="active",
        insurance="medical",
        primary_office_id=seeded["office"].id,
        visit_category="general",
    )
    db.add(p)
    await db.commit()
    return p


async def _save_snapshot_with_row(
    db,
    week_start: date,
    *,
    day: date,
    patient_name: str,
    service_content: str,
    start: str = "10:00",
    end: str = "10:35",
) -> None:
    """カイポケ側に 1 行だけ入っている現況を保存する。

    らく助の同じ訪問とサービス内容だけが違えば、差分は delete + add のペアで出る。
    """
    weekday = "月火水木金土日"[day.weekday()]
    row = [
        STAFF_NAME,
        "看護師",
        "",
        "",
        "",
        "",
        "",
        "",
        OFFICE_NAME,
        str(day.day),
        weekday,
        patient_name,
        "医療保険",
        service_content,
        start,
        end,
        "35",
        "",
    ]
    csv_text = ",".join(HEADER) + "\r\n" + ",".join(row) + "\r\n"
    await save_snapshot(
        db,
        office_id=None,
        month=_month_of(week_start),
        week_start=None,
        csv_text=csv_text,
        source_op="test",
    )
    await db.commit()


async def _seed_service_mismatch_pair(db, seeded, week_start: date) -> tuple[Patient, date]:
    """「サービス内容だけが違う」状態を作る (らく助=一般 / カイポケ=精神科)。"""
    general = await _seed_general_patient(db, seeded)
    day = week_start + timedelta(days=1)
    db.add(
        Visit(
            patient_id=general.id,
            visit_date=day,
            start_time=time(10, 0),
            end_time=time(10, 35),
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=seeded["staff"].id,
        )
    )
    await db.commit()
    await _save_snapshot_with_row(
        db,
        week_start,
        day=day,
        patient_name=general.name,
        service_content="精神基本療養費Ⅰ・正看",
    )
    return general, day


@pytest.mark.asyncio
async def test_unsent_summary_flags_paired_delete_as_unsupported(client, db, stub_kaipoke) -> None:
    """サービス内容のズレは delete+add の両方に rpa_unsupported が立ち、送れない。"""
    admin = await _make_user(db, "unsent-pair-a@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _seed_service_mismatch_pair(db, seeded, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()

    by_action = {it["action"]: it for it in body["items"]}
    assert set(by_action) == {"add", "delete"}
    assert by_action["add"]["after"]["service_type"] == "基本療養費Ⅰ・正看"
    assert by_action["delete"]["before"]["service_type"] == "精神基本療養費Ⅰ・正看"
    # 相方の delete にもフラグが立つ = FE はサービス内容を自前で見なくてよい。
    assert by_action["add"]["rpa_unsupported"] is True
    assert by_action["delete"]["rpa_unsupported"] is True
    assert body["rpa_unsupported_count"] == 2
    assert body["sendable_count"] == 0


@pytest.mark.asyncio
async def test_apply_refuses_paired_delete_even_when_selected_alone(
    client, db, stub_kaipoke
) -> None:
    """delete だけを部分適用しようとしても送らない (カイポケの行だけ消える事故)。"""
    admin = await _make_user(db, "unsent-pair-b@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _seed_service_mismatch_pair(db, seeded, week_start)

    body = (
        await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
    ).json()
    delete_item = next(it for it in body["items"] if it["action"] == "delete")

    stub_kaipoke.responses["apply"] = {"jobId": "job-pair"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": body["sheet_id"], "dryRun": False, "itemIds": [delete_item["id"]]},
        headers=_bearer(admin),
    )
    # 送信対象が空になる = 422 (RPA は呼ばない)。
    assert res.status_code == 422, res.text
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []


@pytest.mark.asyncio
async def test_apply_keeps_unrelated_delete_sendable(client, db, stub_kaipoke) -> None:
    """ペアでない delete は巻き添えにしない (ガードの過剰適用を防ぐ)。"""
    admin = await _make_user(db, "unsent-pair-c@example.com")
    await _seed_master(db)
    week_start = _future_monday()
    day = week_start + timedelta(days=1)
    # カイポケにだけある行 (らく助に対応する訪問なし) = 純粋な delete。
    await _save_snapshot_with_row(
        db,
        week_start,
        day=day,
        patient_name=PATIENT_NAME,
        service_content="精神基本療養費Ⅰ・正看",
    )

    body = (
        await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
    ).json()
    assert [it["action"] for it in body["items"]] == ["delete"]
    assert body["items"][0]["rpa_unsupported"] is False
    assert body["sendable_count"] == 1


@pytest.mark.asyncio
async def test_service_override_clears_the_pair_from_unsent(client, db, stub_kaipoke) -> None:
    """受け入れ: 訪問上書きを適用 → 再計算でペアが消える (C1 の目的そのもの)。"""
    admin = await _make_user(db, "unsent-pair-d@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _seed_service_mismatch_pair(db, seeded, week_start)

    body = (
        await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
    ).json()
    delete_item = next(it for it in body["items"] if it["action"] == "delete")

    # 同期バーの「この訪問だけカイポケに合わせる」= delete 側 item から visit を解決。
    ov = await client.post(
        "/api/v1/schedule/v2/visit-service-override",
        json={"item_id": delete_item["id"], "service_content": "精神基本療養費Ⅰ・正看"},
        headers=_bearer(admin),
    )
    assert ov.status_code == 200, ov.text
    assert ov.json()["kaipoke_service_override"] == "精神基本療養費Ⅰ・正看"

    after = (
        await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
    ).json()
    assert after["items"] == []
    assert after["rpa_unsupported_count"] == 0


# ===========================================================================
# H3 (2026-08-23): 自動送信できない予定があることを admin へお知らせ
#
# 正典 = docs/plans/kaipoke-service-content-design.md §3-2 / §4。
# ガードは黙って送信対象から外すので、気付けないと該当訪問がいつまでも
# カイポケに入らない。冪等キーは **週だけ** (同期バーを開くたびに増えず、
# 件数が変わったら本文を書き換えて未読に戻す = 最新の 1 通だけが残る)。
# ===========================================================================


async def _make_patient_general(db, seeded) -> None:
    """患者を「一般」に切り替える = サービス内容が「基本療養費Ⅰ・正看」になる。"""
    seeded["patient"].visit_category = "general"
    await db.commit()


def _open_rpa_gate(monkeypatch, *, enabled: bool) -> None:
    """S3 完了後の状態 (enabled=True) を再現する。

    ``get_settings`` は lru_cache された単一インスタンスなので、その属性を
    直接差し替える (環境変数 + cache_clear より副作用が小さい)。
    """
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "kaipoke_rpa_service_branch_enabled", enabled)


@pytest.mark.asyncio
async def test_unsent_summary_notifies_admin_once_per_week(client, db, stub_kaipoke) -> None:
    """自動送信できない予定があれば admin へ 1 通。同じ週で件数も同じなら増えない。"""
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-notify-a@example.com")
    other_admin = await _make_user(db, "unsent-notify-a2@example.com")
    staff_user = await _make_user(db, "unsent-notify-a3@example.com", role="staff")
    seeded = await _seed_master(db)
    await _make_patient_general(db, seeded)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    for _ in range(2):
        res = await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
        assert res.status_code == 200, res.text
        assert res.json()["rpa_unsupported_count"] == 1

    rows = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "rpa_unsupported")
            )
        ).all()
    )
    # admin 2 名ぶんだけ (同期バーを 2 回開いても増えない)。staff には出さない。
    assert {r.user_id for r in rows} == {admin.id, other_admin.id}
    assert staff_user.id not in {r.user_id for r in rows}
    assert all(r.title == "カイポケへ自動送信できない予定が 1 件あります" for r in rows)
    for r in rows:
        assert "らく助からカイポケへ自動で登録できない予定が 1 件あります" in (r.body or "")
        assert "准看護師の訪問・一般の訪問看護" in (r.body or "")
        assert "カイポケの画面から直接ご登録ください" in (r.body or "")


@pytest.mark.asyncio
async def test_unsent_summary_updates_notification_when_count_changes(
    client, db, stub_kaipoke
) -> None:
    """件数が変わったら本文を書き換えて未読に戻す (古い数字の通知を溜めない)。"""
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-notify-b@example.com")
    seeded = await _seed_master(db)
    await _make_patient_general(db, seeded)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["rpa_unsupported_count"] == 1

    # 既読にしてから件数を増やす → 未読に戻って新しい数字になる。
    row = await db.scalar(
        select(Notification).where(Notification.reference_type == "rpa_unsupported")
    )
    assert row is not None
    row.read_at = datetime.now(ZoneInfo("Asia/Tokyo"))
    await db.commit()

    await _add_visit(db, seeded, week_start + timedelta(days=2), start=time(13, 0))
    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["rpa_unsupported_count"] == 2

    # API は別セッションで UPDATE している。テスト側の identity map に残る
    # 古い属性を捨ててから読み直す (expire_on_commit=False のため)。
    db.expire_all()
    rows = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "rpa_unsupported")
            )
        ).all()
    )
    # 週ごとに 1 通のまま (件数ごとに増やさない)。
    assert len(rows) == 1
    assert rows[0].title == "カイポケへ自動送信できない予定が 2 件あります"
    assert rows[0].read_at is None


@pytest.mark.asyncio
async def test_unsent_summary_does_not_notify_when_all_sendable(client, db, stub_kaipoke) -> None:
    """自動送信できない予定が 0 件なら通知しない (既定の精神科 × 看護師)。"""
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-notify-c@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["rpa_unsupported_count"] == 0
    rows = (
        await db.scalars(
            select(Notification).where(Notification.reference_type == "rpa_unsupported")
        )
    ).all()
    assert list(rows) == []


def test_dedup_reference_id_is_stable_per_week() -> None:
    """冪等キーは週の決定的 UUID — 件数は含めない (同じ週は 1 通に保つ)。"""
    from app.services.kaipoke.rpa_unsupported_notify import dedup_reference_id

    week = date(2026, 8, 24)
    assert dedup_reference_id(week) == dedup_reference_id(week)
    assert dedup_reference_id(week) != dedup_reference_id(week + timedelta(days=7))


# ===========================================================================
# 担当なしガード (2026-09-03 本番事故)
#
# 職員1が空/'-' の add/edit を RPA へ送ると、カイポケには担当なしの行として
# 入るが、スケジュール表CSV(職員別)は未割当行を出さない = らく助からは
# 「送れていない」ように見えて add を繰り返す (二重登録)。edit は実在の職員を
# '-' で上書きして担当を消す。担当が付くまで送らない。
# ===========================================================================


async def _seed_unassigned_outbound_sheet(db, user: User, week_start: date) -> CorrectionSheet:
    """担当なしの add / edit と、担当が付いた add を 1 枚に混ぜた outbound シート。"""
    sheet = CorrectionSheet(
        target_month=_month_of(week_start),
        status="ready",
        direction="outbound",
        origin="cached",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        created_by_user_id=user.id,
    )
    db.add(sheet)
    await db.flush()

    def _side(offset: int, start: str, staff1: str) -> dict[str, Any]:
        return {
            "user_name": PATIENT_NAME,
            "date": str((week_start + timedelta(days=offset)).day),
            "start_time": start,
            "end_time": "10:35",
            "staff1": staff1,
            "staff2": "",
            "service_type": "精神基本療養費Ⅰ・正看",
            "business_type": "医療保険",
            "remarks": "",
        }

    def _empty() -> dict[str, Any]:
        blank = {k: "" for k in _side(0, "", "")}
        blank["user_name"] = PATIENT_NAME
        return blank

    db.add_all(
        [
            # 担当なしの新規 (らく助のプール行) → 送らない
            CorrectionSheetItem(
                sheet_id=sheet.id, action="add", before=_empty(), after=_side(0, "10:00", "-")
            ),
            # 担当を消す変更 (カイポケの熊澤を '-' で上書き) → 送らない
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="edit",
                before=_side(1, "11:00", STAFF_NAME),
                after=_side(1, "11:00", "-"),
            ),
            # 担当が付いた新規 → これだけ送る
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="add",
                before=_empty(),
                after=_side(2, "13:00", STAFF_NAME),
            ),
        ]
    )
    await db.commit()
    await db.refresh(sheet)
    return sheet


@pytest.mark.asyncio
async def test_apply_skips_unassigned_and_sends_the_rest(client, db, stub_kaipoke) -> None:
    """担当なしの add/edit は送らず、担当が付いた 1 件だけカイポケへ渡す。"""
    from app.models.kaipoke_job import KaipokeJob

    admin = await _make_user(db, "unsent-na-a@example.com")
    week_start = _future_monday()
    sheet = await _seed_unassigned_outbound_sheet(db, admin, week_start)

    stub_kaipoke.responses["apply"] = {"jobId": "job-unassigned"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": str(sheet.id), "dryRun": False},
        headers=_bearer(admin),
    )
    assert res.status_code == 202, res.text

    apply_calls = [payload for name, payload in stub_kaipoke.calls if name == "apply"]
    assert len(apply_calls) == 1
    sent = apply_calls[0]["correction_data"]
    assert len(sent) == 1
    assert sent[0]["staff1_to"] == STAFF_NAME

    job = await db.scalar(select(KaipokeJob).where(KaipokeJob.id == UUID(res.json()["jobId"])))
    assert job is not None
    assert job.result_summary["skipped_unassigned"] == 2
    assert "担当なし" in job.result_summary["skipped_unassigned_reason"]
    assert job.result_summary["correction_count"] == 1
    # 監査カウンタは上流ガードで常に 0 になる。
    assert job.result_summary["unassigned_staff"] == 0
    assert job.params["skipped_unassigned"] == 2


@pytest.mark.asyncio
async def test_apply_rejects_when_all_items_unassigned(client, db, stub_kaipoke) -> None:
    """選んだのが担当なしだけなら 422 で止める (RPA は呼ばない)。"""
    admin = await _make_user(db, "unsent-na-b@example.com")
    week_start = _future_monday()
    sheet = await _seed_unassigned_outbound_sheet(db, admin, week_start)
    items = (
        await db.scalars(
            select(CorrectionSheetItem).where(CorrectionSheetItem.sheet_id == sheet.id)
        )
    ).all()
    unassigned_ids = [
        str(it.id) for it in items if str((it.after or {}).get("staff1") or "").strip() in ("", "-")
    ]
    assert len(unassigned_ids) == 2

    stub_kaipoke.responses["apply"] = {"jobId": "job-unassigned-all"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": str(sheet.id), "itemIds": unassigned_ids, "dryRun": False},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
    assert "担当なし" in res.json()["detail"]
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []

    # dry-run でも同じ 422 = 「試しに送る」でも RPA には触れない。
    dry = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": str(sheet.id), "itemIds": unassigned_ids, "dryRun": True},
        headers=_bearer(admin),
    )
    assert dry.status_code == 422, dry.text
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []


async def _seed_unassigned_pair_sheet(db, user: User, week_start: date) -> CorrectionSheet:
    """担当なし add + 同じ訪問を指す delete のペア (レビュー HIGH の再現)。

    差分エンジンはサービス内容も突合キーに含めるため、「カイポケ=准看で担当あり /
    らく助=担当なし ('-' → 正看)」は edit ではなく delete + add に割れる。
    """
    sheet = CorrectionSheet(
        target_month=_month_of(week_start),
        status="ready",
        direction="outbound",
        origin="cached",
        week_start=week_start,
        week_end=week_start + timedelta(days=6),
        created_by_user_id=user.id,
    )
    db.add(sheet)
    await db.flush()

    day = str((week_start + timedelta(days=1)).day)

    def _side(staff1: str, service: str) -> dict[str, Any]:
        return {
            "user_name": PATIENT_NAME,
            "date": day,
            "start_time": "10:00",
            "end_time": "10:35",
            "staff1": staff1,
            "staff2": "",
            "service_type": service,
            "business_type": "医療保険",
            "remarks": "",
        }

    blank = {k: "" for k in _side("", "")}
    blank["user_name"] = PATIENT_NAME

    db.add_all(
        [
            # らく助の担当なし行 (職員 None なので正看)
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="add",
                before=dict(blank),
                after=_side("-", "精神基本療養費Ⅰ・正看"),
            ),
            # カイポケに入っている同じ訪問 (准看で担当あり)
            CorrectionSheetItem(
                sheet_id=sheet.id,
                action="delete",
                before=_side(STAFF_NAME, "精神基本療養費Ⅰ・准看"),
                after=dict(blank),
            ),
        ]
    )
    await db.commit()
    await db.refresh(sheet)
    return sheet


@pytest.mark.asyncio
async def test_apply_refuses_unassigned_paired_delete_even_when_selected_alone(
    client, db, stub_kaipoke
) -> None:
    """担当なし add の相方 delete だけを部分適用しても送らない。

    片肺で送るとカイポケから予定が丸ごと消えて作り直されない (レビュー HIGH)。
    """
    admin = await _make_user(db, "unsent-na-pair-a@example.com")
    week_start = _future_monday()
    sheet = await _seed_unassigned_pair_sheet(db, admin, week_start)
    items = (
        await db.scalars(
            select(CorrectionSheetItem).where(CorrectionSheetItem.sheet_id == sheet.id)
        )
    ).all()
    delete_id = next(str(it.id) for it in items if it.action == "delete")

    stub_kaipoke.responses["apply"] = {"jobId": "job-na-pair"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": str(sheet.id), "itemIds": [delete_id], "dryRun": False},
        headers=_bearer(admin),
    )
    assert res.status_code == 422, res.text
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []


@pytest.mark.asyncio
async def test_apply_all_skips_the_whole_unassigned_pair(client, db, stub_kaipoke) -> None:
    """全件送信でもペアごと外す (delete だけがカイポケへ渡らない)。"""
    admin = await _make_user(db, "unsent-na-pair-b@example.com")
    week_start = _future_monday()
    sheet = await _seed_unassigned_pair_sheet(db, admin, week_start)

    stub_kaipoke.responses["apply"] = {"jobId": "job-na-pair-all"}
    res = await client.post(
        "/api/v1/integrations/apply",
        json={"sheetId": str(sheet.id), "dryRun": False},
        headers=_bearer(admin),
    )
    # 送るものが残らない = 422。カイポケには一切触れない。
    assert res.status_code == 422, res.text
    assert "担当なし" in res.json()["detail"]
    assert [name for name, _ in stub_kaipoke.calls if name == "apply"] == []


@pytest.mark.asyncio
async def test_unsent_summary_flags_unassigned_visit(client, db, stub_kaipoke) -> None:
    """担当なしの訪問は unassigned=True・sendable から外れる。"""
    admin = await _make_user(db, "unsent-na-c@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    db.add(
        Visit(
            patient_id=seeded["patient"].id,
            visit_date=week_start,
            start_time=time(10, 0),
            end_time=time(10, 35),
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=None,
        )
    )
    await db.commit()
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["after"]["staff1"] == "-"
    assert body["items"][0]["unassigned"] is True
    assert body["unassigned_count"] == 1
    assert body["sendable_count"] == 0


async def _add_unassigned_visit(db, seeded, d: date, start: time = time(10, 0)) -> None:
    """担当なしの訪問 (らく助のプール行)。差分では staff1='-' の add になる。"""
    db.add(
        Visit(
            patient_id=seeded["patient"].id,
            visit_date=d,
            start_time=start,
            end_time=time(start.hour, 35),
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=None,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_unsent_summary_notifies_admin_once_per_week_for_unassigned(
    client, db, stub_kaipoke
) -> None:
    """担当なしが残っていれば admin へ 1 通。同じ週で件数も同じなら増えない。

    ⇧上書き/連携ページの「全件送る」は BE が黙って外すため、通知が無いと
    「送れる 0 件」で平穏に見えたまま予定が登録されない。
    """
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-na-notify-a@example.com")
    other_admin = await _make_user(db, "unsent-na-notify-a2@example.com")
    staff_user = await _make_user(db, "unsent-na-notify-a3@example.com", role="staff")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_unassigned_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    for _ in range(2):
        res = await client.post(
            UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
        )
        assert res.status_code == 200, res.text
        assert res.json()["unassigned_count"] == 1

    rows = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "unassigned_unsent")
            )
        ).all()
    )
    # admin 2 名ぶんだけ (同期バーを 2 回開いても増えない)。staff には出さない。
    assert {r.user_id for r in rows} == {admin.id, other_admin.id}
    assert staff_user.id not in {r.user_id for r in rows}
    assert all(r.title == "担当なしでカイポケへ送れない予定が 1 件あります" for r in rows)
    for r in rows:
        assert "担当なしの予定が 1 件あり、カイポケへ送れません" in (r.body or "")
        assert "先に担当を付けてください" in (r.body or "")
    # RPA 未対応のお知らせとは別枠 (理由が違えば対処も違う)。
    assert (
        await db.scalars(
            select(Notification).where(Notification.reference_type == "rpa_unsupported")
        )
    ).all() == []


@pytest.mark.asyncio
async def test_unsent_summary_updates_unassigned_notification_when_count_changes(
    client, db, stub_kaipoke
) -> None:
    """件数が変わったら本文を書き換えて未読に戻す (古い数字の通知を溜めない)。"""
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-na-notify-b@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_unassigned_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["unassigned_count"] == 1

    row = await db.scalar(
        select(Notification).where(Notification.reference_type == "unassigned_unsent")
    )
    assert row is not None
    row.read_at = datetime.now(ZoneInfo("Asia/Tokyo"))
    await db.commit()

    await _add_unassigned_visit(db, seeded, week_start + timedelta(days=2), start=time(13, 0))
    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["unassigned_count"] == 2

    # API は別セッションで UPDATE している。テスト側の identity map に残る
    # 古い属性を捨ててから読み直す (expire_on_commit=False のため)。
    db.expire_all()
    rows = list(
        (
            await db.scalars(
                select(Notification).where(Notification.reference_type == "unassigned_unsent")
            )
        ).all()
    )
    # 週ごとに 1 通のまま (件数ごとに増やさない)。
    assert len(rows) == 1
    assert rows[0].title == "担当なしでカイポケへ送れない予定が 2 件あります"
    assert rows[0].read_at is None


@pytest.mark.asyncio
async def test_unsent_summary_does_not_notify_when_no_unassigned(client, db, stub_kaipoke) -> None:
    """担当なしが 0 件なら通知しない。"""
    from app.models.notification import Notification

    admin = await _make_user(db, "unsent-na-notify-c@example.com")
    seeded = await _seed_master(db)
    week_start = _future_monday()
    await _add_visit(db, seeded, week_start + timedelta(days=1))
    await _save_empty_snapshot(db, week_start)

    res = await client.post(
        UNSENT_URL, json={"week_start": week_start.isoformat()}, headers=_bearer(admin)
    )
    assert res.json()["unassigned_count"] == 0
    rows = (
        await db.scalars(
            select(Notification).where(Notification.reference_type == "unassigned_unsent")
        )
    ).all()
    assert list(rows) == []


def test_unassigned_dedup_reference_id_is_stable_and_distinct() -> None:
    """冪等キーは週の決定的 UUID。RPA 未対応のキーとは必ず別 (1 通に潰さない)。"""
    from app.services.kaipoke.rpa_unsupported_notify import (
        dedup_reference_id,
        unassigned_dedup_reference_id,
    )

    week = date(2026, 8, 24)
    assert unassigned_dedup_reference_id(week) == unassigned_dedup_reference_id(week)
    assert unassigned_dedup_reference_id(week) != unassigned_dedup_reference_id(
        week + timedelta(days=7)
    )
    assert unassigned_dedup_reference_id(week) != dedup_reference_id(week)
