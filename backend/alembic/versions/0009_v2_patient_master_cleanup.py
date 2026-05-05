"""W1-BE1 v2 patient master cleanup

Revision ID: 0009_v2_patient_master_cleanup
Revises: 0008_merge_w4d_w4f
Create Date: 2026-05-06

設計仕様書 v0.9 §4.1 に基づき、患者マスタを v2 schema に整理する。

## 変更内容

### 削除カラム (drop)
1. ``patients.age`` -- 年齢 (スケジューリング不使用)
2. ``patients.ng_time_start`` -- NG時間開始 (時間タイプで代替)
3. ``patients.ng_time_end``   -- NG時間終了 (時間タイプで代替)
4. ``patients.specified_type`` -- 指定タイプ (廃止)
5. ``patients.ng_staff_ids``  -- NGスタッフ ID 配列 (廃止)
6. ``patients.preferred_staff_ids`` -- 同行希望スタッフ ID 配列 (廃止)
7. ``patients.continuous_request`` -- 継続要望 boolean (廃止)
8. ``patients.required_staff_count`` -- 必要スタッフ数 (weekly_pattern.staff_count に移行)
9. ``patients.area`` -- エリア (拠点で代替)

(ユーザー指示の "8 項目" は NG時間 (start/end) を 1 項目として数えた値。
 物理的には 9 列を drop する。設計書 §4.1 と整合。)

### 追加カラム (add)
1. ``patients.special_weekly_pattern`` -- 特別週パターン JSONB (§3.4)
2. ``patients.special_week_active`` -- 適用週リスト JSONB
   ``[{iso_year:int, iso_week:int}, ...]`` 形式

なお ``patients.weekly_pattern.staff_count`` は **JSONB 内のキー追加** に
留まり、列追加は不要。アプリケーション側 (schema/v2) で扱う。

## expand-contract 戦略

破壊的な drop column を rollback 可能にするため、drop の前に
``_v1_backup_patients_w1be1`` テーブルへ全データをコピーする。
``downgrade`` ではこのバックアップを参照して列を復元する。

PostgreSQL 本番想定だが、SQLite (テスト用) でも動くよう
JSONB / ARRAY 等は ``sa.JSON`` フォールバック経由。

## Preflight (production)

drop は破壊的。Wave 1 終了時に staging で ``up -> down -> up`` 検証必須。
``_v1_backup_patients_w1be1`` テーブルは Wave 6 (W6-MIG1 / 0015) にて
物理削除する想定。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_v2_patient_master_cleanup"
down_revision: str | Sequence[str] | None = "0008_merge_w4d_w4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BACKUP_TABLE = "_v1_backup_patients_w1be1"

# 削除対象列
_DROP_COLUMNS = (
    "age",
    "ng_time_start",
    "ng_time_end",
    "specified_type",
    "ng_staff_ids",
    "preferred_staff_ids",
    "continuous_request",
    "required_staff_count",
    "area",
)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 1) Backup table -- copy data from soon-to-drop columns so rollback
    #    can restore them. Keyed by patients.id (UUID).
    # ------------------------------------------------------------------
    if is_pg:
        op.create_table(
            _BACKUP_TABLE,
            sa.Column(
                "patient_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("age", sa.SmallInteger(), nullable=True),
            sa.Column("ng_time_start", sa.Time(), nullable=True),
            sa.Column("ng_time_end", sa.Time(), nullable=True),
            sa.Column("specified_type", sa.String(length=16), nullable=True),
            sa.Column(
                "ng_staff_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
            sa.Column(
                "preferred_staff_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
            sa.Column("continuous_request", sa.Boolean(), nullable=True),
            sa.Column("required_staff_count", sa.SmallInteger(), nullable=True),
            sa.Column("area", sa.String(length=16), nullable=True),
            sa.Column(
                "backed_up_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

        op.execute(
            f"""
            INSERT INTO {_BACKUP_TABLE} (
                patient_id, age, ng_time_start, ng_time_end, specified_type,
                ng_staff_ids, preferred_staff_ids, continuous_request,
                required_staff_count, area
            )
            SELECT
                id, age, ng_time_start, ng_time_end, specified_type,
                ng_staff_ids, preferred_staff_ids, continuous_request,
                required_staff_count, area
            FROM patients
            """
        )
    else:
        # SQLite (テスト) フォールバック: ARRAY/JSONB を JSON にダウングレード。
        op.create_table(
            _BACKUP_TABLE,
            sa.Column("patient_id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("age", sa.SmallInteger(), nullable=True),
            sa.Column("ng_time_start", sa.Time(), nullable=True),
            sa.Column("ng_time_end", sa.Time(), nullable=True),
            sa.Column("specified_type", sa.String(length=16), nullable=True),
            sa.Column("ng_staff_ids", sa.JSON(), nullable=True),
            sa.Column("preferred_staff_ids", sa.JSON(), nullable=True),
            sa.Column("continuous_request", sa.Boolean(), nullable=True),
            sa.Column("required_staff_count", sa.SmallInteger(), nullable=True),
            sa.Column("area", sa.String(length=16), nullable=True),
            sa.Column(
                "backed_up_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
        )
        # SQLite テスト環境ではテーブルが create_all で作られるため、本来 alembic
        # は走らない。INSERT は念のためのフェイルセーフ。
        try:
            op.execute(
                f"""
                INSERT INTO {_BACKUP_TABLE} (
                    patient_id, age, ng_time_start, ng_time_end, specified_type,
                    ng_staff_ids, preferred_staff_ids, continuous_request,
                    required_staff_count, area
                )
                SELECT
                    id, age, ng_time_start, ng_time_end, specified_type,
                    ng_staff_ids, preferred_staff_ids, continuous_request,
                    required_staff_count, area
                FROM patients
                """
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 2) Drop legacy columns from patients
    # ------------------------------------------------------------------
    with op.batch_alter_table("patients") as batch:
        for col in _DROP_COLUMNS:
            batch.drop_column(col)

    # ------------------------------------------------------------------
    # 3) Add 2 new JSONB columns for v2 special-week handling
    # ------------------------------------------------------------------
    if is_pg:
        op.add_column(
            "patients",
            sa.Column(
                "special_weekly_pattern",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
        op.add_column(
            "patients",
            sa.Column(
                "special_week_active",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
        )
    else:
        op.add_column(
            "patients",
            sa.Column("special_weekly_pattern", sa.JSON(), nullable=True),
        )
        op.add_column(
            "patients",
            sa.Column("special_week_active", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 1) Drop the v2 added columns
    # ------------------------------------------------------------------
    with op.batch_alter_table("patients") as batch:
        batch.drop_column("special_week_active")
        batch.drop_column("special_weekly_pattern")

    # ------------------------------------------------------------------
    # 2) Re-add the dropped columns (nullable defaults)
    # ------------------------------------------------------------------
    if is_pg:
        op.add_column("patients", sa.Column("age", sa.SmallInteger(), nullable=True))
        op.add_column("patients", sa.Column("ng_time_start", sa.Time(), nullable=True))
        op.add_column("patients", sa.Column("ng_time_end", sa.Time(), nullable=True))
        op.add_column(
            "patients",
            sa.Column("specified_type", sa.String(length=16), nullable=True),
        )
        op.add_column(
            "patients",
            sa.Column(
                "ng_staff_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
        )
        op.add_column(
            "patients",
            sa.Column(
                "preferred_staff_ids",
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=True,
            ),
        )
        op.add_column(
            "patients",
            sa.Column(
                "continuous_request",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            "patients",
            sa.Column(
                "required_staff_count",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )
        op.add_column("patients", sa.Column("area", sa.String(length=16), nullable=True))
    else:
        op.add_column("patients", sa.Column("age", sa.SmallInteger(), nullable=True))
        op.add_column("patients", sa.Column("ng_time_start", sa.Time(), nullable=True))
        op.add_column("patients", sa.Column("ng_time_end", sa.Time(), nullable=True))
        op.add_column(
            "patients",
            sa.Column("specified_type", sa.String(length=16), nullable=True),
        )
        op.add_column("patients", sa.Column("ng_staff_ids", sa.JSON(), nullable=True))
        op.add_column("patients", sa.Column("preferred_staff_ids", sa.JSON(), nullable=True))
        op.add_column(
            "patients",
            sa.Column(
                "continuous_request",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        op.add_column(
            "patients",
            sa.Column(
                "required_staff_count",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )
        op.add_column("patients", sa.Column("area", sa.String(length=16), nullable=True))

    # ------------------------------------------------------------------
    # 3) Restore data from backup table
    # ------------------------------------------------------------------
    if is_pg:
        op.execute(
            f"""
            UPDATE patients SET
                age = b.age,
                ng_time_start = b.ng_time_start,
                ng_time_end = b.ng_time_end,
                specified_type = b.specified_type,
                ng_staff_ids = b.ng_staff_ids,
                preferred_staff_ids = b.preferred_staff_ids,
                continuous_request = COALESCE(b.continuous_request, FALSE),
                required_staff_count = COALESCE(b.required_staff_count, 1),
                area = b.area
            FROM {_BACKUP_TABLE} AS b
            WHERE patients.id = b.patient_id
            """
        )
    else:
        op.execute(
            f"""
            UPDATE patients SET
                age = (SELECT age FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                ng_time_start = (SELECT ng_time_start FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                ng_time_end = (SELECT ng_time_end FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                specified_type = (SELECT specified_type FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                ng_staff_ids = (SELECT ng_staff_ids FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                preferred_staff_ids = (SELECT preferred_staff_ids FROM {_BACKUP_TABLE} WHERE patient_id = patients.id),
                continuous_request = COALESCE((SELECT continuous_request FROM {_BACKUP_TABLE} WHERE patient_id = patients.id), 0),
                required_staff_count = COALESCE((SELECT required_staff_count FROM {_BACKUP_TABLE} WHERE patient_id = patients.id), 1),
                area = (SELECT area FROM {_BACKUP_TABLE} WHERE patient_id = patients.id)
            """
        )

    # ------------------------------------------------------------------
    # 4) Drop the backup table
    # ------------------------------------------------------------------
    op.drop_table(_BACKUP_TABLE)
