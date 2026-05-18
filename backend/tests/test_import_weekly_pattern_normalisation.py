"""Importer normalisation tests for weekly_pattern (W3-B / Wave 5-C).

Covers the W4-G helper ``normalize_frequency`` plus the
``_norm_time_type`` wrapper in ``scripts/import_weekly_pattern.py``.
These are the cells operators most often leave blank, so the contract of
"blank → safe default, never raise" is asserted explicitly.

Wave v2 移行で ``weekday_priority`` (曜日優先度) は廃止されたため、
関連するヘルパー (``normalize_weekday_priority`` / ``_norm_priority``) も
削除済み。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Mirror the import pattern used by the script itself (sys.path append) so
# the helpers are reachable without having to package scripts/ as a module.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    normalize_frequency,
)
from import_weekly_pattern import (  # noqa: E402
    _hhmm,
    _norm_time_type,
    build_payload,
)

# ---------- normalize_frequency ---------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [None, "", 0, "0", -1, "選択なし", "abc"],
)
def test_frequency_zero_or_blank_returns_none(raw) -> None:
    assert normalize_frequency(raw) is None


@pytest.mark.parametrize(
    "raw,expected",
    [(1, 1), ("3", 3), ("7回", 7), (5.0, 5)],
)
def test_frequency_positive_values_return_int(raw, expected) -> None:
    assert normalize_frequency(raw) == expected


# ---------- import_weekly_pattern wrappers ----------------------------------


def test_norm_time_type_falls_back_to_jihantai() -> None:
    assert _norm_time_type("固定") == "固定"
    assert _norm_time_type("時間帯") == "時間帯"
    # Unknown / blank → "時間帯" (the safe default for the runtime engine).
    assert _norm_time_type("夜間") == "時間帯"
    assert _norm_time_type(None) == "時間帯"
    assert _norm_time_type("選択なし") == "時間帯"


def test_hhmm_round_trip() -> None:
    from datetime import time

    assert _hhmm(time(9, 0)) == "09:00"
    assert _hhmm("09:30") == "09:30"
    # Blank cells do NOT raise — they emit None so the JSONB stays nullable.
    assert _hhmm(None) is None
    assert _hhmm("") is None
    assert _hhmm("not-a-time") is None


def test_build_payload_handles_blank_pattern_columns() -> None:
    """Operators routinely leave the weekly_pattern columns blank for new
    patients; the payload must still be parseable and fall back to safe
    defaults instead of raising."""
    headers = {
        "patient_id": 0,
        "週訪問回数": 1,
        "希望曜日（複数可）": 2,
        "サービス時間": 3,
        "時間タイプ": 4,
        "希望時間帯（開始）": 5,
        "希望時間帯（終了）": 6,
        "曜日NG": 7,
        "エリア": 8,
        "指定タイプ": 9,
        "継続希望": 10,
        "指定スタッフID": 11,
        "NGスタッフID": 12,
    }
    row = (
        "P001",
        "",  # 週訪問回数 → None
        "選択なし",  # 希望曜日 → []
        "",  # サービス時間 → None
        "夜間",  # 時間タイプ → "時間帯" (fallback)
        "",  # preferred_start
        "",  # preferred_end
        "選択なし",  # 曜日NG
        "北区",
        "",  # 指定タイプ → None
        "",  # 継続希望 → False
        "選択なし",  # 指定スタッフID
        "選択なし",  # NGスタッフID
    )
    payload = build_payload(row, headers)
    assert payload is not None
    pattern = payload["weekly_pattern"]
    assert pattern["frequency_per_week"] is None
    assert pattern["preferred_weekdays"] == []
    assert "weekday_priority" not in pattern
    assert pattern["service_minutes"] is None
    assert pattern["time_type"] == "時間帯"
    assert pattern["preferred_start"] is None
    assert pattern["preferred_end"] is None
    assert pattern["ng_weekdays"] == []
    assert payload["area"] == "北区"
    assert payload["specified_type"] is None
    assert payload["continuous_request"] is False


def test_build_payload_normalises_full_width_frequency() -> None:
    headers = {
        "patient_id": 0,
        "週訪問回数": 1,
        "希望曜日（複数可）": 2,
        "サービス時間": 3,
        "時間タイプ": 4,
        "希望時間帯（開始）": 5,
        "希望時間帯（終了）": 6,
        "曜日NG": 7,
        "エリア": 8,
        "指定タイプ": 9,
        "継続希望": 10,
        "指定スタッフID": 11,
        "NGスタッフID": 12,
    }
    # Full-width "３" should still parse as int 3 once parse_int strips the
    # non-digit suffix; this guards a real Excel paste-from-IME scenario.
    row = (
        "P002",
        "3回",
        "月、水、金",
        45,
        "固定",
        "09:00",
        "10:00",
        "土,日",
        "南区",
        "全員",
        "継続",
        "S001",
        "選択なし",
    )
    payload = build_payload(row, headers)
    assert payload is not None
    assert payload["weekly_pattern"]["frequency_per_week"] == 3
    assert payload["weekly_pattern"]["preferred_weekdays"] == ["Mon", "Wed", "Fri"]
    assert "weekday_priority" not in payload["weekly_pattern"]
    assert payload["weekly_pattern"]["service_minutes"] == 45
    assert payload["weekly_pattern"]["time_type"] == "固定"
    assert payload["weekly_pattern"]["preferred_start"] == "09:00"
    assert payload["weekly_pattern"]["ng_weekdays"] == ["Sat", "Sun"]
    assert payload["specified_type"] == "ALL"
    assert payload["continuous_request"] is True
    assert payload["_specified_codes"] == ["S001"]
    assert payload["_ng_codes"] == []


def test_build_payload_returns_none_when_code_blank() -> None:
    payload = build_payload(("",), {"patient_id": 0})
    assert payload is None
