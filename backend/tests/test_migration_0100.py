"""alembic migration 0100 (患者様の住所を 2 つ持つ) のテスト.

1. 0100 が 0099 から派生し、head が単一。revision id は 32 文字以内。
2. SQLite で upgrade → ``insurance_address = NULLIF(TRIM(address), '')``・
   ``visit_same_as_insurance = true``。``address`` / ``lat`` / ``lng`` は 1 件も変わらない。
3. 新しい行の ``visit_same_as_insurance`` の既定は true。
4. downgrade で 2 列が消え (``address`` は元のまま)、再 upgrade で戻る。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.db.base import Base

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REVISION = "0100_patient_insurance_address"
_NEW_COLUMNS = {"insurance_address", "visit_same_as_insurance"}


def _run(engine: sa.Engine, fn_name: str) -> None:
    path = _BACKEND_ROOT / "alembic" / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("migration_0100", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0100"] = mod
    spec.loader.exec_module(mod)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn, opts={"target_metadata": Base.metadata})
        with Operations.context(ctx):
            getattr(mod, fn_name)()


def _prepare(tmp_path: Path) -> sa.Engine:
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_0100.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE patients (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    code VARCHAR(32) NOT NULL,
                    name VARCHAR(120) NOT NULL,
                    address VARCHAR(255),
                    lat NUMERIC(10, 7),
                    lng NUMERIC(10, 7)
                )
                """
            )
        )
        rows = (
            ("p1", "千葉市稲毛区1-1", 35.6, 140.1),
            ("p2", "  千葉市中央区2-2  ", 35.5, 140.2),
            ("p3", "   ", None, None),
            ("p4", None, None, None),
        )
        for pid, address, lat, lng in rows:
            conn.execute(
                sa.text(
                    "INSERT INTO patients (id, code, name, address, lat, lng) "
                    "VALUES (:id, :id, :id, :address, :lat, :lng)"
                ),
                {"id": pid, "address": address, "lat": lat, "lng": lng},
            )
    return engine


def _rows(engine: sa.Engine, cols: str) -> list[tuple]:
    with engine.begin() as conn:
        return [tuple(r) for r in conn.execute(sa.text(f"SELECT {cols} FROM patients ORDER BY id"))]


def test_migration_0100_revision_chain() -> None:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision(_REVISION)
    assert rev is not None
    assert rev.down_revision == "0099_leave_and_kinmu"
    assert len(_REVISION) <= 32
    assert len(list(script.get_heads())) == 1


def test_migration_0100_upgrade_downgrade(tmp_path: Path) -> None:
    engine = _prepare(tmp_path)
    before = _rows(engine, "id, address, lat, lng")

    _run(engine, "upgrade")
    assert _NEW_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("patients")}
    assert _rows(engine, "id, address, lat, lng") == before, "訪問先 (address/lat/lng) は変えない"
    assert _rows(engine, "id, insurance_address, visit_same_as_insurance") == [
        ("p1", "千葉市稲毛区1-1", 1),
        ("p2", "千葉市中央区2-2", 1),
        ("p3", None, 1),
        ("p4", None, 1),
    ]
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO patients (id, code, name) VALUES ('p5', 'p5', 'p5')"))
        assert (
            conn.execute(
                sa.text("SELECT visit_same_as_insurance FROM patients WHERE id = 'p5'")
            ).scalar()
            == 1
        )
        conn.execute(sa.text("DELETE FROM patients WHERE id = 'p5'"))

    _run(engine, "downgrade")
    assert not (_NEW_COLUMNS & {c["name"] for c in inspect(engine).get_columns("patients")})
    assert _rows(engine, "id, address, lat, lng") == before

    _run(engine, "upgrade")
    assert _NEW_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("patients")}
    assert _rows(engine, "id, address, lat, lng") == before
