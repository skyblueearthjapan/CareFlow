"""alembic migration 0089 (別の事業所へ提供する準備: 設定化) のテスト.

検証観点:
  1. 0089 が 0088 から派生し、head が単一。revision id は 32 文字以内。
  2. SQLite で upgrade → business_profile ができ、checkin_settings に 4 列 (CHECK 付き)。
  3. データ: 今までコードにあった値 (90 / 30 / 7 / 60・よりより様の QR カードの値・
     INAGE→稲/1・TSUGA→津/2) がそのまま入る。値の入っている拠点には触れない。
  4. よりより様の拠点 (INAGE / TSUGA) が無い DB には事業所の情報を入れない。
  5. downgrade → 再 upgrade で往復できる (シングルトン部分 UNIQUE も残る)。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

_CREATE_CHECKIN_SETTINGS = """
CREATE TABLE checkin_settings (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    is_singleton BOOLEAN NOT NULL DEFAULT 1,
    match_m INTEGER,
    review_m INTEGER,
    accuracy_m INTEGER,
    no_show_grace_min INTEGER,
    late_min INTEGER,
    max_inprogress_min INTEGER
        CONSTRAINT ck_checkin_settings_max_inprogress_range
        CHECK (max_inprogress_min IS NULL OR
               (max_inprogress_min >= 30 AND max_inprogress_min <= 1440)),
    created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP)
)
"""
_CREATE_SINGLETON_INDEX = (
    "CREATE UNIQUE INDEX uq_checkin_settings_singleton "
    "ON checkin_settings (is_singleton) WHERE is_singleton = 1"
)
_CREATE_OFFICES = """
CREATE TABLE offices (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    code VARCHAR(32),
    sort_order INTEGER,
    short_label VARCHAR(8),
    deleted_at DATETIME
)
"""

_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _load_migration_module() -> object:
    path = _BACKEND_ROOT / "alembic" / "versions" / "0089_multi_office_settings.py"
    spec = importlib.util.spec_from_file_location("migration_0089", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0089"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run(engine: sa.Engine, fn_name: str) -> None:
    mod = _load_migration_module()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def _prepare(tmp_path: Path, offices: list[tuple[str, str, str | None, str | None, int | None]]):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0089.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(_CREATE_CHECKIN_SETTINGS))
        conn.execute(sa.text(_CREATE_SINGLETON_INDEX))
        conn.execute(sa.text(_CREATE_OFFICES))
        for oid, name, code, short, order in offices:
            conn.execute(
                sa.text(
                    "INSERT INTO offices (id, name, code, short_label, sort_order) "
                    "VALUES (:id, :name, :code, :short, :order)"
                ),
                {"id": oid, "name": name, "code": code, "short": short, "order": order},
            )
    return engine


def test_migration_0089_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0089_multi_office_settings")
    assert rev is not None
    assert rev.down_revision == "0088_visit_time_adjustments"
    assert len("0089_multi_office_settings") <= 32
    assert len(list(script.get_heads())) == 1


def test_migration_0089_seeds_current_values_for_current_client(tmp_path: Path) -> None:
    # 0059 は拠点名で略称を入れたので、名前が違う拠点は略称・並び順が空のまま残りうる。
    # 値の入っている拠点 (稲毛) は触らず、空の拠点 (都賀) だけ拠点コードで埋める。
    engine = _prepare(
        tmp_path,
        [
            ("o1", "稲毛", "INAGE", "稲", 1),
            ("o2", "都賀支店", "TSUGA", None, None),
            ("o3", "幕張", "MAKUHARI", None, None),
        ],
    )
    # 既存のしきい値の行 (他の列の値は変えない)。
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO checkin_settings (id, is_singleton, match_m) VALUES ('c1', 1, 120)"
            )
        )

    _run(engine, "upgrade")

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("checkin_settings")}
    assert {
        "arrival_max_back_min",
        "departure_max_ahead_min",
        "staff_adjust_window_days",
        "unplanned_default_minutes",
    } <= cols
    assert "uq_checkin_settings_singleton" in {
        ix["name"] for ix in insp.get_indexes("checkin_settings")
    }
    assert "uq_business_profile_singleton" in {
        ix["name"] for ix in insp.get_indexes("business_profile")
    }

    with engine.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT match_m, arrival_max_back_min, departure_max_ahead_min, "
                "staff_adjust_window_days, unplanned_default_minutes FROM checkin_settings"
            )
        ).one()
        assert tuple(row) == (120, 90, 30, 7, 60)

        profile = conn.execute(
            sa.text(
                "SELECT station_name, contact_tel, contact_hours, contact_days, logo_url "
                "FROM business_profile"
            )
        ).all()
        assert [tuple(p) for p in profile] == [
            (
                "訪問看護ステーション よりより",
                "043-215-8991",
                "9:00〜18:00",
                "日曜・年末年始休暇を除く",
                "/brand/yoriyori-logo-h.svg",
            )
        ]

        offices = {
            r.code: (r.short_label, r.sort_order)
            for r in conn.execute(sa.text("SELECT code, short_label, sort_order FROM offices"))
        }
        assert offices == {
            "INAGE": ("稲", 1),
            "TSUGA": ("津", 2),
            # 今までコードに無かった拠点には何も入れない (拠点名の 1 文字目に落ちる)。
            "MAKUHARI": (None, None),
        }

    # 範囲外は CHECK で拒否。
    for column, bad in (
        ("arrival_max_back_min", 9),
        ("departure_max_ahead_min", 181),
        ("staff_adjust_window_days", 32),
        ("unplanned_default_minutes", 241),
    ):
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        f"INSERT INTO checkin_settings (id, is_singleton, {column}) "
                        f"VALUES ('bad-{column}', 0, {bad})"
                    )
                )

    # downgrade → 列とテーブルが消え、部分 UNIQUE は残る。
    _run(engine, "downgrade")
    insp2 = inspect(engine)
    cols2 = {c["name"] for c in insp2.get_columns("checkin_settings")}
    assert "arrival_max_back_min" not in cols2
    assert "max_inprogress_min" in cols2
    assert "business_profile" not in insp2.get_table_names()
    assert "uq_checkin_settings_singleton" in {
        ix["name"] for ix in insp2.get_indexes("checkin_settings")
    }

    # 再 upgrade (往復)。
    _run(engine, "upgrade")
    assert "business_profile" in inspect(engine).get_table_names()
    engine.dispose()


def test_migration_0089_no_row_inserts_singleton_and_skips_profile_for_other_client(
    tmp_path: Path,
) -> None:
    # 別の事業所の新しい DB (よりより様の拠点コードが無い・しきい値の行も無い)。
    engine = _prepare(tmp_path, [("o1", "本店", "HQ", None, None)])

    _run(engine, "upgrade")

    with engine.begin() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT is_singleton, match_m, arrival_max_back_min, departure_max_ahead_min, "
                "staff_adjust_window_days, unplanned_default_minutes FROM checkin_settings"
            )
        ).all()
        assert [tuple(r) for r in rows] == [(1, None, 90, 30, 7, 60)]
        # よりより様の電話番号やロゴを別の事業所のカードに出さない。
        assert conn.execute(sa.text("SELECT COUNT(*) FROM business_profile")).scalar() == 0
        office = conn.execute(sa.text("SELECT short_label, sort_order FROM offices")).one()
        assert tuple(office) == (None, None)
    engine.dispose()
