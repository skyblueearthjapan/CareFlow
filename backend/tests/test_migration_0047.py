"""P2-A: alembic migration 0047 tests (movability + suggestion_dismissals).

検証観点:
  1. 0047 リビジョンが読み込め、down_revision が 0046 を指す (単一 head).
  2. upgrade 後、patient_fixed_visits に movability カラム (NOT NULL, default 'unknown').
  3. backfill: is_pinned=true の既存行は movability='locked' に矯正される.
  4. movability の CHECK (4 値) が効く (不正値は IntegrityError).
  5. suggestion_dismissals テーブル + CHECK 3 本 + FK CASCADE が効く.
  6. downgrade 後、movability カラム / suggestion_dismissals テーブルが消える.
  7. ORM モデル (SuggestionDismissal) の基本 CRUD + 制約 (db fixture = create_all).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

_MIGRATION_FILENAME = "0047_movability_and_suggestion_dismissals.py"
_MIGRATION_REVISION = "0047_movability_and_suggestion_dismissals"


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / _MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("migration_0047", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0047"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prepare_sqlite_db(engine: sa.engine.Engine) -> None:
    """0047 が依存する最小スキーマ (patients / users / patient_fixed_visits with is_pinned)."""
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("CREATE TABLE patients (id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"))
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
                    is_pinned BOOLEAN NOT NULL DEFAULT 0
                )
                """
            )
        )


@pytest.fixture()
def upgraded_engine(tmp_path: Path):
    """fresh SQLite + 0047 upgrade を適用した engine を返す.

    既存 PFV を 2 行 (pinned / 非pinned) 入れてから upgrade し、backfill を検証する.
    """
    db_path = tmp_path / "migration_0047.db"
    db_url = f"sqlite:///{db_path}?foreign_keys=on"
    engine = create_engine(db_url)
    _prepare_sqlite_db(engine)

    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("INSERT INTO patients (id, name) VALUES ('p1', 'p1')"))
        # pinned 行 (backfill 対象 → locked) と 非pinned 行 (unknown 維持).
        conn.execute(
            sa.text(
                "INSERT INTO patient_fixed_visits "
                "(id, patient_id, mode, weekday, start_time, duration_min, is_pinned) "
                "VALUES ('pfv-pinned', 'p1', 'normal', 0, '09:00:00', 30, 1)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO patient_fixed_visits "
                "(id, patient_id, mode, weekday, start_time, duration_min, is_pinned) "
                "VALUES ('pfv-free', 'p1', 'normal', 1, '10:00:00', 30, 0)"
            )
        )

    migration_mod = _load_migration_module()
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    yield engine, migration_mod
    engine.dispose()


# ---------------------------------------------------------------------------
# revision chain (単一 head)
# ---------------------------------------------------------------------------


def test_migration_0047_revision_chain(alembic_cfg: Config) -> None:
    """0047 が 0046 から派生し、単一 head であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision(_MIGRATION_REVISION)
    assert rev is not None, "0047 リビジョンが見つからない"
    assert rev.down_revision == "0046_user_username_and_uniques", (
        f"0047 の down_revision は 0046 のはず, got {rev.down_revision}"
    )
    # 単一 head 不変量 (0047 以降に 0048/0049… が積まれても head は 1 本であること).
    # 具体的な head リビジョンは最新 migration に追従するためハードコードしない.
    heads = list(script.get_heads())
    assert len(heads) == 1, f"単一 head のはず, got {heads}"


# ---------------------------------------------------------------------------
# movability カラム + backfill
# ---------------------------------------------------------------------------


def test_movability_column_added_with_default(upgraded_engine) -> None:
    """upgrade 後、movability カラムが NOT NULL で追加される."""
    engine, _ = upgraded_engine
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("patient_fixed_visits")}
    assert "movability" in cols, "movability カラムが無い"
    assert cols["movability"]["nullable"] is False, "movability は NOT NULL のはず"


def test_movability_backfill_locked_for_pinned(upgraded_engine) -> None:
    """backfill: is_pinned=true の行は locked, それ以外は unknown."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        rows = dict(conn.execute(sa.text("SELECT id, movability FROM patient_fixed_visits")).all())
    assert rows["pfv-pinned"] == "locked", f"pinned 行は locked のはず: {rows}"
    assert rows["pfv-free"] == "unknown", f"非pinned 行は unknown のはず: {rows}"


def test_new_pfv_defaults_to_unknown(upgraded_engine) -> None:
    """upgrade 後の新規 INSERT は movability='unknown' (server_default)."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO patient_fixed_visits "
                "(id, patient_id, mode, weekday, start_time, duration_min, is_pinned) "
                "VALUES ('pfv-new', 'p1', 'normal', 2, '11:00:00', 30, 0)"
            )
        )
        val = conn.execute(
            sa.text("SELECT movability FROM patient_fixed_visits WHERE id='pfv-new'")
        ).scalar()
    assert val == "unknown", f"新規 PFV の default は unknown のはず: {val!r}"


def test_movability_check_rejects_invalid(upgraded_engine) -> None:
    """movability CHECK: 4 値以外は IntegrityError."""
    engine, _ = upgraded_engine
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO patient_fixed_visits "
                    "(id, patient_id, mode, weekday, start_time, duration_min, "
                    "is_pinned, movability) "
                    "VALUES ('pfv-bad', 'p1', 'normal', 3, '12:00:00', 30, 0, 'bogus')"
                )
            )


# ---------------------------------------------------------------------------
# suggestion_dismissals テーブル + CHECK
# ---------------------------------------------------------------------------


def test_suggestion_dismissals_table_and_checks(upgraded_engine) -> None:
    """suggestion_dismissals が張れ、kind/reason/weekday の CHECK が効く."""
    engine, _ = upgraded_engine
    insp = inspect(engine)
    assert "suggestion_dismissals" in insp.get_table_names()

    # 正常値: OK
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-ok', 'p1', 'time_change', 0, 'other')"
            )
        )

    # kind 不正 → CHECK 違反
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO suggestion_dismissals "
                    "(id, patient_id, kind, target_weekday, reason) "
                    "VALUES ('sd-badkind', 'p1', 'bogus', 0, 'other')"
                )
            )

    # weekday 範囲外 → CHECK 違反
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO suggestion_dismissals "
                    "(id, patient_id, kind, target_weekday, reason) "
                    "VALUES ('sd-badwd', 'p1', 'time_change', 7, 'other')"
                )
            )


def test_suggestion_dismissals_unique_fingerprint(upgraded_engine) -> None:
    """指紋 (patient_id, kind, target_weekday) に UNIQUE 制約 (uq_sd_fingerprint) がある."""
    engine, _ = upgraded_engine
    # 1 件目: 正常 INSERT
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-uniq1', 'p1', 'time_change', 0, 'other')"
            )
        )
    # 同一指紋の 2 件目: UNIQUE 違反 → IntegrityError
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO suggestion_dismissals "
                    "(id, patient_id, kind, target_weekday, reason) "
                    "VALUES ('sd-uniq2', 'p1', 'time_change', 0, 'day_immovable')"
                )
            )
    # 異なる kind は別指紋 → OK
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-uniq3', 'p1', 'day_change', 0, 'other')"
            )
        )


def test_suggestion_dismissals_cascade_on_patient_delete(upgraded_engine) -> None:
    """patient 物理削除で suggestion_dismissals が CASCADE で消える."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO suggestion_dismissals "
                "(id, patient_id, kind, target_weekday, reason) "
                "VALUES ('sd-c', 'p1', 'day_change', 2, 'day_immovable')"
            )
        )
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("DELETE FROM patients WHERE id='p1'"))
    with engine.begin() as conn:
        cnt = conn.execute(sa.text("SELECT COUNT(*) FROM suggestion_dismissals")).scalar()
    assert cnt == 0, "patient 削除で dismissals が CASCADE されていない"


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def test_downgrade_removes_column_and_table(upgraded_engine) -> None:
    """downgrade 後、movability カラム / suggestion_dismissals テーブルが消える."""
    engine, migration_mod = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "movability" not in cols, f"downgrade 後も movability が残る: {cols}"
    assert "suggestion_dismissals" not in insp.get_table_names(), (
        "downgrade 後も suggestion_dismissals が残る"
    )


# ---------------------------------------------------------------------------
# ORM モデル (create_all = conftest db fixture) の基本 CRUD + 制約
# ---------------------------------------------------------------------------


def test_suggestion_dismissal_model_registered() -> None:
    """SuggestionDismissal モデルが metadata に登録されている."""
    from app.models import SuggestionDismissal

    assert SuggestionDismissal.__tablename__ == "suggestion_dismissals"
    cols = {c.name for c in SuggestionDismissal.__table__.columns}
    assert {
        "id",
        "patient_id",
        "kind",
        "target_weekday",
        "reason",
        "reason_note",
        "dismissed_by",
        "dismissed_at",
        "expires_at",
    } <= cols, f"必須列が無い: {cols}"


def test_pfv_model_has_movability_column() -> None:
    """PatientFixedVisit モデルに movability 列 (NOT NULL) がある."""
    from app.models.patient_fixed_visit import PatientFixedVisit

    col = PatientFixedVisit.__table__.columns["movability"]
    assert col.nullable is False, "movability は NOT NULL のはず"


@pytest.mark.asyncio
async def test_suggestion_dismissal_crud(db) -> None:
    """ORM 経由で SuggestionDismissal を作成・取得できる (create_all テーブル)."""
    from sqlalchemy import select

    from app.models import Patient, SuggestionDismissal

    p = Patient(code="SD-1", name="SD患者", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)

    sd = SuggestionDismissal(
        patient_id=p.id,
        kind="time_change",
        target_weekday=3,
        reason="time_immovable",
        reason_note="本人希望",
    )
    db.add(sd)
    await db.commit()
    await db.refresh(sd)

    got = await db.scalar(select(SuggestionDismissal).where(SuggestionDismissal.patient_id == p.id))
    assert got is not None
    assert got.kind == "time_change"
    assert got.target_weekday == 3
    assert got.reason == "time_immovable"
    assert got.expires_at is None  # NULL = 無期限
    assert got.dismissed_at is not None


@pytest.mark.asyncio
async def test_suggestion_dismissal_check_constraint(db) -> None:
    """不正な reason は CHECK 違反 (create_all テーブルでも制約が効く)."""
    from app.models import Patient, SuggestionDismissal

    p = Patient(code="SD-2", name="SD患者2", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)

    db.add(
        SuggestionDismissal(
            patient_id=p.id,
            kind="day_change",
            target_weekday=0,
            reason="bogus_reason",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_pfv_movability_check_constraint_via_orm(db) -> None:
    """create_all テーブルでも movability CHECK が効く (不正値は IntegrityError)."""
    from app.models import Patient, PatientFixedVisit

    p = Patient(code="MV-1", name="可動域患者", special_week_active=[])
    db.add(p)
    await db.commit()
    await db.refresh(p)

    db.add(
        PatientFixedVisit(
            patient_id=p.id,
            mode="normal",
            weekday=0,
            start_time=time(9, 0),
            duration_min=30,
            slot_index=0,
            movability="not_a_valid_value",
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
