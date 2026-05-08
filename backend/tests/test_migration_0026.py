"""W34: alembic migration 0026 (visits partial UNIQUE) tests.

検証観点:
  1. 0026 リビジョンが alembic スクリプト上で読み込め, down_revision が 0025 を指す
  2. upgrade() が partial UNIQUE INDEX を貼る (deleted_at IS NULL 条件)
  3. partial UNIQUE は active 行 (deleted_at IS NULL) で重複を弾く
  4. soft-deleted 行 (deleted_at NOT NULL) は重複を許容する
  5. downgrade() で INDEX が drop される
"""

# ruff: noqa: I001
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


def test_migration_0026_revision_chain(alembic_cfg: Config) -> None:
    """0026 が 0025 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0026_v2_visits_unique_pds")
    assert rev is not None, "0026 リビジョンが見つからない"
    assert rev.down_revision == "0025_v2_pfv_course_template_id", (
        f"0026 の down_revision は 0025 のはず, got {rev.down_revision}"
    )


def test_migration_0026_creates_partial_unique_index() -> None:
    """upgrade() スクリプト本文に partial UNIQUE INDEX 定義が含まれる."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0026_v2_visits_unique_pds.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # upgrade: CREATE UNIQUE INDEX ... ON visits (patient_id, visit_date, start_time)
    # WHERE deleted_at IS NULL
    assert "CREATE UNIQUE INDEX" in upgrade_body
    assert "uq_visits_pds_active" in upgrade_body
    assert "visits" in upgrade_body
    assert "patient_id" in upgrade_body
    assert "visit_date" in upgrade_body
    assert "start_time" in upgrade_body
    assert "deleted_at IS NULL" in upgrade_body

    # downgrade: DROP INDEX
    assert "DROP INDEX" in downgrade_body
    assert "uq_visits_pds_active" in downgrade_body


def test_migration_0026_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0026 の upgrade()/downgrade() を実 SQL レベルで往復実行する.

    最小スキーマで visits テーブルを SQLite に作り, 0026 上下で
    partial UNIQUE INDEX が作成・削除されることを確認する.
    """
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0026.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小 visits テーブル (UNIQUE 検証に必要なカラムのみ)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE visits (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    visit_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    deleted_at TEXT
                )
                """
            )
        )

    # 0026 をロード
    migration_path = backend_root / "alembic" / "versions" / "0026_v2_visits_unique_pds.py"
    spec = importlib.util.spec_from_file_location("migration_0026", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0026"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # 初期状態: uq_visits_pds_active INDEX は無い
    insp = inspect(engine)
    idx_before = {ix["name"] for ix in insp.get_indexes("visits")}
    assert "uq_visits_pds_active" not in idx_before

    # upgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    idx_after_up = {ix["name"]: ix for ix in insp.get_indexes("visits")}
    assert "uq_visits_pds_active" in idx_after_up, (
        f"upgrade 後に uq_visits_pds_active が存在するはず, got indexes={list(idx_after_up)}"
    )
    ix = idx_after_up["uq_visits_pds_active"]
    # SQLite returns 1, PostgreSQL returns True — 真偽値として比較
    assert bool(ix["unique"]) is True, "uq_visits_pds_active は UNIQUE INDEX のはず"
    assert set(ix["column_names"]) == {"patient_id", "visit_date", "start_time"}, (
        f"INDEX 列は (patient_id, visit_date, start_time) のはず, got {ix['column_names']}"
    )

    # ----- 動作テスト 1: active 行 (deleted_at IS NULL) で重複は弾かれる -----
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v1', 'p1', '2026-05-04', '10:00:00', NULL)
                """
            )
        )

    # 同一 (patient, date, start_time) で deleted_at IS NULL を 2 度入れると失敗
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                    VALUES ('v2', 'p1', '2026-05-04', '10:00:00', NULL)
                    """
                )
            )

    # ----- 動作テスト 2: soft-deleted 行 (deleted_at NOT NULL) は重複可 -----
    # 既存 v1 を soft-delete してから, 同 key で active な行を INSERT できる
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE visits SET deleted_at = '2026-05-04T10:00:00+00:00' WHERE id = 'v1'")
        )
        # soft-deleted v1 と同 key で active な v3 を入れられる
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v3', 'p1', '2026-05-04', '10:00:00', NULL)
                """
            )
        )
        # さらに soft-deleted な行は同 key で何件でも入れられる
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v4', 'p1', '2026-05-04', '10:00:00', '2026-05-05T10:00:00+00:00')
                """
            )
        )

    # ----- 動作テスト 3: 異なる patient/date/time なら問題なし -----
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v5', 'p2', '2026-05-04', '10:00:00', NULL)
                """
            )
        )

    # downgrade
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    idx_after_down = {ix["name"] for ix in insp.get_indexes("visits")}
    assert "uq_visits_pds_active" not in idx_after_down, (
        "downgrade 後に uq_visits_pds_active が消えているはず"
    )

    # downgrade 後は重複が通る (UNIQUE 制約が無くなった証跡)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v6', 'p3', '2026-05-04', '10:00:00', NULL)
                """
            )
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO visits (id, patient_id, visit_date, start_time, deleted_at)
                VALUES ('v7', 'p3', '2026-05-04', '10:00:00', NULL)
                """
            )
        )

    engine.dispose()
