"""v2 schema cleanup: alembic migration 0036 tests.

検証観点:
  1. 0036 リビジョンが alembic スクリプト上で読み込め、down_revision が 0035 を指す.
  2. upgrade() が weekly_pattern / special_weekly_pattern JSONB から
     ``weekday_priority`` キーを取り除く.
  3. downgrade() は no-op (情報損失を許容).
  4. upgrade()/downgrade() を実 SQL レベル (SQLite) で往復実行できる.
  5. patients テーブルに ``weekday_priority`` という物理カラムは存在しない
     (本 migration はカラム drop ではなく JSONB key 削除なので、副作用として
     スキーマ構造は変化しないことの確認).
"""

from __future__ import annotations

import importlib.util
import json
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
    migration_path = backend_root / "alembic" / "versions" / "0036_drop_weekday_priority.py"
    spec = importlib.util.spec_from_file_location("migration_0036", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0036"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_migration_0036_revision_chain(alembic_cfg: Config) -> None:
    """0036 が 0035 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0036_drop_weekday_priority")
    assert rev is not None, "0036 リビジョンが見つからない"
    assert rev.down_revision == "0035_pfv_sub_office", (
        f"0036 の down_revision は 0035 のはず, got {rev.down_revision}"
    )


def test_migration_0036_ddl_targets_jsonb_keys() -> None:
    """upgrade() が weekly_pattern / special_weekly_pattern を対象とし、
    'weekday_priority' キーを取り除く SQL を含む.
    downgrade() は no-op であること (`return None` のみ)."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0036_drop_weekday_priority.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: 両 JSONB カラムを掃除している
    assert "weekly_pattern" in upgrade_body
    assert "special_weekly_pattern" in upgrade_body
    assert "weekday_priority" in upgrade_body
    # PG パスに jsonb - key 演算子の使用
    assert "- 'weekday_priority'" in upgrade_body or '- "weekday_priority"' in upgrade_body

    # downgrade: 何もしない (no-op)
    assert "return None" in downgrade_body or "pass" in downgrade_body
    # drop_column を呼んで誤って既存スキーマを破壊していないこと
    assert "drop_column" not in downgrade_body


def test_migration_0036_sqlite_roundtrip(tmp_path: Path) -> None:
    """SQLite 環境で upgrade / downgrade を往復実行し、データの正しさを確認."""
    db_path = tmp_path / "migration_0036.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小 patients テーブル (本 migration が触る 2 カラムだけ持たせる).
    # 物理カラムとしては JSON 文字列で保持する.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE patients (
                    id TEXT PRIMARY KEY,
                    weekly_pattern TEXT,
                    special_weekly_pattern TEXT
                )
                """
            )
        )

        # P1: weekly_pattern に weekday_priority がある
        conn.execute(
            sa.text(
                "INSERT INTO patients (id, weekly_pattern, special_weekly_pattern) "
                "VALUES (:id, :wp, :swp)"
            ),
            {
                "id": "p1",
                "wp": json.dumps(
                    {
                        "frequency_per_week": 2,
                        "preferred_weekdays": ["Mon"],
                        "weekday_priority": "高",
                        "service_minutes": 60,
                    },
                    ensure_ascii=False,
                ),
                "swp": None,
            },
        )

        # P2: special_weekly_pattern にも weekday_priority がある
        conn.execute(
            sa.text(
                "INSERT INTO patients (id, weekly_pattern, special_weekly_pattern) "
                "VALUES (:id, :wp, :swp)"
            ),
            {
                "id": "p2",
                "wp": json.dumps(
                    {"frequency_per_week": 1, "weekday_priority": "中"},
                    ensure_ascii=False,
                ),
                "swp": json.dumps(
                    {"preferred_weekdays": ["Wed"], "weekday_priority": "低"},
                    ensure_ascii=False,
                ),
            },
        )

        # P3: weekday_priority キーが無いのでそのまま (no-op の対象)
        conn.execute(
            sa.text(
                "INSERT INTO patients (id, weekly_pattern, special_weekly_pattern) "
                "VALUES (:id, :wp, :swp)"
            ),
            {
                "id": "p3",
                "wp": json.dumps({"frequency_per_week": 3}, ensure_ascii=False),
                "swp": None,
            },
        )

        # P4: 全カラム NULL (一切触らない)
        conn.execute(
            sa.text(
                "INSERT INTO patients (id, weekly_pattern, special_weekly_pattern) "
                "VALUES (:id, :wp, :swp)"
            ),
            {"id": "p4", "wp": None, "swp": None},
        )

    migration_mod = _load_migration_module()

    # upgrade を実行
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    # 1) P1: weekday_priority が消える、他キーは保持
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT weekly_pattern FROM patients WHERE id='p1'")).one()
        wp = json.loads(row[0])
        assert "weekday_priority" not in wp
        assert wp["frequency_per_week"] == 2
        assert wp["preferred_weekdays"] == ["Mon"]
        assert wp["service_minutes"] == 60

    # 2) P2: weekly_pattern / special_weekly_pattern の両方から消える
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT weekly_pattern, special_weekly_pattern FROM patients WHERE id='p2'")
        ).one()
        wp = json.loads(row[0])
        swp = json.loads(row[1])
        assert "weekday_priority" not in wp
        assert wp["frequency_per_week"] == 1
        assert "weekday_priority" not in swp
        assert swp["preferred_weekdays"] == ["Wed"]

    # 3) P3: weekday_priority が元々無い行は他キー保持
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT weekly_pattern FROM patients WHERE id='p3'")).one()
        wp = json.loads(row[0])
        assert wp == {"frequency_per_week": 3}

    # 4) P4: NULL は NULL のまま
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT weekly_pattern, special_weekly_pattern FROM patients WHERE id='p4'")
        ).one()
        assert row[0] is None
        assert row[1] is None

    # downgrade は no-op: 直後の SELECT で同じ状態 (weekday_priority 復活せず)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT weekly_pattern FROM patients WHERE id='p1'")).one()
        wp = json.loads(row[0])
        assert "weekday_priority" not in wp, (
            "downgrade は no-op のはずだが weekday_priority が復活している"
        )

    engine.dispose()


def test_patients_table_has_no_weekday_priority_column() -> None:
    """patients テーブルに ``weekday_priority`` という物理列は存在しない.

    この migration は JSONB key 削除なのでテーブル構造は変化しない. 念のため
    SQLAlchemy 定義側を確認する.
    """
    from app.models.patient import Patient

    cols = {c.name for c in Patient.__table__.columns}
    assert "weekday_priority" not in cols, (
        f"patients テーブルに weekday_priority カラムが存在する: {cols}"
    )
    insp_cols = inspect  # noqa: F841 (silence "unused" — we keep the import for parity)
