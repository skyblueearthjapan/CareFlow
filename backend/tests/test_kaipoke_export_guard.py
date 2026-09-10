"""export 失敗 (success=False / csv_content=None) を「カイポケが空」と読み違えない.

RPA は CSV が古い/取得失敗のとき ``success: False`` + ``csv_content: None`` を返す。
これを空文字として飲み込むと **らく助の全訪問が add 差分に化け**、空CSVが
「最後に見たカイポケの姿」として保存されてしまう (静かに間違う事故)。
``ensure_export_ok`` で例外に倒し、スナップショットも保存しないことを固定する。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.services.kaipoke.csv_snapshot import get_latest
from app.services.kaipoke.export_guard import KaipokeExportError, ensure_export_ok
from app.services.kaipoke.local_diff import build_local_diff, export_current_week_csv
from app.services.kaipoke_client import KaipokeApiError

HEADER = (
    "職員名１,職種１,職員名２,職種２,同行２,職員名３,職種３,同行３,"
    "事業所名,日付,曜日,利用者,業務種別,サービス内容,開始時間,終了時間,提供時間（分）,備考"
)
GOOD_CSV = (
    HEADER + "\n"
    "看護A,看護師,,,,,,,よりより,3,月,山田　太郎,医療保険,精神基本療養費Ⅰ・正看,"
    "09:00,09:35,35,\n"
)


class _ExportStub:
    """``/api/export`` だけを返すスタブ (呼ばれた回数を数える)。"""

    def __init__(
        self,
        result: dict[str, Any] | None,
        *,
        by_month: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.result = result
        #: 月ごとに別の結果を返したいとき (月跨ぎ週の検証用)。
        self.by_month = by_month or {}
        self.calls: list[dict[str, Any]] = []

    async def export(
        self, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        self.calls.append(payload)
        month = payload.get("month")
        if month in self.by_month:
            return {"result": self.by_month[month]}
        return {"result": self.result}


# --- ensure_export_ok 単体 -------------------------------------------------


def test_ensure_export_ok_returns_csv_on_success() -> None:
    assert ensure_export_ok({"success": True, "csv_content": GOOD_CSV}) == GOOD_CSV


def test_ensure_export_ok_tolerates_missing_success_key() -> None:
    """``success`` を返さない旧 RPA でも従来どおり通す (後方互換)。"""
    assert ensure_export_ok({"csv_content": GOOD_CSV}) == GOOD_CSV


def test_ensure_export_ok_raises_on_success_false() -> None:
    with pytest.raises(KaipokeExportError) as ei:
        ensure_export_ok({"success": False, "csv_content": None, "error": "csv is stale"})
    assert "予定" in str(ei.value)
    assert "csv is stale" in str(ei.value)
    assert ei.value.division == "plan"
    # 既存の except KaipokeApiError 節 (ジョブ failed + 502) で決着させるための継承。
    assert isinstance(ei.value, KaipokeApiError)
    assert ei.value.status_code == 502


def test_ensure_export_ok_raises_when_success_true_but_body_empty() -> None:
    """成功なら最低でもヘッダー行が来る — 空なら契約違反なので倒す。"""
    with pytest.raises(KaipokeExportError):
        ensure_export_ok({"success": True, "csv_content": ""})
    # row_count が付いていても 0 以外なら「本文だけ落ちた」= 契約違反。
    with pytest.raises(KaipokeExportError):
        ensure_export_ok({"success": True, "csv_content": "", "row_count": 12})


def test_ensure_export_ok_accepts_explicit_zero_rows() -> None:
    """RPA が「0 件でした」と明言しているなら、空は失敗ではなく事実。"""
    assert ensure_export_ok({"success": True, "csv_content": "", "row_count": 0}) == ""


def test_ensure_export_ok_passes_empty_body_without_success_flag() -> None:
    """「カイポケにその週の入力がまだ無い」は正常系 — 呼び出し側の 0件拒否に委ねる。

    ここで倒すと置換取り込みの「0件での置換は安全のため拒否します」という
    現場向けの案内 (422) が 502 に化けてしまう。
    """
    assert ensure_export_ok({"csv_content": ""}) == ""
    assert ensure_export_ok({}) == ""
    assert ensure_export_ok(None) == ""


def test_ensure_export_ok_names_the_actual_division() -> None:
    with pytest.raises(KaipokeExportError) as ei:
        ensure_export_ok({"success": False, "csv_content": None}, division="actual")
    assert "実績" in str(ei.value)
    assert ei.value.division == "actual"


# --- build_local_diff ------------------------------------------------------


@pytest.mark.asyncio
async def test_build_local_diff_raises_and_saves_nothing_on_failed_export(db) -> None:
    stub = _ExportStub({"success": False, "csv_content": None, "error": "download failed"})
    with pytest.raises(KaipokeExportError):
        await build_local_diff(db, month="2026-08", kaipoke=stub)  # type: ignore[arg-type]
    # 空スナップショットを残さない (= ●未送信 が全滅表示にならない)。
    assert await get_latest(db, month="2026-08") is None


@pytest.mark.asyncio
async def test_build_local_diff_success_path_unchanged(db) -> None:
    stub = _ExportStub({"success": True, "csv_content": GOOD_CSV})
    corrections, meta = await build_local_diff(db, month="2026-08", kaipoke=stub)  # type: ignore[arg-type]
    # らく助側は visits 0 件 → カイポケの1行は delete 差分。
    assert len(corrections) == 1
    assert corrections[0].action == "delete"
    assert meta["current_row_count"] == 1
    snap = await get_latest(db, month="2026-08")
    assert snap is not None and snap.csv_text == GOOD_CSV


# --- export_current_week_csv ----------------------------------------------


@pytest.mark.asyncio
async def test_export_current_week_csv_raises_on_failed_export(db) -> None:
    stub = _ExportStub({"success": False, "csv_content": None, "error": "timeout"})
    with pytest.raises(KaipokeExportError):
        await export_current_week_csv(
            kaipoke=stub,  # type: ignore[arg-type]
            week_start=date(2026, 8, 3),
            db=db,
        )
    assert await get_latest(db, month="2026-08", week_start=date(2026, 8, 3)) is None


@pytest.mark.asyncio
async def test_export_current_week_csv_keeps_rows_when_other_month_is_empty(db) -> None:
    """月跨ぎ週: 片方の月が 0 件でも、もう片方の行は残す。

    7/27〜8/2 のような週は 2 か月ぶん export する。片方が「その月に予定なし」
    (row_count 0) なだけで週まるごと落としてしまうと、正しく取れた側の行まで
    消えて「週が空」= 全 add に化ける。
    """
    from datetime import date as _date

    # 8/31(月)〜9/6(日) の週。8 月は行あり、9 月は 0 件。
    aug = (
        HEADER + "\n"
        "看護A,看護師,,,,,,,よりより,31,月,山田　太郎,医療保険,精神基本療養費Ⅰ・正看,"
        "09:00,09:35,35,\n"
    )
    stub = _ExportStub(
        None,
        by_month={
            "2026-08": {"success": True, "csv_content": aug, "row_count": 1},
            "2026-09": {"success": True, "csv_content": "", "row_count": 0},
        },
    )
    merged = await export_current_week_csv(
        kaipoke=stub,  # type: ignore[arg-type]
        week_start=_date(2026, 8, 31),
        db=db,
    )
    assert [c["month"] for c in stub.calls] == ["2026-08", "2026-09"]
    assert "山田" in merged
    snap = await get_latest(db, month="2026-08", week_start=_date(2026, 8, 31))
    assert snap is not None and snap.row_count == 1


@pytest.mark.asyncio
async def test_export_current_week_csv_success_path_unchanged(db) -> None:
    stub = _ExportStub({"success": True, "csv_content": GOOD_CSV})
    merged = await export_current_week_csv(
        kaipoke=stub,  # type: ignore[arg-type]
        week_start=date(2026, 8, 3),
        db=db,
    )
    # 8/3 週 (3〜9日) に 3 日の行が入る。
    assert "山田" in merged
    snap = await get_latest(db, month="2026-08", week_start=date(2026, 8, 3))
    assert snap is not None and snap.row_count == 1
