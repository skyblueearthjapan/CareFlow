"""alembic migration 0098 (kaipoke_source_snapshots) のテスト.

1. 0098 が 0097 から派生し、head が単一。revision id は 32 文字以内。
2. SQLite で upgrade → 表と列・UNIQUE(kind, period_start)・CHECK ができる。
3. downgrade で表が消え、再 upgrade で戻る。
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

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REVISION = "0098_kaipoke_source_snapshots"
_TABLE = "kaipoke_source_snapshots"


def _run(engine: sa.Engine, fn_name: str) -> None:
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0098", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0098"] = mod
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def _insert(conn: sa.Connection, id_: str, kind: str, status: str = "ok") -> None:
    conn.execute(
        sa.text(
            f"INSERT INTO {_TABLE} (id, kind, period_start, rows, row_count, status, fetched_at) "
            "VALUES (:id, :kind, '2026-10-01', '[]', 0, :status, '2026-10-20 00:00:00')"
        ),
        {"id": id_, "kind": kind, "status": status},
    )


def test_migration_0098_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_REVISION)
    assert rev is not None
    assert rev.down_revision == "0097_checkin_patient_idx"
    assert len(_REVISION) <= 32
    assert len(list(script.get_heads())) == 1


def test_migration_0098_upgrade_downgrade(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0098.db'}")

    _run(engine, "upgrade")
    cols = {c["name"] for c in inspect(engine).get_columns(_TABLE)}
    assert cols == {
        "id",
        "kind",
        "period_start",
        "office_id",
        "rows",
        "covered_dates",
        "row_count",
        "status",
        "error",
        "fetched_at",
        "job_id",
    }
    indexes = {ix["name"] for ix in inspect(engine).get_indexes(_TABLE)}
    assert "ix_kaipoke_source_snapshots_job" in indexes
    with engine.begin() as conn:
        _insert(conn, "a", "plan_csv")
        _insert(conn, "b", "actual_csv", "empty")
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _insert(conn, "c", "plan_csv")  # UNIQUE(kind, period_start)
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _insert(conn, "d", "visits")  # CHECK kind
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        _insert(conn, "e", "tasks", "partial")  # CHECK status

    _run(engine, "downgrade")
    assert _TABLE not in inspect(engine).get_table_names()

    _run(engine, "upgrade")
    assert _TABLE in inspect(engine).get_table_names()
