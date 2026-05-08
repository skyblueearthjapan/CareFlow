"""W18 Phase A-1: alembic migration 0024 (patients.requires_multiple_staff 追加) tests.

検証観点:
  1. 0024 リビジョンが alembic スクリプト上で読み込め、down_revision が 0023 を指す
  2. upgrade() が patients に requires_multiple_staff を NOT NULL DEFAULT FALSE で追加
  3. downgrade() が requires_multiple_staff を drop_column する
  4. SQLAlchemy モデル側に Boolean カラムが宣言されている
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


def test_migration_0024_revision_chain(alembic_cfg: Config) -> None:
    """0024 が 0023 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0024_v2_patient_requires_multiple_staff")
    assert rev is not None, "0024 リビジョンが見つからない"
    assert rev.down_revision == "0023_v2_courses_code_check_add_e", (
        f"0024 の down_revision は 0023 のはず, got {rev.down_revision}"
    )


def test_migration_0024_upgrade_adds_column() -> None:
    """upgrade() が requires_multiple_staff を追加し、downgrade() が drop_column する."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (
        backend_root / "alembic" / "versions" / "0024_v2_patient_requires_multiple_staff.py"
    ).read_text(encoding="utf-8")

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: add_column with patients / requires_multiple_staff / Boolean
    assert "add_column" in upgrade_body
    assert '"patients"' in upgrade_body or "'patients'" in upgrade_body
    assert "requires_multiple_staff" in upgrade_body
    assert "Boolean" in upgrade_body
    assert "nullable=False" in upgrade_body
    assert "server_default" in upgrade_body

    # downgrade: drop_column
    assert "drop_column" in downgrade_body
    assert "requires_multiple_staff" in downgrade_body


def test_migration_0024_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0024 の upgrade()/downgrade() を実 SQL レベルで往復実行する.

    最小スキーマで patients テーブルを SQLite に作り、0024 上下で
    requires_multiple_staff カラムが追加・削除されることを確認する。
    """
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0024.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小 patients テーブルを作る (本テスト用 — 本物のスキーマは不要)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE patients (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL
                )
                """
            )
        )

    # 0024 をロード
    migration_path = (
        backend_root / "alembic" / "versions" / "0024_v2_patient_requires_multiple_staff.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0024", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0024"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # 初期状態: requires_multiple_staff は無い
    insp = inspect(engine)
    cols_before = {c["name"] for c in insp.get_columns("patients")}
    assert "requires_multiple_staff" not in cols_before

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_up = {c["name"] for c in insp.get_columns("patients")}
    assert "requires_multiple_staff" in cols_after_up, (
        "upgrade 後に requires_multiple_staff カラムが存在するはず"
    )

    # 既存行の DEFAULT が FALSE で埋まる: INSERT で NULL を許さないことを確認
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO patients (id, code, name) VALUES ('p1', 'P-001', 'テスト')")
        )
        rows = conn.execute(
            sa.text("SELECT requires_multiple_staff FROM patients WHERE id='p1'")
        ).all()
        assert len(rows) == 1
        # SQLite では BOOLEAN DEFAULT FALSE は 0 になる
        assert rows[0][0] in (0, False), (
            f"requires_multiple_staff の default は 0/FALSE のはず, got {rows[0][0]!r}"
        )

    # downgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after_down = {c["name"] for c in insp.get_columns("patients")}
    assert "requires_multiple_staff" not in cols_after_down, (
        "downgrade 後に requires_multiple_staff カラムが消えているはず"
    )

    engine.dispose()


def test_patient_model_has_requires_multiple_staff_column() -> None:
    """SQLAlchemy モデル側にも requires_multiple_staff 列が存在する."""
    from app.models.patient import Patient

    cols = {c.name for c in Patient.__table__.columns}
    assert "requires_multiple_staff" in cols, (
        f"Patient.__table__ に requires_multiple_staff が無い: {cols}"
    )
    col = Patient.__table__.columns["requires_multiple_staff"]
    assert col.nullable is False, "requires_multiple_staff は NOT NULL のはず"
