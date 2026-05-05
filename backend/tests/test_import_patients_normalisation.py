"""Importer normalisation tests for patient master rows (W3-B / Wave 5-C).

These exercise the pure-function helpers used by ``scripts/import_patients.py``
without touching the DB. We focus on the rough edges that bit us in
production: blank strings, NaN-style cells, full-width digits, and the
``"選択なし"`` sentinel that operators routinely paste from the source sheet.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

# scripts/ is not a packaged module (no __init__.py); add it to sys.path the
# same way the importers do at runtime so we can call the helpers directly.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _import_utils import (  # noqa: E402
    clean_str,
    is_blank,
    normalize_sex,
    parse_areas,
    parse_bool,
    parse_float,
    parse_id_list,
    parse_int,
    parse_weekdays,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("選択なし", True),
        ("空白", True),
        ("なし", True),
        ("None", True),
        ("活動中", False),
    ],
)
def test_is_blank_recognises_sentinels(raw, expected) -> None:
    assert is_blank(raw) is expected


def test_clean_str_trims_and_blanks_to_none() -> None:
    assert clean_str("  山田太郎  ") == "山田太郎"
    assert clean_str("選択なし") is None
    # Numeric cells (Excel often returns int / float) are coerced to str.
    assert clean_str(123) == "123"


def test_normalize_sex_handles_synonyms() -> None:
    assert normalize_sex("男") == "male"
    assert normalize_sex("女性") == "female"
    assert normalize_sex("M") == "male"
    # Unknown labels return None rather than raising — importers tolerate
    # garbage and log a single line per skipped row.
    assert normalize_sex("その他") is None
    assert normalize_sex("選択なし") is None


def test_parse_int_strips_units_and_full_width() -> None:
    assert parse_int(" 5 人 ") == 5
    assert parse_int("12件") == 12
    # Empty / blank tokens collapse to None (NOT 0) so optional ints stay
    # NULL in the DB.
    assert parse_int("") is None
    assert parse_int(None) is None
    # Booleans become 0/1.
    assert parse_int(True) == 1


def test_parse_float_handles_floats_and_blank() -> None:
    assert parse_float("35.6895") == pytest.approx(35.6895)
    assert parse_float(139) == pytest.approx(139.0)
    assert parse_float("") is None
    assert parse_float(None) is None


def test_parse_float_treats_nan_as_value() -> None:
    """``parse_float`` returns NaN for the literal float; callers must guard.

    This documents the current contract — the importer relies on the caller
    (``import_patients.build_payload``) to drop NaN before persisting.
    """
    result = parse_float(float("nan"))
    assert result is not None
    assert math.isnan(result)


def test_parse_bool_japanese_synonyms() -> None:
    assert parse_bool("継続") is True
    assert parse_bool("あり") is True
    assert parse_bool("○") is True
    assert parse_bool("なし") is False
    assert parse_bool("×") is False
    # Default applies on unrecognised input.
    assert parse_bool("謎", default=True) is True
    assert parse_bool("謎", default=False) is False


def test_parse_id_list_handles_japanese_and_blank() -> None:
    assert parse_id_list("S001,S003") == ["S001", "S003"]
    assert parse_id_list("S001、S003") == ["S001", "S003"]
    assert parse_id_list("選択なし") == []
    assert parse_id_list(None) == []


def test_parse_weekdays_accepts_jp_and_ascii() -> None:
    assert parse_weekdays("Mon, Wed, Fri") == ["Mon", "Wed", "Fri"]
    assert parse_weekdays("月、水、金") == ["Mon", "Wed", "Fri"]
    # Empty / unknown tokens are dropped silently.
    assert parse_weekdays("Monday,火") == ["Tue"]
    assert parse_weekdays("") == []


def test_parse_areas_strips_and_splits() -> None:
    assert parse_areas("北区, 南区 ,東区") == ["北区", "南区", "東区"]
    assert parse_areas("選択なし") == []
