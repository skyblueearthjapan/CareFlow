"""W22 Phase A-1: alembic migration 0025 (patient_fixed_visits.course_template_id 追加) tests.

検証観点:
  1. 0025 リビジョンが alembic スクリプト上で読み込め、down_revision が 0024 を指す
  2. upgrade() が patient_fixed_visits に course_template_id を NULL 可で追加
  3. downgrade() が course_template_id を drop_column する
  4. SQLAlchemy モデル側に UUID NULL 可カラムが宣言されている
  5. upgrade()/downgrade() を実 SQL レベル (SQLite) で往復実行できる
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


def test_migration_0025_revision_chain(alembic_cfg: Config) -> None:
    """0025 が 0024 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0025_v2_pfv_course_template_id")
    assert rev is not None, "0025 リビジョンが見つからない"
    assert rev.down_revision == "0024_v2_patient_requires_multiple_staff", (
        f"0025 の down_revision は 0024 のはず, got {rev.down_revision}"
    )


def test_migration_0025_upgrade_adds_column() -> None:
    """upgrade() が course_template_id を追加し、downgrade() が drop_column する."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0025_v2_pfv_course_template_id.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: add_column with patient_fixed_visits / course_template_id / nullable=True
    assert "add_column" in upgrade_body
    assert '"patient_fixed_visits"' in upgrade_body or "'patient_fixed_visits'" in upgrade_body
    assert "course_template_id" in upgrade_body
    assert "nullable=True" in upgrade_body
    # FK ON DELETE SET NULL
    assert "course_templates" in upgrade_body
    assert 'ondelete="SET NULL"' in upgrade_body or "ondelete='SET NULL'" in upgrade_body

    # downgrade: drop_column
    assert "drop_column" in downgrade_body
    assert "course_template_id" in downgrade_body


def test_migration_0025_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0025 の upgrade()/downgrade() を実 SQL レベルで往復実行する.

    最小スキーマで course_templates / patient_fixed_visits テーブルを SQLite に
    作り、0025 上下で course_template_id カラムが追加・削除されることを確認する。
    """
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0025.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小 course_templates / patient_fixed_visits テーブル
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE course_templates (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL
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

    # 0025 をロード
    migration_path = backend_root / "alembic" / "versions" / "0025_v2_pfv_course_template_id.py"
    spec = importlib.util.spec_from_file_location("migration_0025", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0025"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # 初期状態: course_template_id は無い
    insp = inspect(engine)
    cols_before = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "course_template_id" not in cols_before

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_up = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "course_template_id" in cols_after_up, (
        "upgrade 後に course_template_id カラムが存在するはず"
    )

    # NULL 可かどうか
    col_meta = next(
        c for c in insp.get_columns("patient_fixed_visits") if c["name"] == "course_template_id"
    )
    assert col_meta["nullable"] is True, "course_template_id は NULL 可のはず"

    # 既存行に NULL を許す: INSERT で course_template_id を省略しても通る
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
            sa.text("SELECT course_template_id FROM patient_fixed_visits WHERE id='pfv1'")
        ).all()
        assert len(rows) == 1
        assert rows[0][0] is None, f"course_template_id は省略時 NULL のはず, got {rows[0][0]!r}"

    # downgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_down = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "course_template_id" not in cols_after_down, (
        "downgrade 後に course_template_id カラムが消えているはず"
    )

    engine.dispose()


def test_pfv_model_has_course_template_id_column() -> None:
    """SQLAlchemy モデル側にも course_template_id 列が存在する (NULL 可)."""
    from app.models.patient_fixed_visit import PatientFixedVisit

    cols = {c.name for c in PatientFixedVisit.__table__.columns}
    assert "course_template_id" in cols, (
        f"PatientFixedVisit.__table__ に course_template_id が無い: {cols}"
    )
    col = PatientFixedVisit.__table__.columns["course_template_id"]
    assert col.nullable is True, "course_template_id は NULL 可のはず"
    # FK が定義されている
    fks = list(col.foreign_keys)
    assert len(fks) == 1, f"course_template_id に FK が 1 件あるはず, got {len(fks)}"
    assert fks[0].column.table.name == "course_templates", (
        f"FK の参照先は course_templates のはず, got {fks[0].column.table.name}"
    )
