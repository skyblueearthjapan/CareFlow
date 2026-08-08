"""QR 訪問チェックイン Phase 4: alembic migration 0043 (max_inprogress_min) tests.

検証観点:
  1. 0043 リビジョンが読み込め、down_revision が 0042 を指す (= 単一 head 鎖).
  2. upgrade() で checkin_settings に max_inprogress_min 列が追加される.
  3. 範囲 CHECK (30..1440) が実 SQL レベル (SQLite) で機能する. NULL は許容.
  4. シングルトン部分 UNIQUE index が upgrade 後も維持される (= テーブル再構築なし).
  5. downgrade() で列が drop され、再 upgrade で復元できる (往復).
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

# 0041 が SQLite に作る checkin_settings の最小再現 (シングルトン部分 UNIQUE 込み)。
# 0043 は ALTER のため前提テーブルが要る (0039 の標準テーブル作成テストと異なる)。
_CREATE_CHECKIN_SETTINGS = """
CREATE TABLE checkin_settings (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    is_singleton BOOLEAN NOT NULL DEFAULT 1,
    match_m INTEGER,
    review_m INTEGER,
    accuracy_m INTEGER,
    no_show_grace_min INTEGER,
    late_min INTEGER,
    created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    updated_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
    CONSTRAINT ck_checkin_settings_match_m_range
        CHECK (match_m IS NULL OR (match_m >= 10 AND match_m <= 1000))
)
"""
_CREATE_SINGLETON_INDEX = (
    "CREATE UNIQUE INDEX uq_checkin_settings_singleton "
    "ON checkin_settings (is_singleton) WHERE is_singleton = 1"
)


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / "0043_checkin_max_inprogress.py"
    spec = importlib.util.spec_from_file_location("migration_0043", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0043"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_0043_revision_chain(alembic_cfg: Config) -> None:
    """0043 が 0042 から派生し、head が単一であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0043_checkin_max_inprogress")
    assert rev is not None
    assert rev.down_revision == "0042_revoked_qr_tokens", (
        f"0043 の down_revision は 0042 のはず, got {rev.down_revision}"
    )
    # head は単一 (線形 chain) であること。後続 migration (0044, 0045, ...) が
    # 増えても単一 head であれば良いため、特定リビジョン名には固定しない。
    heads = list(script.get_heads())
    assert len(heads) == 1, f"head は単一のはず, got {heads}"


def test_migration_0043_sqlite_roundtrip(tmp_path: Path) -> None:
    """SQLite で upgrade / downgrade を往復し、列 + CHECK + 部分 index を確認."""
    db_path = tmp_path / "migration_0043.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # 前提: 0041 相当の checkin_settings を作る。
    with engine.begin() as conn:
        conn.execute(sa.text(_CREATE_CHECKIN_SETTINGS))
        conn.execute(sa.text(_CREATE_SINGLETON_INDEX))

    migration_mod = _load_migration_module()

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("checkin_settings")}
    assert "max_inprogress_min" in cols, f"列が無い: {cols}"

    # 部分 UNIQUE index がテーブル再構築で失われていないこと。
    idx_names = {ix["name"] for ix in insp.get_indexes("checkin_settings")}
    assert "uq_checkin_settings_singleton" in idx_names

    # NULL は許容。
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO checkin_settings (id, is_singleton) VALUES ('c1', 1)"))
    # 範囲内 (240) は許容。
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO checkin_settings (id, is_singleton, max_inprogress_min) "
                "VALUES ('c2', 0, 240)"
            )
        )
    # 範囲外 (29 / 1441) は CHECK で拒否。
    for bad in (29, 1441):
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO checkin_settings "
                        "(id, is_singleton, max_inprogress_min) "
                        f"VALUES ('bad{bad}', 0, {bad})"
                    )
                )

    # downgrade: 列 drop
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]
    insp2 = inspect(engine)
    cols2 = {c["name"] for c in insp2.get_columns("checkin_settings")}
    assert "max_inprogress_min" not in cols2

    # 再 upgrade で復元できる (= 冪等な往復)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]
    insp3 = inspect(engine)
    cols3 = {c["name"] for c in insp3.get_columns("checkin_settings")}
    assert "max_inprogress_min" in cols3

    engine.dispose()
