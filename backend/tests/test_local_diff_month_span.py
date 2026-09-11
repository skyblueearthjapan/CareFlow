"""月を跨ぐ週の build_local_diff — 前月頭の行が混入しないこと (2026-09-11 の根治).

バグ: らく助側を ``build_month_csv`` で **1 か月ぶんだけ** 生成して
diff/engine の週フィルタ (日 1-31 のみ・start>end を折返しと解釈) に渡すと、
2026-09-28〜10-04 の週で **9/1〜9/4 の行が 10/1〜10/4 として比較に混ざり**、
偽の delete/add/edit を生んでいた。

本テストが固定するもの:
  1. 月跨ぎ週 + 両側一致 → inbound/outbound とも差分ゼロ (9/1 の行が漏れない)
  2. 月跨ぎ週 + カイポケ側 10/1 だけ時刻違い → 両方向とも edit 1 件だけ
  3. 非跨ぎ週 (9/14〜9/20) → 従来どおり差分ゼロ (退行検知)
  4. 片方の月に らく助 の訪問が 1 件も無くても落ちない (空CSVの結合)
  5. ``current_csv`` 未指定 (= 🔄突合の実経路) のスナップショット保存:
     月跨ぎ週は **週スコープ 1 行だけ** / 非跨ぎ週は従来どおり月スコープ
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.kaipoke_csv_snapshot import KaipokeCsvSnapshot
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.visit import Visit
from app.services.kaipoke.csv_builder import KaipokeCsvRow, StaffCell, build_csv
from app.services.kaipoke.csv_snapshot import get_latest
from app.services.kaipoke.local_diff import build_local_diff

# 月跨ぎの週: 2026-09-28(月) 〜 2026-10-04(日)。day で見ると 28..4 の折返し。
SPAN_WEEK_START = date(2026, 9, 28)
SPAN_MONTH = "2026-09"  # 呼び出し側は常に week_start の月を渡す
# 非跨ぎの週 (対照群): 2026-09-14(月) 〜 2026-09-20(日)。
PLAIN_WEEK_START = date(2026, 9, 14)

OFFICE_NAME = "稲毛"
PATIENT_NAME = "山田　花子"
STAFF_NAME = "田中　看護師"
SERVICE = "精神基本療養費Ⅰ・正看"


class StubKaipokeClient:
    """export を月別レスポンスで差し替える最小スタブ。

    ``tests/test_kaipoke_smart_inbound.py`` の同名クラスと同型だが、あちらは
    別作業で編集中のため **import せずに写す** (テスト間の結合を作らない)。
    """

    def __init__(self, by_month: dict[str, str] | None = None) -> None:
        self.by_month: dict[str, str] = dict(by_month or {})
        self.calls: list[dict[str, Any]] = []

    async def aclose(self) -> None:  # pragma: no cover — 呼ばれない
        pass

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return {"result": {"csv_content": self.by_month.get(str(payload.get("month")), "")}}


def _kp_row(d: date, start: time, end: time) -> KaipokeCsvRow:
    return KaipokeCsvRow(
        patient_name=PATIENT_NAME,
        visit_date=d,
        start_time=start,
        end_time=end,
        office_name=OFFICE_NAME,
        business_type="医療保険",
        service_content=SERVICE,
        primary=StaffCell(name=STAFF_NAME, qualification="看護師"),
    )


def _kaipoke_csv(*rows: KaipokeCsvRow) -> str:
    """カイポケ現況CSV。**週結合形** (export_current_week_csv の出力と同じ形)。"""
    return build_csv(list(rows), encoding="utf-8-sig").decode("utf-8-sig")


async def _seed(db, visit_dates: list[tuple[date, time, time]]) -> dict[str, Any]:
    """office / staff / patient と指定日時の visits を作る。"""
    office = Office(name=OFFICE_NAME, code="INAGE")
    db.add(office)
    await db.flush()
    staff = Staff(name=STAFF_NAME, role="staff", primary_office_id=office.id)
    staff.qualification = "看護師"
    db.add(staff)
    await db.flush()
    patient = Patient(
        code="PT-SPAN-1",
        name=PATIENT_NAME,
        status="active",
        insurance="medical",
        primary_office_id=office.id,
    )
    db.add(patient)
    await db.flush()
    visits = [
        Visit(
            patient_id=patient.id,
            visit_date=d,
            start_time=s,
            end_time=e,
            type="regular",
            status="planned",
            source="auto",
            required_staff_count=1,
            primary_staff_id=staff.id,
        )
        for d, s, e in visit_dates
    ]
    db.add_all(visits)
    await db.commit()
    return {"office": office, "staff": staff, "patient": patient, "visits": visits}


# 月跨ぎ週の らく助 訪問。9/1 と 9/10 は **週外** (混入したら偽差分になる罠)。
#   * 9/1  — diff/engine の折返しフィルタ (day>=28 or day<=4) を通り抜けるので、
#            らく助側を月まるごと渡すと 10/1 の行と衝突する。時刻は 10/1 と
#            わざと違える (同時刻だと偶然一致してバグを素通ししてしまう)。
#   * 9/10 — 折返しフィルタでは落ちるが、**optimized_row_count には残る**。
#            月まるごと生成 (バグ) なら 3 行、月別フィルタ (修正後) なら 2 行 —
#            つまり行数だけで両者を判別できるようにするための番人。
_SPAN_VISITS = [
    (date(2026, 9, 1), time(8, 0), time(8, 35)),  # 週外 — 折返しフィルタを抜ける罠
    (date(2026, 9, 10), time(9, 0), time(9, 35)),  # 週外 — 行数の番人
    (date(2026, 9, 29), time(10, 0), time(10, 35)),
    (date(2026, 10, 1), time(10, 0), time(10, 35)),
]


async def _diff(db, *, week_start: date, month: str, current_csv: str, direction: str):
    return await build_local_diff(
        db,
        month=month,
        week_start=week_start,
        direction=direction,
        current_csv=current_csv,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
async def test_month_spanning_week_ignores_previous_month_head(db, direction: str) -> None:
    """9/28〜10/4 の週: 9/1 の訪問が 10/1 として比較に混ざらない → 差分ゼロ。"""
    await _seed(db, _SPAN_VISITS)
    current = _kaipoke_csv(
        _kp_row(date(2026, 9, 29), time(10, 0), time(10, 35)),
        _kp_row(date(2026, 10, 1), time(10, 0), time(10, 35)),
    )

    corrections, meta = await _diff(
        db,
        week_start=SPAN_WEEK_START,
        month=SPAN_MONTH,
        current_csv=current,
        direction=direction,
    )

    assert corrections == [], [
        (c.action, c.date_from, c.date_to, c.start_time_from, c.start_time_to) for c in corrections
    ]
    assert meta["scope"] == "week"
    assert meta["months"] == ["2026-09", "2026-10"]
    # らく助側は週内 2 件だけ (9/1・9/10 は月別フィルタで落ちている)。
    # バグ経路 (9月を月まるごと) なら 3 件になるので、この数値が両者を判別する。
    assert meta["optimized_row_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "expected_from", "expected_to"),
    [
        # outbound = カイポケ(11:00) を らく助(10:00) へ寄せる修正。
        ("outbound", "11:00", "10:00"),
        # inbound = らく助(10:00) をカイポケ(11:00) へ寄せる修正 (取り込み)。
        ("inbound", "10:00", "11:00"),
    ],
)
async def test_month_spanning_week_detects_real_edit_only(
    db, direction: str, expected_from: str, expected_to: str
) -> None:
    """10/1 だけカイポケが 11:00 → edit 1 件 (9/1 由来の偽差分が増えない)。"""
    await _seed(db, _SPAN_VISITS)
    current = _kaipoke_csv(
        _kp_row(date(2026, 9, 29), time(10, 0), time(10, 35)),
        _kp_row(date(2026, 10, 1), time(11, 0), time(11, 35)),
    )

    corrections, _meta = await _diff(
        db,
        week_start=SPAN_WEEK_START,
        month=SPAN_MONTH,
        current_csv=current,
        direction=direction,
    )

    assert len(corrections) == 1, [(c.action, c.date_from, c.date_to) for c in corrections]
    c = corrections[0]
    assert c.action == "edit"
    assert c.date_from == "1" and c.date_to == "1"
    assert c.start_time_from == expected_from
    assert c.start_time_to == expected_to


@pytest.mark.asyncio
async def test_month_spanning_week_with_empty_second_month(db) -> None:
    """10月に らく助 の訪問が 1 件も無い週でも落ちない (空CSVの結合)。"""
    await _seed(
        db,
        [
            (date(2026, 9, 1), time(8, 0), time(8, 35)),  # 週外
            (date(2026, 9, 29), time(10, 0), time(10, 35)),
        ],
    )
    # カイポケ側も 9/29 のみ。ただし時刻が違う = 9月由来の本物の edit が 1 件。
    current = _kaipoke_csv(_kp_row(date(2026, 9, 29), time(11, 0), time(11, 35)))

    corrections, meta = await _diff(
        db,
        week_start=SPAN_WEEK_START,
        month=SPAN_MONTH,
        current_csv=current,
        direction="outbound",
    )

    assert meta["months"] == ["2026-09", "2026-10"]
    assert meta["optimized_row_count"] == 1
    assert len(corrections) == 1, [(c.action, c.date_from, c.date_to) for c in corrections]
    c = corrections[0]
    assert c.action == "edit"
    # 9月側の行だけが差分になる (10月の空は add/delete を生まない)。
    assert c.date_from == "29" and c.date_to == "29"


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["inbound", "outbound"])
async def test_non_spanning_week_unchanged(db, direction: str) -> None:
    """非跨ぎ週 (9/14〜9/20) は従来どおり: 一致すれば差分ゼロ。"""
    await _seed(
        db,
        [
            (date(2026, 9, 1), time(10, 0), time(10, 35)),  # 週外
            (date(2026, 9, 16), time(10, 0), time(10, 35)),
        ],
    )
    current = _kaipoke_csv(_kp_row(date(2026, 9, 16), time(10, 0), time(10, 35)))

    corrections, meta = await _diff(
        db,
        week_start=PLAIN_WEEK_START,
        month="2026-09",
        current_csv=current,
        direction=direction,
    )

    assert corrections == [], [(c.action, c.date_from, c.date_to) for c in corrections]
    assert meta["months"] == ["2026-09"]
    # 非跨ぎ週は従来どおり月まるごと生成 (週の絞りは diff/engine 側) = 9/1 も行として載る。
    assert meta["optimized_row_count"] == 2


# --- current_csv 未指定 (= 🔄突合の実経路) のスナップショット保存 -----------


async def _snapshot_rows(db) -> list[KaipokeCsvSnapshot]:
    res = await db.execute(select(KaipokeCsvSnapshot))
    return list(res.scalars().all())


@pytest.mark.asyncio
async def test_spanning_week_exports_both_months_and_saves_week_snapshot(db) -> None:
    """月跨ぎ週 + current_csv=None: 両月を export し、週スコープを 1 行だけ保存。

    月スコープ (``week_start IS NULL``) は **触らない** — 週限定CSVをそこへ書くと
    同月の他の週の ●未送信 が丸ごと空 (全 add) に見えるため。
    """
    await _seed(db, _SPAN_VISITS)
    stub = StubKaipokeClient(
        {
            "2026-09": _kaipoke_csv(_kp_row(date(2026, 9, 29), time(10, 0), time(10, 35))),
            "2026-10": _kaipoke_csv(_kp_row(date(2026, 10, 1), time(10, 0), time(10, 35))),
        }
    )

    corrections, meta = await build_local_diff(
        db,
        month=SPAN_MONTH,
        kaipoke=stub,  # type: ignore[arg-type]
        week_start=SPAN_WEEK_START,
    )

    # export は月ごとに 1 回ずつ = ちょうど 2 回 (翌月が欠けると偽 add になる)。
    assert [c["month"] for c in stub.calls] == ["2026-09", "2026-10"]
    assert corrections == [], [(c.action, c.date_from, c.date_to) for c in corrections]
    assert meta["months"] == ["2026-09", "2026-10"]

    rows = await _snapshot_rows(db)
    assert len(rows) == 1, [(r.month, r.week_start) for r in rows]
    assert rows[0].week_start == date(2026, 9, 28)
    assert rows[0].month == "2026-09"
    # 月スコープの行は増えていない (●未送信 の month_only 検索は従来どおり空)。
    assert await get_latest(db, month="2026-09", month_only=True) is None


@pytest.mark.asyncio
async def test_non_spanning_week_still_saves_month_snapshot(db) -> None:
    """非跨ぎ週 + current_csv=None: 従来どおり月スコープ (week_start IS NULL) で保存。

    月跨ぎ対応で入れた ``did_export`` の分岐が、非跨ぎ週の保存まで止めていないか
    の退行検知。
    """
    await _seed(db, [(date(2026, 9, 16), time(10, 0), time(10, 35))])
    stub = StubKaipokeClient(
        {"2026-09": _kaipoke_csv(_kp_row(date(2026, 9, 16), time(10, 0), time(10, 35)))}
    )

    corrections, _meta = await build_local_diff(
        db,
        month="2026-09",
        kaipoke=stub,  # type: ignore[arg-type]
        week_start=PLAIN_WEEK_START,
    )

    assert [c["month"] for c in stub.calls] == ["2026-09"]
    assert corrections == []
    total = await db.scalar(select(func.count()).select_from(KaipokeCsvSnapshot))
    assert total == 1
    snap = await get_latest(db, month="2026-09", month_only=True)
    assert snap is not None and snap.week_start is None
