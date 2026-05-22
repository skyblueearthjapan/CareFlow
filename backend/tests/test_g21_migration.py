"""Phase G-21 T1: alembic migration 0037 tests.

検証観点:
  1. 0037 リビジョンが alembic スクリプト上で読み込め、down_revision が 0036 を指す.
  2. upgrade 後、既存全 PFV 行が ``is_pinned=true`` に backfill されている.
  3. upgrade 後、新規 INSERT した PFV は ``is_pinned=false`` (= default).
  4. patient_same_address_links に a > b の行を入れると CHECK 違反.
  5. pair_mode に不正値を入れると違反 (CHECK / ENUM).
  6. patient 削除で links が cascade で消える.
  7. office_feature_flags の UNIQUE (office_id, feature_key) 制約.
  8. migration down 後、 column / table が消える.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import DataError, IntegrityError

_MIGRATION_FILENAME = "0037_g21_pfv_is_pinned_and_same_address_links.py"
_MIGRATION_REVISION = "0037_g21_pfv_is_pinned_and_same_address_links"

# PG 専用テストの起動 URL. CI / 開発者ローカルで環境変数があるときのみ走る.
# 例: PG_TEST_URL=postgresql+psycopg://carelink:carelink@localhost:5432/carelink_test
_PG_TEST_URL = os.environ.get("PG_TEST_URL")


@pytest.fixture()
def alembic_cfg() -> Config:
    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    return cfg


def _load_migration_module() -> object:
    backend_root = Path(__file__).resolve().parent.parent
    migration_path = backend_root / "alembic" / "versions" / _MIGRATION_FILENAME
    spec = importlib.util.spec_from_file_location("migration_0037", migration_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0037"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _prepare_sqlite_db(engine: sa.engine.Engine) -> None:
    """0037 が依存する最小スキーマ (patients / offices / users / patient_fixed_visits) を作る.

    SQLite では FK の ondelete=CASCADE は ``PRAGMA foreign_keys = ON`` が必須.
    """
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                """
                CREATE TABLE patients (
                    id TEXT PRIMARY KEY,
                    name TEXT
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE offices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT
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


# 本番想定行数 (134) と同程度をテスト fixture で再現する.
# reviewer 要求 "50 件以上 (推奨 60 件)" を満たす.
_BACKFILL_ROWS = 60


@pytest.fixture()
def upgraded_engine(tmp_path: Path):
    """fresh SQLite + 0037 upgrade を適用した engine を返す.

    backfill 対象の PFV 行を ``_BACKFILL_ROWS`` 件入れた状態で upgrade する.
    """
    db_path = tmp_path / "g21_migration.db"
    db_url = f"sqlite:///{db_path}?foreign_keys=on"
    engine = create_engine(db_url)
    _prepare_sqlite_db(engine)

    # 既存 PFV 行を _BACKFILL_ROWS 件入れておく (= backfill 対象).
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        for i in range(_BACKFILL_ROWS):
            pid = f"p-existing-{i:03d}"
            conn.execute(
                sa.text("INSERT INTO patients (id, name) VALUES (:id, :name)"),
                {"id": pid, "name": pid},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO patient_fixed_visits "
                    "(id, patient_id, mode, weekday, start_time, duration_min) "
                    "VALUES (:id, :pid, 'normal', :wd, :st, :dur)"
                ),
                {
                    "id": f"pfv-existing-{i:03d}",
                    "pid": pid,
                    "wd": i % 7,
                    "st": f"{9 + (i % 8):02d}:00:00",
                    "dur": 30 + (i % 3) * 15,
                },
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
# T1.0: revision chain
# ---------------------------------------------------------------------------


def test_migration_0037_revision_chain(alembic_cfg: Config) -> None:
    """0037 が 0036 から派生していること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision(_MIGRATION_REVISION)
    assert rev is not None, "0037 リビジョンが見つからない"
    assert rev.down_revision == "0036_drop_weekday_priority", (
        f"0037 の down_revision は 0036 のはず, got {rev.down_revision}"
    )


# ---------------------------------------------------------------------------
# T1.1: 既存全 PFV 行が is_pinned=true (backfill)
# ---------------------------------------------------------------------------


def test_t1_1_existing_pfv_rows_are_backfilled_to_true(upgraded_engine) -> None:
    """upgrade 後、 既存全 PFV 行 (_BACKFILL_ROWS 件 ≥ 50) が is_pinned=true.

    本番想定行数 (134 件) を見据え、 fixture で ``_BACKFILL_ROWS`` (= 60) 件
    INSERT してから upgrade を適用し、 1 件も漏れず true で埋まることを検証.
    Step 1 で ``server_default=true`` で ADD COLUMN しているため、 UPDATE 全行
    backfill 無しでも既存行は true になる.
    """
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT id, is_pinned FROM patient_fixed_visits ORDER BY id")
        ).all()
    assert len(rows) == _BACKFILL_ROWS, (
        f"fixture が _BACKFILL_ROWS 件入れているはず: got {len(rows)}"
    )
    assert _BACKFILL_ROWS >= 50, "reviewer 要求 (50 件以上) を満たすこと"
    # SQLite stores Boolean as 0/1.
    not_true = [row for row in rows if bool(row[1]) is not True]
    assert not not_true, f"is_pinned=true でない行が {len(not_true)} 件: {not_true[:5]}"


# ---------------------------------------------------------------------------
# T1.2: 新規 INSERT した PFV は is_pinned=false (default)
# ---------------------------------------------------------------------------


def test_t1_2_new_pfv_row_defaults_to_false(upgraded_engine) -> None:
    """upgrade 後、 INSERT した新規 PFV は is_pinned=false (= server_default)."""
    engine, _ = upgraded_engine
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("INSERT INTO patients (id, name) VALUES ('p-new', 'p-new')"))
        # is_pinned を明示しない. server_default が効くはず.
        conn.execute(
            sa.text(
                "INSERT INTO patient_fixed_visits "
                "(id, patient_id, mode, weekday, start_time, duration_min) "
                "VALUES ('pfv-new', 'p-new', 'normal', 2, '11:00:00', 30)"
            )
        )
        row = conn.execute(
            sa.text("SELECT is_pinned FROM patient_fixed_visits WHERE id = 'pfv-new'")
        ).one()
    assert bool(row[0]) is False, f"新規 PFV の is_pinned default が false でない: {row[0]!r}"


# ---------------------------------------------------------------------------
# T1.3: patient_same_address_links に a > b の行を入れると CHECK 違反
# ---------------------------------------------------------------------------


def test_t1_3_same_address_links_order_check(upgraded_engine) -> None:
    """patient_a_id < patient_b_id を満たさない行は CHECK 違反."""
    engine, _ = upgraded_engine
    # 2 つの patient を準備.
    pid_lo = "a-aaa"
    pid_hi = "z-zzz"
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        for pid in (pid_lo, pid_hi):
            conn.execute(
                sa.text("INSERT INTO patients (id, name) VALUES (:id, :name)"),
                {"id": pid, "name": pid},
            )

    # OK: a < b
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO patient_same_address_links "
                "(patient_a_id, patient_b_id, pair_mode) "
                "VALUES (:a, :b, 'blocked')"
            ),
            {"a": pid_lo, "b": pid_hi},
        )

    # NG: a > b (swap で逆順)
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO patient_same_address_links "
                    "(patient_a_id, patient_b_id, pair_mode) "
                    "VALUES (:a, :b, 'blocked')"
                ),
                {"a": pid_hi, "b": pid_lo},
            )


# ---------------------------------------------------------------------------
# T1.4: pair_mode に不正値を入れると違反
# ---------------------------------------------------------------------------


def test_t1_4_pair_mode_invalid_value_rejected(upgraded_engine) -> None:
    """pair_mode に未定義値を入れると違反 (SQLite: CHECK / PG: ENUM).

    本 test は upgraded_engine が SQLite ベースなので CHECK 制約による IntegrityError
    を確認する. PG 側の ENUM 違反 (DataError) は ``test_t1_8_pg_enum_rejects_invalid_pair_mode``
    で PG_TEST_URL が設定された環境のみ別途検証する.
    """
    engine, _ = upgraded_engine
    assert engine.dialect.name == "sqlite", (
        "本テストの IntegrityError 期待は SQLite CHECK 前提. PG は t1_8 で別途検証."
    )
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        for pid in ("p-pm-1", "p-pm-2"):
            conn.execute(
                sa.text("INSERT INTO patients (id, name) VALUES (:id, :name)"),
                {"id": pid, "name": pid},
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO patient_same_address_links "
                    "(patient_a_id, patient_b_id, pair_mode) "
                    "VALUES ('p-pm-1', 'p-pm-2', 'invalid_mode')"
                )
            )


# ---------------------------------------------------------------------------
# T1.5: patient 削除で links が cascade で消える
# ---------------------------------------------------------------------------


def test_t1_5_patient_delete_cascades_to_links(upgraded_engine) -> None:
    """patient 削除で patient_same_address_links 行が cascade で消える."""
    engine, _ = upgraded_engine
    pid_a, pid_b = "p-c-1", "p-c-2"
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        for pid in (pid_a, pid_b):
            conn.execute(
                sa.text("INSERT INTO patients (id, name) VALUES (:id, :name)"),
                {"id": pid, "name": pid},
            )
        conn.execute(
            sa.text(
                "INSERT INTO patient_same_address_links "
                "(patient_a_id, patient_b_id, pair_mode) "
                "VALUES (:a, :b, 'preferred')"
            ),
            {"a": pid_a, "b": pid_b},
        )

    # 削除前: 1 行存在
    with engine.begin() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM patient_same_address_links")).scalar()
        assert count == 1

    # patient_a を削除 → link も消える
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(sa.text("DELETE FROM patients WHERE id = :id"), {"id": pid_a})

    with engine.begin() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM patient_same_address_links")).scalar()
    assert count == 0, "patient_a 削除で links が cascade されていない"


# ---------------------------------------------------------------------------
# T1.6: office_feature_flags の UNIQUE (office_id, feature_key)
# ---------------------------------------------------------------------------


def test_t1_6_office_feature_flags_unique_constraint(upgraded_engine) -> None:
    """同一 (office_id, feature_key) を 2 回入れると UNIQUE 違反."""
    engine, _ = upgraded_engine
    office_id = "office-1"
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text("INSERT INTO offices (id, name) VALUES (:id, :name)"),
            {"id": office_id, "name": "稲毛"},
        )

    flag_id1 = str(uuid.uuid4())
    flag_id2 = str(uuid.uuid4())

    # 1 回目: OK
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO office_feature_flags (id, office_id, feature_key) "
                "VALUES (:id, :off, :key)"
            ),
            {"id": flag_id1, "off": office_id, "key": "g21_new_algorithm"},
        )

    # 2 回目: 同じ (office_id, feature_key) → UNIQUE 違反
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(sa.text("PRAGMA foreign_keys = ON"))
            conn.execute(
                sa.text(
                    "INSERT INTO office_feature_flags (id, office_id, feature_key) "
                    "VALUES (:id, :off, :key)"
                ),
                {"id": flag_id2, "off": office_id, "key": "g21_new_algorithm"},
            )

    # 違う feature_key は OK
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        conn.execute(
            sa.text(
                "INSERT INTO office_feature_flags (id, office_id, feature_key) "
                "VALUES (:id, :off, :key)"
            ),
            {"id": flag_id2, "off": office_id, "key": "g21_other_feature"},
        )


# ---------------------------------------------------------------------------
# T1.7: migration down 後、 column / table が消える
# ---------------------------------------------------------------------------


def test_t1_7_downgrade_removes_column_and_tables(upgraded_engine) -> None:
    """downgrade 後、 is_pinned カラム / 2 テーブル / index が消える."""
    engine, migration_mod = upgraded_engine

    # 削除前: あること確認
    insp = inspect(engine)
    cols_before = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    tables_before = set(insp.get_table_names())
    assert "is_pinned" in cols_before
    assert "patient_same_address_links" in tables_before
    assert "office_feature_flags" in tables_before

    # downgrade を実行
    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    cols_after = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    tables_after = set(insp.get_table_names())
    assert "is_pinned" not in cols_after, f"downgrade 後も is_pinned が残っている: {cols_after}"
    assert "patient_same_address_links" not in tables_after, (
        f"downgrade 後も patient_same_address_links が残っている: {tables_after}"
    )
    assert "office_feature_flags" not in tables_after, (
        f"downgrade 後も office_feature_flags が残っている: {tables_after}"
    )


# ---------------------------------------------------------------------------
# 補助テスト: ORM model に is_pinned が宣言されていること
# ---------------------------------------------------------------------------


def test_pfv_model_has_is_pinned_column() -> None:
    """SQLAlchemy モデル側にも is_pinned 列が存在する (NOT NULL, default False)."""
    from app.models.patient_fixed_visit import PatientFixedVisit

    cols = {c.name for c in PatientFixedVisit.__table__.columns}
    assert "is_pinned" in cols, f"PatientFixedVisit.__table__ に is_pinned が無い: {cols}"
    col = PatientFixedVisit.__table__.columns["is_pinned"]
    assert col.nullable is False, "is_pinned は NOT NULL のはず"


def test_same_address_link_model_registered() -> None:
    """PatientSameAddressLink モデルが metadata に登録されている."""
    from app.models import PatientSameAddressLink

    assert PatientSameAddressLink.__tablename__ == "patient_same_address_links"
    cols = {c.name for c in PatientSameAddressLink.__table__.columns}
    assert {"patient_a_id", "patient_b_id", "pair_mode"} <= cols, f"必須列が無い: {cols}"


def test_office_feature_flag_model_registered() -> None:
    """OfficeFeatureFlag モデルが metadata に登録されている."""
    from app.models import OfficeFeatureFlag

    assert OfficeFeatureFlag.__tablename__ == "office_feature_flags"
    cols = {c.name for c in OfficeFeatureFlag.__table__.columns}
    assert {"id", "office_id", "feature_key"} <= cols, f"必須列が無い: {cols}"


# ---------------------------------------------------------------------------
# HIGH-4: PatientSameAddressLinkCreate schema の sort validator
# ---------------------------------------------------------------------------


def test_same_address_link_create_swaps_when_a_greater_than_b() -> None:
    """patient_a_id > patient_b_id のとき自動 swap (a < b 不変条件保持)."""
    from app.schemas.patient_same_address_link import PatientSameAddressLinkCreate

    big = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    small = uuid.UUID("00000000-0000-0000-0000-000000000001")

    obj = PatientSameAddressLinkCreate(
        patient_a_id=big,
        patient_b_id=small,
        pair_mode="blocked",
    )
    assert obj.patient_a_id == small, "a > b で渡しても swap 後に a < b になるはず"
    assert obj.patient_b_id == big


def test_same_address_link_create_preserves_when_a_less_than_b() -> None:
    """既に a < b なら何も変えない."""
    from app.schemas.patient_same_address_link import PatientSameAddressLinkCreate

    small = uuid.UUID("00000000-0000-0000-0000-000000000001")
    big = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    obj = PatientSameAddressLinkCreate(
        patient_a_id=small,
        patient_b_id=big,
        pair_mode="required",
    )
    assert obj.patient_a_id == small
    assert obj.patient_b_id == big


def test_same_address_link_create_rejects_same_id() -> None:
    """patient_a_id == patient_b_id のとき ValueError."""
    from pydantic import ValidationError

    from app.schemas.patient_same_address_link import PatientSameAddressLinkCreate

    same = uuid.UUID("12345678-1234-1234-1234-123456789012")
    with pytest.raises(ValidationError) as excinfo:
        PatientSameAddressLinkCreate(
            patient_a_id=same,
            patient_b_id=same,
            pair_mode="preferred",
        )
    assert "同じにできません" in str(excinfo.value)


# ---------------------------------------------------------------------------
# HIGH-2: PG 専用 ENUM 検証 (PG_TEST_URL 設定時のみ動作)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _PG_TEST_URL is None,
    reason="PG_TEST_URL 未設定 (= 環境変数で PG 接続文字列を渡したときのみ動作)",
)
def test_t1_8_pg_enum_rejects_invalid_pair_mode() -> None:
    """[PG only] same_address_pair_mode ENUM は未定義値を弾く.

    SQLite は ENUM 相当を CHECK 制約で表現するため `test_t1_4` で代用するが、
    本番 PG では真の ENUM 型のため、ENUM 不正値挿入は ``invalid_text_representation``
    エラーになることを直接検証する.

    必要 env:
        PG_TEST_URL=postgresql+psycopg://user:pass@host:port/dbname
        (= 0036 まで migrated 済の使い捨て DB を期待)
    """
    assert _PG_TEST_URL is not None  # for type narrowing
    engine = create_engine(_PG_TEST_URL)
    migration_mod = _load_migration_module()

    # PG 側に 0037 を適用 (前提: down_revision 0036 が当たっている使い捨て DB).
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    try:
        with engine.begin() as conn:
            # ENUM 型が作られていること.
            row = conn.execute(
                sa.text("SELECT typname FROM pg_type WHERE typname = 'same_address_pair_mode'")
            ).first()
            assert row is not None, "PG ENUM 'same_address_pair_mode' が未作成"

            # 患者 2 件を投入.
            pid_a = uuid.uuid4()
            pid_b = uuid.uuid4()
            lo, hi = sorted([pid_a, pid_b], key=str)
            for pid in (lo, hi):
                conn.execute(
                    sa.text("INSERT INTO patients (id) VALUES (:id)"),
                    {"id": pid},
                )

        # ENUM 不正値: PG では DataError (InvalidTextRepresentation) を投げる.
        with pytest.raises((DataError, IntegrityError)):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO patient_same_address_links "
                        "(patient_a_id, patient_b_id, pair_mode) "
                        "VALUES (:a, :b, 'invalid_enum_value')"
                    ),
                    {"a": lo, "b": hi},
                )

        # 正常値はそのまま入る (= ENUM 受理).
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO patient_same_address_links "
                    "(patient_a_id, patient_b_id, pair_mode) "
                    "VALUES (:a, :b, 'required')"
                ),
                {"a": lo, "b": hi},
            )
    finally:
        # クリーンアップ: downgrade で完全に元に戻す.
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                migration_mod.downgrade()  # type: ignore[attr-defined]
        engine.dispose()
