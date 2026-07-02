"""P3-②: alembic migration 0049 tests (ck_sd_kind に 'swap' を追加).

検証観点:
  1. 0049 リビジョンが読み込め、down_revision が 0048 を指す (単一 head).
  2. upgrade 後、suggestion_dismissals.kind = 'swap' が INSERT できる.
  3. upgrade 後も 'bogus' 等の不正 kind は CHECK 違反 (IntegrityError).
  4. downgrade 後、kind='swap' は再び拒否される (元の 2 値に戻る).

ローカル SQLite のみ (本番 DB 禁止). 0047 テストの batch 作法を踏襲する.
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
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

_MIGRATION_FILENAME = "0049_swap_dismissal_kind.py"
_MIGRATION_REVISION = "0049_swap_dismissal_kind"


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / _MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("migration_0049", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0049"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prepare_sqlite_db(engine: sa.engine.Engine) -> None:
    """0049 が依存する最小スキーマ (patients / users / suggestion_dismissals 旧 CHECK)."""
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("CREATE TABLE patients (id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"))
        # 0047 が張るのと同型の named CHECK (旧 2 値) を持つテーブルを作る.
        conn.execute(
            sa.text(
                """
                CREATE TABLE suggestion_dismissals (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    target_weekday SMALLINT NOT NULL,
                    reason TEXT NOT NULL,
                    reason_note TEXT,
                    dismissed_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                    dismissed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    CONSTRAINT ck_sd_kind CHECK (kind IN ('time_change','day_change')),
                    CONSTRAINT ck_sd_reason CHECK (
                        reason IN ('day_immovable','time_immovable','staff_relation','other')
                    ),
                    CONSTRAINT ck_sd_weekday CHECK (target_weekday BETWEEN 0 AND 6),
                    CONSTRAINT uq_sd_fingerprint UNIQUE (patient_id, kind, target_weekday)
                )
                """
            )
        )
        conn.execute(sa.text("INSERT INTO patients (id, name) VALUES ('p1', 'p1')"))


@pytest.fixture()
def upgraded_engine(tmp_path: Path):
    db_path = tmp_path / "migration_0049.db"
    engine = create_engine(f"sqlite:///{db_path}?foreign_keys=on")
    _prepare_sqlite_db(engine)

    migration_mod = _load_migration_module()
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    yield engine, migration_mod
    engine.dispose()


def test_migration_0049_revision_chain(alembic_cfg: Config) -> None:
    """0049 が 0048 から派生し、単一 head であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision(_MIGRATION_REVISION)
    assert rev is not None, "0049 リビジョンが見つからない"
    assert rev.down_revision == "0048_visit_manual_staff_override", (
        f"0049 の down_revision は 0048 のはず, got {rev.down_revision}"
    )
    heads = list(script.get_heads())
    assert heads == [_MIGRATION_REVISION], f"単一 head 0049 のはず, got {heads}"


def test_swap_kind_accepted_after_upgrade(upgraded_engine) -> None:
    """upgrade 後、kind='swap' が INSERT できる (CHECK 拡張)."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-swap', 'p1', 'swap', 0, 'other')"
            )
        )
        val = conn.execute(
            sa.text("SELECT kind FROM suggestion_dismissals WHERE id='sd-swap'")
        ).scalar()
    assert val == "swap"


def test_invalid_kind_still_rejected_after_upgrade(upgraded_engine) -> None:
    """upgrade 後も未定義 kind ('bogus') は CHECK 違反."""
    engine, _ = upgraded_engine
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO suggestion_dismissals "
                    "(id, patient_id, kind, target_weekday, reason) "
                    "VALUES ('sd-bad', 'p1', 'bogus', 0, 'other')"
                )
            )


def test_existing_kinds_still_accepted_after_upgrade(upgraded_engine) -> None:
    """upgrade 後も 既存 2 値 (time_change / day_change) は引き続き受理される."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-tc', 'p1', 'time_change', 1, 'other'), "
                "('sd-dc', 'p1', 'day_change', 2, 'other')"
            )
        )


def test_downgrade_rejects_swap_again(upgraded_engine) -> None:
    """downgrade 後、kind='swap' は再び CHECK 違反になる (元の 2 値へ)."""
    engine, migration_mod = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO suggestion_dismissals "
                    "(id, patient_id, kind, target_weekday, reason) "
                    "VALUES ('sd-swap2', 'p1', 'swap', 3, 'other')"
                )
            )
