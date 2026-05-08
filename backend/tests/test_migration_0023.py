"""W16 codex fix (中 2): alembic migration 0023 (courses.code CHECK に E 追加) tests.

検証観点:
  1. 0023 リビジョンが alembic スクリプト上で読み込め、down_revision が
     0022 を指している
  2. upgrade() が ck_courses_code_v2 を drop し、'E' 含む新制約を作成する
  3. downgrade() が 'E' を含まない旧制約に戻す
  4. SQLAlchemy モデル側の CheckConstraint が 'E' を含む
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


def test_migration_0023_revision_chain(alembic_cfg: Config) -> None:
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0023_v2_courses_code_check_add_e")
    assert rev is not None, "0023 リビジョンが見つからない"
    assert rev.down_revision == "0022_v2_w16_manager_course_seed", (
        f"0023 の down_revision は 0022 のはず, got {rev.down_revision}"
    )


def test_migration_0023_upgrade_adds_e() -> None:
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0023_v2_courses_code_check_add_e.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: 旧 CHECK drop + 'E' を含む新 CHECK 作成
    assert 'drop_constraint("ck_courses_code_v2"' in upgrade_body
    # 新 CHECK 文字列に 'E' が含まれる
    assert "'A', 'B', 'C', 'D', 'E', 'M'" in upgrade_body, (
        "upgrade で 'E' を含む CHECK が定義されていない"
    )

    # downgrade: 新 CHECK drop + 'E' を含まない旧 CHECK 作成
    assert 'drop_constraint("ck_courses_code_v2"' in downgrade_body
    assert "'A', 'B', 'C', 'D', 'M'" in downgrade_body
    # downgrade では 'E' を含む文字列が無いはず (upgrade だけ)
    # 文字列 "'A', 'B', 'C', 'D', 'E'" が downgrade に出てこないこと
    assert "'A', 'B', 'C', 'D', 'E', 'M'" not in downgrade_body, (
        "downgrade に 'E' を含む CHECK が紛れ込んでいる"
    )


def test_migration_0023_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0023 の upgrade()/downgrade() を実 SQL レベルで往復実行し、
    テーブル CHECK 制約の挙動を確認する.

    SQLite に最小スキーマで courses テーブルを作り、0023 上下を実行する。
    旧 CHECK (Mのみ) → upgrade で 'E' OK → downgrade で 'E' NG に戻る。
    """
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    from sqlalchemy.exc import IntegrityError

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0023.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 旧 CHECK (M のみ) で courses テーブルを作る
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE courses (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    CONSTRAINT ck_courses_code_v2
                        CHECK (code IN ('A', 'B', 'C', 'D', 'M'))
                )
                """
            )
        )

    # 0023 を ロード
    migration_path = backend_root / "alembic" / "versions" / "0023_v2_courses_code_check_add_e.py"
    spec = importlib.util.spec_from_file_location("migration_0023", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0023"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # 旧 CHECK 状態: 'E' は INSERT 不可
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(sa.text("INSERT INTO courses (id, code) VALUES ('e1', 'E')"))

    # upgrade 実行
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    # upgrade 後: 'E' が INSERT 可能
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO courses (id, code) VALUES ('e2', 'E')"))
        # 'X' は依然 NG
        with pytest.raises(IntegrityError):
            conn.execute(sa.text("INSERT INTO courses (id, code) VALUES ('x1', 'X')"))

    # downgrade 実行 (E 行を削除してから)
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM courses WHERE code = 'E'"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    # downgrade 後: 'E' が再び INSERT 不可
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(sa.text("INSERT INTO courses (id, code) VALUES ('e3', 'E')"))
        # 'A' は引き続き OK
        conn.execute(sa.text("INSERT INTO courses (id, code) VALUES ('a1', 'A')"))

    engine.dispose()


def test_courses_model_check_constraint_includes_e() -> None:
    """SQLAlchemy モデル側の CheckConstraint も 'E' を含むことを確認."""
    from app.models.course import COURSE_CODE_VALUES, Course

    assert "E" in COURSE_CODE_VALUES, f"COURSE_CODE_VALUES に 'E' が無い: {COURSE_CODE_VALUES}"

    found = False
    for ta in Course.__table_args__:
        if hasattr(ta, "sqltext"):
            text = str(ta.sqltext)
            if "'E'" in text and "code" in text:
                found = True
                break
    assert found, "Course.__table_args__ の CheckConstraint に 'E' が含まれない"
