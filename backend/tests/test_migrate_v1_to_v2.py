"""Tests for ``scripts/migrate_v1_to_v2.py`` (W6-MIG1).

設計仕様書 v0.9 §3.3 / §3.4 / §4.1 に基づく v1 → v2 データ変換の単体テスト。

カバー範囲:

1. **Pure conversion helpers** (DB 非依存)

   - ``convert_weekly_pattern``: v1 dict → v2 entries[] + staff_count
   - ``split_special_week``: 旧 special_week JSONB → 新 2 カラムへ分離

2. **DB driver** (in-memory SQLite + Patient model)

   - dry-run で DB が更新されないこと
   - apply で v1 形式の Patient.weekly_pattern が v2 化されること
   - 既に v2 形式 (entries 持ち) のレコードは no-op
   - special_week → special_weekly_pattern + special_week_active 分離

これらは ``alembic`` には依存せず ``Base.metadata.create_all`` で作る
SQLite メタデータの上で動かす (conftest.py の ``_engine`` fixture を使用)。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.patient import Patient

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _BACKEND_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from migrate_v1_to_v2 import (  # noqa: E402
    convert_weekly_pattern,
    migrate,
    split_special_week,
)

# ---------------------------------------------------------------------------
# Pure conversion helpers
# ---------------------------------------------------------------------------


class TestConvertWeeklyPattern:
    """``convert_weekly_pattern`` の単体テスト (DB 非依存)."""

    def test_converts_v1_dict_to_v2_entries(self) -> None:
        v1 = {
            "frequency_per_week": 2,
            "preferred_weekdays": ["Mon", "Thu"],
            "weekday_priority": "低",
            "service_minutes": 60,
            "time_type": "午前",
            "preferred_start": None,
            "preferred_end": None,
            "ng_weekdays": ["Sat", "Sun"],
        }
        out = convert_weekly_pattern(v1)
        assert out is not None
        # 既存トップレベルキーは温存
        assert out["frequency_per_week"] == 2
        assert out["service_minutes"] == 60
        assert out["time_type"] == "午前"
        # v2 で削除確定キーは drop
        assert "weekday_priority" not in out
        assert "ng_weekdays" not in out
        # entries[] が生成されている
        assert isinstance(out["entries"], list)
        assert len(out["entries"]) == 2
        assert out["entries"][0]["weekday"] == "Mon"
        assert out["entries"][1]["weekday"] == "Thu"
        # staff_count default=1
        for entry in out["entries"]:
            assert entry["staff_count"] == 1
            assert entry["service_minutes"] == 60
            assert entry["time_type"] == "午前"

    def test_staff_count_defaults_to_one(self) -> None:
        """v1 に存在しなかった staff_count は entries の各要素で 1 が付く."""
        v1 = {"preferred_weekdays": ["Wed"], "service_minutes": 30}
        out = convert_weekly_pattern(v1)
        assert out is not None
        assert out["entries"] == [
            {
                "weekday": "Wed",
                "staff_count": 1,
                "service_minutes": 30,
            }
        ]

    def test_already_v2_returns_none(self) -> None:
        """既に entries キーを持つレコードは no-op (二重生成防止)."""
        v2 = {
            "frequency_per_week": 1,
            "preferred_weekdays": ["Mon"],
            "entries": [{"weekday": "Mon", "staff_count": 2, "service_minutes": 60}],
        }
        assert convert_weekly_pattern(v2) is None

    def test_none_or_non_dict_returns_none(self) -> None:
        assert convert_weekly_pattern(None) is None
        assert convert_weekly_pattern("string") is None
        assert convert_weekly_pattern([1, 2, 3]) is None
        assert convert_weekly_pattern(42) is None

    def test_empty_preferred_weekdays_yields_empty_entries(self) -> None:
        v1 = {
            "frequency_per_week": 0,
            "preferred_weekdays": [],
            "weekday_priority": "中",
        }
        out = convert_weekly_pattern(v1)
        assert out is not None
        assert out["entries"] == []
        # 削除キーは entries 空でも drop される
        assert "weekday_priority" not in out
        # 既存キーは温存 (frequency_per_week=0 はそのまま; 別の正規化は別スクリプト)
        assert out["frequency_per_week"] == 0

    def test_invalid_weekdays_filtered_out(self) -> None:
        v1 = {"preferred_weekdays": ["Mon", "BAD", "", None, "Tue", "Mon"]}
        out = convert_weekly_pattern(v1)
        assert out is not None
        # Mon は重複削除、BAD/空文字/None は除外
        weekdays = [e["weekday"] for e in out["entries"]]
        assert weekdays == ["Mon", "Tue"]

    def test_does_not_mutate_input(self) -> None:
        v1 = {
            "preferred_weekdays": ["Mon"],
            "weekday_priority": "高",
            "ng_weekdays": ["Sat"],
        }
        snapshot = {**v1, "preferred_weekdays": list(v1["preferred_weekdays"])}
        _ = convert_weekly_pattern(v1)
        # 入力 dict は破壊しない
        assert v1 == snapshot

    def test_service_minutes_bool_treated_as_missing(self) -> None:
        # bool は int の subclass。誤って 1/0 を service_minutes に流さない。
        v1 = {"preferred_weekdays": ["Mon"], "service_minutes": True}
        out = convert_weekly_pattern(v1)
        assert out is not None
        assert "service_minutes" not in out["entries"][0]


class TestSplitSpecialWeek:
    """``split_special_week`` の単体テスト (DB 非依存)."""

    def test_separates_pattern_and_weeks(self) -> None:
        legacy = {
            "pattern": {
                "preferred_weekdays": ["Mon", "Wed", "Fri"],
                "service_minutes": 45,
                "time_type": "固定",
                "preferred_start": "09:00",
                "preferred_end": "09:45",
                "weekday_priority": "高",
            },
            "weeks": [
                {"iso_year": 2026, "iso_week": 18},
                {"iso_year": 2026, "iso_week": 19},
            ],
        }
        sp_pattern, active = split_special_week(legacy)

        assert sp_pattern is not None
        # 旧 pattern が v2 (entries[]) に変換されている
        assert isinstance(sp_pattern["entries"], list)
        assert len(sp_pattern["entries"]) == 3
        assert "weekday_priority" not in sp_pattern
        # 適用週リストが正しくコピーされている
        assert active == [
            {"iso_year": 2026, "iso_week": 18},
            {"iso_year": 2026, "iso_week": 19},
        ]

    def test_none_or_non_dict_returns_empty(self) -> None:
        assert split_special_week(None) == (None, [])
        assert split_special_week("not-a-dict") == (None, [])
        assert split_special_week(42) == (None, [])
        assert split_special_week([{"iso_year": 2026, "iso_week": 1}]) == (None, [])

    def test_dedupes_and_validates_weeks(self) -> None:
        legacy = {
            "weeks": [
                {"iso_year": 2026, "iso_week": 18},
                {"iso_year": 2026, "iso_week": 18},  # 重複
                {"iso_year": 2026, "iso_week": 0},  # iso_week 不正
                {"iso_year": 1900, "iso_week": 5},  # iso_year 範囲外
                {"iso_year": "abc", "iso_week": 1},  # cast 失敗
                {"iso_year": 2026},  # iso_week 欠落
                "garbage",
                {"iso_year": 2027, "iso_week": 1},
            ]
        }
        _, active = split_special_week(legacy)
        assert active == [
            {"iso_year": 2026, "iso_week": 18},
            {"iso_year": 2027, "iso_week": 1},
        ]

    def test_pattern_already_v2_is_kept(self) -> None:
        legacy = {
            "pattern": {
                "frequency_per_week": 5,
                "preferred_weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "entries": [
                    {"weekday": "Mon", "staff_count": 1},
                ],
            },
            "weeks": [{"iso_year": 2026, "iso_week": 20}],
        }
        sp_pattern, active = split_special_week(legacy)
        assert sp_pattern is not None
        # 既に v2 (entries 持ち) のため、entries はそのまま保持される
        assert sp_pattern["entries"] == [{"weekday": "Mon", "staff_count": 1}]
        assert active == [{"iso_year": 2026, "iso_week": 20}]


# ---------------------------------------------------------------------------
# DB driver tests (require in-memory SQLite via conftest._engine)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_persist_changes(_engine, db) -> None:
    """``--dry-run`` (apply=False) は DB に変更を残さない."""
    patient = Patient(
        id=uuid.uuid4(),
        code="P-DRY-001",
        name="Dry Run Patient",
        status="active",
        weekly_pattern={
            "frequency_per_week": 2,
            "preferred_weekdays": ["Mon", "Wed"],
            "weekday_priority": "低",
            "service_minutes": 60,
            "time_type": "午前",
            "preferred_start": None,
            "preferred_end": None,
            "ng_weekdays": [],
        },
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=False)

    assert summary.scanned == 1
    assert summary.weekly_pattern_converted == 1
    assert summary.rows_changed == 1

    # 別セッションで読み直して、dry-run が DB に書いていないことを確認。
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        loaded = (await fresh.scalars(select(Patient).where(Patient.code == "P-DRY-001"))).one()
        wp = loaded.weekly_pattern
        assert wp is not None
        assert "entries" not in wp  # 旧 v1 のまま
        assert wp["weekday_priority"] == "低"


@pytest.mark.asyncio
async def test_apply_converts_weekly_pattern(_engine, db) -> None:
    """``--apply`` で v1 形式の weekly_pattern が v2 形式に書き換えられる."""
    patient = Patient(
        id=uuid.uuid4(),
        code="P-APPLY-001",
        name="Apply Patient",
        status="active",
        weekly_pattern={
            "frequency_per_week": 1,
            "preferred_weekdays": ["Tue"],
            "weekday_priority": "高",
            "service_minutes": 30,
            "time_type": "時間帯",
            "preferred_start": "09:00",
            "preferred_end": "10:00",
            "ng_weekdays": ["Sun"],
        },
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=True)
    assert summary.weekly_pattern_converted == 1
    assert summary.rows_changed == 1

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        loaded = (await fresh.scalars(select(Patient).where(Patient.code == "P-APPLY-001"))).one()
        wp = loaded.weekly_pattern
        assert wp is not None
        assert isinstance(wp["entries"], list)
        assert len(wp["entries"]) == 1
        entry = wp["entries"][0]
        assert entry["weekday"] == "Tue"
        assert entry["staff_count"] == 1
        assert entry["preferred_start"] == "09:00"
        assert entry["preferred_end"] == "10:00"
        # 削除キーは drop されている
        assert "weekday_priority" not in wp
        assert "ng_weekdays" not in wp


@pytest.mark.asyncio
async def test_apply_splits_special_week(_engine, db) -> None:
    """``special_week`` が ``special_weekly_pattern`` + ``special_week_active`` に分離."""
    patient = Patient(
        id=uuid.uuid4(),
        code="P-SP-001",
        name="Special Week Patient",
        status="active",
        weekly_pattern={
            "preferred_weekdays": ["Mon"],
            "weekday_priority": "中",
        },
        special_week={
            "pattern": {
                "preferred_weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "service_minutes": 45,
                "time_type": "固定",
                "preferred_start": "13:00",
                "preferred_end": "13:45",
            },
            "weeks": [
                {"iso_year": 2026, "iso_week": 18},
                {"iso_year": 2026, "iso_week": 19},
            ],
        },
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=True)
    assert summary.special_weekly_pattern_set == 1
    assert summary.special_week_active_set == 1

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        loaded = (await fresh.scalars(select(Patient).where(Patient.code == "P-SP-001"))).one()
        # special_weekly_pattern が v2 形式 (entries[]) に
        assert loaded.special_weekly_pattern is not None
        sp = loaded.special_weekly_pattern
        assert isinstance(sp["entries"], list)
        assert len(sp["entries"]) == 5
        for entry in sp["entries"]:
            assert entry["staff_count"] == 1
            assert entry["service_minutes"] == 45
            assert entry["preferred_start"] == "13:00"
        # special_week_active が適用週リストに
        assert loaded.special_week_active == [
            {"iso_year": 2026, "iso_week": 18},
            {"iso_year": 2026, "iso_week": 19},
        ]


@pytest.mark.asyncio
async def test_apply_idempotent_on_already_v2_rows(_engine, db) -> None:
    """既に v2 形式のレコードは entries が二重生成されない (no-op)."""
    patient = Patient(
        id=uuid.uuid4(),
        code="P-V2-001",
        name="Already V2",
        status="active",
        weekly_pattern={
            "frequency_per_week": 1,
            "preferred_weekdays": ["Mon"],
            "entries": [{"weekday": "Mon", "staff_count": 2, "service_minutes": 60}],
        },
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=True)
    assert summary.weekly_pattern_converted == 0
    assert summary.weekly_pattern_already_v2 == 1
    assert summary.rows_changed == 0

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        loaded = (await fresh.scalars(select(Patient).where(Patient.code == "P-V2-001"))).one()
        wp = loaded.weekly_pattern
        assert wp is not None
        # entries の中身がそのまま (staff_count=2 を上書きしない)
        assert wp["entries"] == [{"weekday": "Mon", "staff_count": 2, "service_minutes": 60}]


@pytest.mark.asyncio
async def test_apply_keeps_existing_special_weekly_pattern(_engine, db) -> None:
    """既に special_weekly_pattern が non-null のレコードは上書きしない (冪等)."""
    existing_v2_pattern = {
        "preferred_weekdays": ["Mon"],
        "entries": [{"weekday": "Mon", "staff_count": 2}],
    }
    patient = Patient(
        id=uuid.uuid4(),
        code="P-KEEP-001",
        name="Keep Existing",
        status="active",
        weekly_pattern={"preferred_weekdays": ["Mon"]},
        special_week={
            "pattern": {"preferred_weekdays": ["Tue"]},  # 旧 v1
            "weeks": [{"iso_year": 2026, "iso_week": 30}],
        },
        special_weekly_pattern=existing_v2_pattern,  # 既に新しい値
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=True)
    # 旧 special_week.pattern を新カラムに上書きせず、既存値を尊重
    assert summary.special_weekly_pattern_kept_existing == 1
    assert summary.special_weekly_pattern_set == 0

    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as fresh:
        loaded = (await fresh.scalars(select(Patient).where(Patient.code == "P-KEEP-001"))).one()
        assert loaded.special_weekly_pattern == existing_v2_pattern


@pytest.mark.asyncio
async def test_skips_soft_deleted_patients(_engine, db) -> None:
    """``deleted_at`` 付きの患者はスキャン対象外."""
    from datetime import UTC, datetime

    patient = Patient(
        id=uuid.uuid4(),
        code="P-DEL-001",
        name="Deleted",
        status="active",
        deleted_at=datetime.now(UTC),
        weekly_pattern={"preferred_weekdays": ["Mon"]},
    )
    db.add(patient)
    await db.commit()

    summary = await migrate(apply=True)
    assert summary.scanned == 0
    assert summary.rows_changed == 0
