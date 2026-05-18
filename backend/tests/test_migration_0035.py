"""Phase E-5 (項目 ⑥B): alembic migration 0035 tests.

検証観点:
  1. 0035 リビジョンが alembic スクリプト上で読み込め、down_revision が 0034 を指す.
  2. upgrade() が patient_fixed_visits に sub_office_id を NULL 可で追加.
  3. downgrade() が sub_office_id を drop_column する.
  4. SQLAlchemy モデル側に UUID NULL 可カラムが宣言されている.
  5. upgrade()/downgrade() を実 SQL レベル (SQLite) で往復実行できる.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def test_migration_0035_revision_chain(alembic_cfg: Config) -> None:
    """0035 が 0034 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0035_pfv_sub_office")
    assert rev is not None, "0035 リビジョンが見つからない"
    assert rev.down_revision == "0034_courses_code_check_m_overflow", (
        f"0035 の down_revision は 0034 のはず, got {rev.down_revision}"
    )


def test_migration_0035_upgrade_adds_column() -> None:
    """upgrade() が sub_office_id を追加し、downgrade() が drop_column する."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0035_pfv_sub_office.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: add_column with patient_fixed_visits / sub_office_id / nullable=True
    assert "add_column" in upgrade_body
    assert '"patient_fixed_visits"' in upgrade_body or "'patient_fixed_visits'" in upgrade_body
    assert "sub_office_id" in upgrade_body
    assert "nullable=True" in upgrade_body
    # FK 関連
    assert "offices" in upgrade_body
    assert "fk_pfv_sub_office_id" in upgrade_body

    # downgrade: drop_column
    assert "drop_column" in downgrade_body
    assert "sub_office_id" in downgrade_body


def test_migration_0035_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0035 の upgrade()/downgrade() を実 SQL レベルで往復実行する."""
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0035.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小 offices / patient_fixed_visits テーブル
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE offices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE patient_fixed_visits (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    weekday INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    duration_min INTEGER NOT NULL DEFAULT 30
                )
                """
            )
        )

    # 0035 をロード
    migration_path = backend_root / "alembic" / "versions" / "0035_pfv_sub_office.py"
    spec = importlib.util.spec_from_file_location("migration_0035", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0035"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # 初期状態: sub_office_id は無い
    insp = inspect(engine)
    cols_before = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "sub_office_id" not in cols_before

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_up = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "sub_office_id" in cols_after_up, "upgrade 後に sub_office_id カラムが存在するはず"

    # NULL 可かどうか
    col_meta = next(
        c for c in insp.get_columns("patient_fixed_visits") if c["name"] == "sub_office_id"
    )
    assert col_meta["nullable"] is True, "sub_office_id は NULL 可のはず"

    # 既存行に NULL を許す
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO patient_fixed_visits
                  (id, patient_id, mode, weekday, start_time)
                VALUES ('pfv1', 'p1', 'normal', 0, '09:00:00')
                """
            )
        )
        rows = conn.execute(
            sa.text("SELECT sub_office_id FROM patient_fixed_visits WHERE id='pfv1'")
        ).all()
        assert len(rows) == 1
        assert rows[0][0] is None

    # downgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_down = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "sub_office_id" not in cols_after_down

    engine.dispose()


def test_pfv_model_has_sub_office_id_column() -> None:
    """SQLAlchemy モデル側にも sub_office_id 列が存在する (NULL 可)."""
    from app.models.patient_fixed_visit import PatientFixedVisit

    cols = {c.name for c in PatientFixedVisit.__table__.columns}
    assert "sub_office_id" in cols, f"PatientFixedVisit.__table__ に sub_office_id が無い: {cols}"
    col = PatientFixedVisit.__table__.columns["sub_office_id"]
    assert col.nullable is True, "sub_office_id は NULL 可のはず"
    fks = list(col.foreign_keys)
    assert len(fks) == 1, f"sub_office_id に FK が 1 件あるはず, got {len(fks)}"
    assert fks[0].column.table.name == "offices", (
        f"FK の参照先は offices のはず, got {fks[0].column.table.name}"
    )
