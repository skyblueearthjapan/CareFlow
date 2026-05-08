"""W37 Phase 1: alembic migration 0027 (multi-staff slot_index base) tests.

検証観点:
  1. 0027 リビジョンが alembic スクリプト上で読み込め, down_revision が 0026 を指す
  2. alembic heads が単一 (0027) であること
  3. upgrade() が patient_fixed_visits.slot_index 列 + 新 UNIQUE
     (patient_id, mode, weekday, slot_index) を作成し,
     旧 UNIQUE (patient_id, mode, weekday) を削除する
  4. upgrade() が visits の旧 partial UNIQUE INDEX
     uq_visits_pds_active を DROP し,
     新 partial UNIQUE INDEX uq_visits_pds_group_active を CREATE する
  5. 同 (patient, mode, weekday) で slot_index 0/1 を両方 INSERT 可能
  6. 同 (patient, mode, weekday, slot_index=0) 重複は UNIQUE 違反
  7. visits: 同 (patient, date, time) で visit_group_id A/B の 2 行 INSERT 可能
  8. visits: 同 (patient, date, time, visit_group_id=NULL) 2 行は UNIQUE 違反
  9. downgrade() で 0026 の状態に戻る
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


def test_migration_0027_revision_chain(alembic_cfg: Config) -> None:
    """0027 が 0026 から派生し, 単一 head であること."""
    script = ScriptDirectory.from_config(alembic_cfg)
    rev = script.get_revision("0027_v2_multi_staff_slot_index")
    assert rev is not None, "0027 リビジョンが見つからない"
    assert rev.down_revision == "0026_v2_visits_unique_pds", (
        f"0027 の down_revision は 0026 のはず, got {rev.down_revision}"
    )
    heads = list(script.get_heads())
    assert heads == ["0027_v2_multi_staff_slot_index"], (
        f"alembic heads が単一 0027 であるべき, got {heads}"
    )


def test_migration_0027_source_has_expected_ddl() -> None:
    """upgrade() スクリプト本文に主要 DDL が含まれる."""
    backend_root = Path(__file__).resolve().parent.parent
    src = (backend_root / "alembic" / "versions" / "0027_v2_multi_staff_slot_index.py").read_text(
        encoding="utf-8"
    )

    upgrade_idx = src.find("def upgrade()")
    downgrade_idx = src.find("def downgrade()")
    assert upgrade_idx >= 0 and downgrade_idx > upgrade_idx
    upgrade_body = src[upgrade_idx:downgrade_idx]
    downgrade_body = src[downgrade_idx:]

    # (a) slot_index 列追加 + UNIQUE 差し替え
    assert "slot_index" in upgrade_body
    assert "uq_pfv_patient_mode_weekday_slot" in upgrade_body
    assert "ck_pfv_slot_index" in upgrade_body
    # 旧 UNIQUE drop が含まれる
    assert "uq_pfv_patient_mode_weekday" in upgrade_body
    assert "drop_constraint" in upgrade_body

    # (b) visits partial UNIQUE 差し替え
    assert "DROP INDEX IF EXISTS uq_visits_pds_active" in upgrade_body
    assert "uq_visits_pds_group_active" in upgrade_body
    assert "COALESCE(visit_group_id" in upgrade_body
    assert "deleted_at IS NULL" in upgrade_body

    # downgrade: 元に戻す
    assert "uq_visits_pds_active" in downgrade_body
    assert "DROP INDEX IF EXISTS uq_visits_pds_group_active" in downgrade_body
    assert "drop_column" in downgrade_body and "slot_index" in downgrade_body


def test_migration_0027_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    """0027 の upgrade()/downgrade() を SQLite で実 SQL レベルで往復実行する.

    検証観点:
      - patient_fixed_visits: slot_index 列追加 + UNIQUE 差し替え
      - visits: partial UNIQUE INDEX 差し替え
      - 同曜日 slot 0/1 共存 OK / 同 slot 重複 NG
      - 同 (patient, date, time) は visit_group_id 違いなら OK / NULL 同士は NG
      - downgrade で 0026 形に戻る
    """
    import importlib.util
    import sys

    import sqlalchemy as sa
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    backend_root = Path(__file__).resolve().parent.parent
    db_path = tmp_path / "migration_0027.db"
    db_url = f"sqlite:///{db_path}"

    engine = create_engine(db_url)

    # 最小スキーマ: 0026 適用済みの形を再現する
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
                    course_template_id TEXT,
                    CONSTRAINT uq_pfv_patient_mode_weekday
                        UNIQUE (patient_id, mode, weekday)
                )
                """
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE visits (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL,
                    visit_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    visit_group_id TEXT,
                    deleted_at TEXT
                )
                """
            )
        )
        # 0026 が貼った partial UNIQUE INDEX を再現
        conn.execute(
            sa.text(
                """
                CREATE UNIQUE INDEX uq_visits_pds_active
                ON visits (patient_id, visit_date, start_time)
                WHERE deleted_at IS NULL
                """
            )
        )

    # 既存行を 1 件入れて, slot_index default=0 で温存されることを確認する
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO patient_fixed_visits
                  (id, patient_id, mode, weekday, start_time, duration_min)
                VALUES ('pfv_legacy', 'p1', 'normal', 0, '09:00:00', 30)
                """
            )
        )

    # 0027 をロード
    migration_path = backend_root / "alembic" / "versions" / "0027_v2_multi_staff_slot_index.py"
    spec = importlib.util.spec_from_file_location("migration_0027", migration_path)
    assert spec is not None and spec.loader is not None
    migration_mod = importlib.util.module_from_spec(spec)
    sys.modules["migration_0027"] = migration_mod
    spec.loader.exec_module(migration_mod)  # type: ignore[union-attr]

    # ----- upgrade -----
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.upgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    pfv_cols = {c["name"]: c for c in insp.get_columns("patient_fixed_visits")}
    assert "slot_index" in pfv_cols, "upgrade 後に slot_index 列が存在するはず"
    assert pfv_cols["slot_index"]["nullable"] is False, "slot_index は NOT NULL"

    # 既存行の slot_index は default の 0 に正規化されているはず
    with engine.begin() as conn:
        row = conn.execute(
            sa.text("SELECT slot_index FROM patient_fixed_visits WHERE id = 'pfv_legacy'")
        ).one()
        assert row[0] == 0, f"既存行の slot_index は default 0 のはず, got {row[0]}"

    # 新 UNIQUE が存在し, 旧 UNIQUE が消えている
    pfv_uniques = {uc["name"]: uc for uc in insp.get_unique_constraints("patient_fixed_visits")}
    assert "uq_pfv_patient_mode_weekday_slot" in pfv_uniques, (
        f"新 UNIQUE が無い: {list(pfv_uniques)}"
    )
    assert "uq_pfv_patient_mode_weekday" not in pfv_uniques, (
        f"旧 UNIQUE が残っている: {list(pfv_uniques)}"
    )
    new_uc_cols = set(pfv_uniques["uq_pfv_patient_mode_weekday_slot"]["column_names"])
    assert new_uc_cols == {"patient_id", "mode", "weekday", "slot_index"}, (
        f"UNIQUE 列構成が違う, got {new_uc_cols}"
    )

    # visits index が差し替わっていること
    # NB: SQLAlchemy reflection は expression-based index (COALESCE) をスキップするため,
    # sqlite_master を直接照会して INDEX 定義の存在を確認する.
    with engine.begin() as conn:
        idx_names = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='visits'")
            ).all()
        }
    assert "uq_visits_pds_active" not in idx_names, f"旧 partial UNIQUE が残っている: {idx_names}"
    assert "uq_visits_pds_group_active" in idx_names, f"新 partial UNIQUE が無い: {idx_names}"

    # ----- 動作テスト 1: pfv 同 weekday slot 0/1 共存 OK -----
    with engine.begin() as conn:
        # slot_index=0 で別 weekday を入れて legacy 行と衝突しないよう確認
        conn.execute(
            sa.text(
                """
                INSERT INTO patient_fixed_visits
                  (id, patient_id, mode, weekday, start_time, duration_min, slot_index)
                VALUES ('pfv_a0', 'p1', 'normal', 1, '09:00:00', 30, 0)
                """
            )
        )
        # 同 (patient, mode, weekday) でも slot_index=1 なら OK
        conn.execute(
            sa.text(
                """
                INSERT INTO patient_fixed_visits
                  (id, patient_id, mode, weekday, start_time, duration_min, slot_index)
                VALUES ('pfv_a1', 'p1', 'normal', 1, '09:00:00', 30, 1)
                """
            )
        )

    # ----- 動作テスト 2: 同 (patient, mode, weekday, slot_index=0) は UNIQUE NG -----
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO patient_fixed_visits
                      (id, patient_id, mode, weekday, start_time, duration_min, slot_index)
                    VALUES ('pfv_dup', 'p1', 'normal', 1, '10:00:00', 30, 0)
                    """
                )
            )

    # ----- 動作テスト 3: visits 同 (p, date, time) でも visit_group_id 違いなら OK -----
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO visits
                  (id, patient_id, visit_date, start_time, visit_group_id, deleted_at)
                VALUES ('v_grp_a', 'p_multi', '2026-05-25', '10:00:00',
                        '11111111-1111-1111-1111-111111111111', NULL)
                """
            )
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO visits
                  (id, patient_id, visit_date, start_time, visit_group_id, deleted_at)
                VALUES ('v_grp_b', 'p_multi', '2026-05-25', '10:00:00',
                        '22222222-2222-2222-2222-222222222222', NULL)
                """
            )
        )
        # 同 visit_group_id ペア (= 2 名 1 組) なら 2 行 OK でも NULL 同士は NG
        conn.execute(
            sa.text(
                """
                INSERT INTO visits
                  (id, patient_id, visit_date, start_time, visit_group_id, deleted_at)
                VALUES ('v_solo_1', 'p_solo', '2026-05-25', '10:00:00', NULL, NULL)
                """
            )
        )
    # 2 行目の NULL は同キー扱いで弾かれるはず
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO visits
                      (id, patient_id, visit_date, start_time, visit_group_id, deleted_at)
                    VALUES ('v_solo_2', 'p_solo', '2026-05-25', '10:00:00', NULL, NULL)
                    """
                )
            )

    # ----- 動作テスト 4: 異なる visit_group_id 2 行 + 同 group 2 行も入る -----
    # 同 visit_group_id (= partner) は同時刻 2 行までなのでこの test では不要

    # ----- soft-deleted 行は UNIQUE 対象外 (0026 から踏襲) -----
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE visits SET deleted_at = '2026-05-25T10:00:00+00:00' WHERE id = 'v_solo_1'"
            )
        )
        # 同キーで active な行が入る
        conn.execute(
            sa.text(
                """
                INSERT INTO visits
                  (id, patient_id, visit_date, start_time, visit_group_id, deleted_at)
                VALUES ('v_solo_3', 'p_solo', '2026-05-25', '10:00:00', NULL, NULL)
                """
            )
        )

    # downgrade では旧 partial UNIQUE (visit_group_id 込みでない) を貼り直すため,
    # 同 (patient, date, time) で active な行が複数あると INDEX 作成に失敗する.
    # 検証目的の partner visit を soft-delete してから downgrade をかける.
    # 同様に slot_index=1 の pfv 行も削除する (旧 UNIQUE は slot_index を含まないため
    # 同 patient/mode/weekday の重複 = downgrade 不可).
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE visits SET deleted_at = '2026-05-25T11:00:00+00:00'"
                " WHERE id IN ('v_grp_a', 'v_grp_b', 'v_solo_3')"
            )
        )
        conn.execute(sa.text("DELETE FROM patient_fixed_visits WHERE slot_index = 1"))

    # ----- downgrade -----
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration_mod.downgrade()  # type: ignore[attr-defined]

    insp = inspect(engine)
    pfv_cols_after = {c["name"] for c in insp.get_columns("patient_fixed_visits")}
    assert "slot_index" not in pfv_cols_after, "downgrade 後に slot_index 列が消えているはず"
    pfv_uc_after = {uc["name"] for uc in insp.get_unique_constraints("patient_fixed_visits")}
    assert "uq_pfv_patient_mode_weekday" in pfv_uc_after, (
        f"downgrade 後に旧 UNIQUE が戻るはず, got {pfv_uc_after}"
    )
    assert "uq_pfv_patient_mode_weekday_slot" not in pfv_uc_after

    with engine.begin() as conn:
        idx_names_after = {
            r[0]
            for r in conn.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='visits'")
            ).all()
        }
    assert "uq_visits_pds_group_active" not in idx_names_after
    assert "uq_visits_pds_active" in idx_names_after, (
        f"downgrade 後に 0026 の index が戻るはず, got {idx_names_after}"
    )

    engine.dispose()


def test_pfv_model_has_slot_index_column() -> None:
    """SQLAlchemy モデル側にも slot_index 列が存在する (NOT NULL, default=0)."""
    from app.models.patient_fixed_visit import PatientFixedVisit

    cols = {c.name: c for c in PatientFixedVisit.__table__.columns}
    assert "slot_index" in cols, f"PatientFixedVisit に slot_index が無い: {list(cols)}"
    col = cols["slot_index"]
    assert col.nullable is False, "slot_index は NOT NULL のはず"

    # __table_args__ に新 UNIQUE が定義されている
    uniques = [
        c
        for c in PatientFixedVisit.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    ]
    uq_names = {u.name for u in uniques}
    assert "uq_pfv_patient_mode_weekday_slot" in uq_names, (
        f"モデル側 UNIQUE 名が新形になっていない: {uq_names}"
    )
    assert "uq_pfv_patient_mode_weekday" not in uq_names, (
        f"モデル側 UNIQUE に旧名が残っている: {uq_names}"
    )


def test_pfv_schema_has_slot_index_field() -> None:
    """Pydantic schema 側に slot_index フィールドが存在し, ge=0 le=1 の制約がある."""
    from app.schemas.v2.patient_fixed_visit import PatientFixedVisitV2Base

    fields = PatientFixedVisitV2Base.model_fields
    assert "slot_index" in fields, f"slot_index フィールドが無い: {list(fields)}"
    info = fields["slot_index"]
    # default=0
    assert info.default == 0, f"slot_index default は 0 のはず, got {info.default}"
    # metadata に Ge(0) / Le(1) が含まれる
    metadata_repr = repr(info.metadata)
    assert "Ge(ge=0)" in metadata_repr or "ge=0" in metadata_repr, (
        f"slot_index に ge=0 が無い: {metadata_repr}"
    )
    assert "Le(le=1)" in metadata_repr or "le=1" in metadata_repr, (
        f"slot_index に le=1 が無い: {metadata_repr}"
    )
