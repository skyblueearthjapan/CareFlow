"""P4-C: alembic migration 0050 tests (ピン由来 locked 残骸の一括解放).

検証観点:
  1. 0050 リビジョンが読み込め、down_revision が 0049 を指す (単一 head).
  2. backfill: is_pinned=false ∧ movability='locked' の行は 'unknown' に解放される.
  3. is_pinned=true ∧ movability='locked' の行は 'locked' のまま維持 (含意どおり).
  4. locked 以外 (unknown / time_flexible / day_flexible) は非 pin でも触らない.
  5. downgrade は no-op (エラーにならず、データも変わらない).
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

_MIGRATION_FILENAME = "0050_release_pin_movability_locked.py"
_MIGRATION_REVISION = "0050_release_pin_movability_locked"


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / _MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("migration_0050", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0050"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prepare_sqlite_db(engine: sa.engine.Engine) -> None:
    """0050 が依存する最小スキーマ (patient_fixed_visits with movability + is_pinned)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                CREATE TABLE patient_fixed_visits (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    weekday INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    duration_min INTEGER NOT NULL DEFAULT 30,
                    is_pinned BOOLEAN NOT NULL DEFAULT 0,
                    movability TEXT NOT NULL DEFAULT 'unknown'
                )
                """
            )
        )


def _insert(conn, *, pfv_id: str, weekday: int, is_pinned: int, movability: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO patient_fixed_visits "
            "(id, patient_id, mode, weekday, start_time, duration_min, is_pinned, movability) "
            "VALUES (:id, 'p1', 'normal', :wd, '09:00:00', 30, :pin, :mov)"
        ),
        {"id": pfv_id, "wd": weekday, "pin": is_pinned, "mov": movability},
    )


@pytest.fixture()
def upgraded_engine(tmp_path: Path):
    """fresh SQLite に多様な (is_pinned, movability) 行を入れてから 0050 upgrade を適用."""
    db_path = tmp_path / "migration_0050.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _prepare_sqlite_db(engine)

    with engine.begin() as conn:
        # 解放対象: 非 pin ∧ locked → unknown
        _insert(conn, pfv_id="free-locked", weekday=0, is_pinned=0, movability="locked")
        # 維持: pin ∧ locked → locked のまま
        _insert(conn, pfv_id="pin-locked", weekday=1, is_pinned=1, movability="locked")
        # 非対象: 非 pin ∧ time_flexible → 不変
        _insert(conn, pfv_id="free-tflex", weekday=2, is_pinned=0, movability="time_flexible")
        # 非対象: 非 pin ∧ unknown → 不変
        _insert(conn, pfv_id="free-unknown", weekday=3, is_pinned=0, movability="unknown")
        # 非対象: 非 pin ∧ day_flexible → 不変
        _insert(conn, pfv_id="free-dflex", weekday=4, is_pinned=0, movability="day_flexible")

    migration_mod = _load_migration_module()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    yield engine, migration_mod
    engine.dispose()


def _movability_map(engine: sa.engine.Engine) -> dict[str, str]:
    with engine.begin() as conn:
        return dict(conn.execute(sa.text("SELECT id, movability FROM patient_fixed_visits")).all())


# ---------------------------------------------------------------------------
# revision chain (単一 head)
# ---------------------------------------------------------------------------


def test_migration_0050_revision_chain(alembic_cfg: Config) -> None:
    """0050 が 0049 から派生し、単一 head であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision(_MIGRATION_REVISION)
    assert rev is not None, "0050 リビジョンが見つからない"
    assert rev.down_revision == "0049_swap_dismissal_kind", (
        f"0050 の down_revision は 0049 のはず, got {rev.down_revision}"
    )
    # head は単一 (線形 chain) であること。後続 migration (0051, 0052, ...) が増えても
    # 単一 head であれば良いため、特定リビジョン名には固定しない (0043 と同じ規約)。
    heads = list(script.get_heads())
    assert len(heads) == 1, f"単一 head のはず, got {heads}"


# ---------------------------------------------------------------------------
# backfill: 非 pin の locked のみ解放
# ---------------------------------------------------------------------------


def test_backfill_releases_only_free_locked(upgraded_engine) -> None:
    """非 pin ∧ locked → unknown, pin ∧ locked → locked 維持."""
    engine, _ = upgraded_engine
    rows = _movability_map(engine)
    assert rows["free-locked"] == "unknown", f"非pin locked は解放されるはず: {rows}"
    assert rows["pin-locked"] == "locked", f"pin locked は維持されるはず: {rows}"


def test_backfill_does_not_touch_non_locked(upgraded_engine) -> None:
    """locked 以外は非 pin でも一切触らない."""
    engine, _ = upgraded_engine
    rows = _movability_map(engine)
    assert rows["free-tflex"] == "time_flexible", rows
    assert rows["free-unknown"] == "unknown", rows
    assert rows["free-dflex"] == "day_flexible", rows


# ---------------------------------------------------------------------------
# downgrade: no-op
# ---------------------------------------------------------------------------


def test_downgrade_is_noop(upgraded_engine) -> None:
    """downgrade は no-op: エラーにならず、backfill 済みデータも変わらない."""
    engine, migration_mod = upgraded_engine
    before = _movability_map(engine)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]
    after = _movability_map(engine)
    assert before == after, f"downgrade で値が変わってはいけない: {before} -> {after}"
