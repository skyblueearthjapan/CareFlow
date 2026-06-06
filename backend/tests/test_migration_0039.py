"""Phase G-88 Step2: alembic migration 0039 (scheduling_settings) tests.

検証観点:
  1. 0039 リビジョンが読み込め、down_revision が 0038 を指す (= 単一 head 鎖).
  2. upgrade() で scheduling_settings テーブル + 部分 UNIQUE index が作られる.
  3. CHECK 制約 (範囲 / 時刻順) が実 SQL レベル (SQLite) で機能する.
  4. downgrade() でテーブルが drop され、再 upgrade で復元できる (往復).
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


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / "0039_scheduling_settings.py"
    spec = importlib.util.spec_from_file_location("migration_0039", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0039"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_0039_revision_chain(alembic_cfg: Config) -> None:
    """0039 が 0038 から派生し、head が単一であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0039_scheduling_settings")
    assert rev is not None
    assert rev.down_revision == "0038_office_operating_weekdays", (
        f"0039 の down_revision は 0038 のはず, got {rev.down_revision}"
    )
    heads = list(script.get_heads())
    assert heads == ["0039_scheduling_settings"], f"head は単一のはず, got {heads}"


def test_migration_0039_sqlite_roundtrip(tmp_path: Path) -> None:
    """SQLite で upgrade / downgrade を往復し、テーブル + CHECK を確認."""
    db_path = tmp_path / "migration_0039.db"
    engine = create_engine(f"sqlite:///{db_path}")

    migration_mod = _load_migration_module()

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    assert "scheduling_settings" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("scheduling_settings")}
    for expected in (
        "id",
        "is_singleton",
        "visit_buffer_min",
        "travel_speed_kmh",
        "lunch_duration_min",
        "lunch_window_start",
        "lunch_window_end",
        "business_start",
        "business_end",
        "max_patients_per_course",
        "created_at",
        "updated_at",
    ):
        assert expected in cols, f"列 {expected} が無い: {cols}"

    # 部分 UNIQUE index が存在
    idx_names = {ix["name"] for ix in insp.get_indexes("scheduling_settings")}
    assert "uq_scheduling_settings_singleton" in idx_names

    # 正常 INSERT (全 NULL 設定列 + is_singleton=1)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO scheduling_settings (id, is_singleton) VALUES ('s1', 1)"))

    # 範囲外 buffer は CHECK で拒否
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO scheduling_settings (id, is_singleton, visit_buffer_min) "
                    "VALUES ('s2', 0, 31)"
                )
            )

    # 時刻順 CHECK 違反 (business_start >= business_end) は拒否
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO scheduling_settings "
                    "(id, is_singleton, business_start, business_end) "
                    "VALUES ('s3', 0, '18:00:00', '09:30:00')"
                )
            )

    # 部分 UNIQUE: is_singleton=1 の 2 行目は拒否
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO scheduling_settings (id, is_singleton) VALUES ('s4', 1)")
            )

    # downgrade: テーブル drop
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp2 = inspect(engine)
    assert "scheduling_settings" not in insp2.get_table_names()

    # 再 upgrade で復元できる (= 冪等な往復)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]
    insp3 = inspect(engine)
    assert "scheduling_settings" in insp3.get_table_names()

    engine.dispose()
