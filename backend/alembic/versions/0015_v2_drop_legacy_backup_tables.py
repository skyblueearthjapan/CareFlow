"""W6-MIG1: drop legacy v1 backup tables (expand-contract 第 2 段).

Revision ID: 0015_v2_drop_legacy_backup_tables
Revises: 0014_v2_ai_scope_metadata
Create Date: 2026-05-06

設計仕様書 v0.9 §8 / 実装手順書 v0.2 §7 W6-MIG1 / 番号予約表 §1 0015。

## このマイグレーションの責務

Wave 1 で導入した expand-contract 戦略の **第 2 段 (物理 DROP)** を実施する。
W1-BE1 (0009) と W1-BE2 (0010) はそれぞれ削除対象列をバックアップ表へ
退避してから DROP するという 2 段階方式を採用していた。Wave 6 (本 revision)
は **退避テーブルそのものを物理 DROP** し、v1 → v2 移行を完結させる。

### 削除対象テーブル

| テーブル名 | 作成 revision | 内容 |
|---|---|---|
| ``_v1_backup_patients_w1be1`` | 0009_v2_patient_master_cleanup | 患者の v1 列バックアップ (age, ng_time_*, area, ...) |
| ``_v1_backup_staff_w1be2``    | 0010_v2_staff_master_cleanup  | スタッフの v1 列バックアップ (home_*, areas, max_per_day, ...) |

### 不可逆性

本マイグレーションは **データを物理的に消去する**。Alembic 上の
``downgrade`` は **空のバックアップテーブルを再作成するのみ**であり、
**データの復元は不可能**である (バックアップ表の中身は drop 時点で消える)。

そのため、本 revision を本番適用する前に必ず以下を実施すること:

1. 本番 DB の論理 / 物理スナップショット (pg_dump, RDS スナップショット 等)
2. ``_v1_backup_patients_w1be1`` / ``_v1_backup_staff_w1be2`` を
   `migrate_v1_to_v2.py --dry-run` で読みつつ、当該データが
   v2 schema (``patients.weekly_pattern.entries[]`` /
   ``patients.special_weekly_pattern`` / ``patients.special_week_active``)
   へ正しく転写されているかを検証
3. 上司の承認を得てから ``alembic upgrade head`` を実行

### Alembic chain での位置付け

- ``Wave 6`` の sequential phase (実装手順書 §7) で実行する想定
- 番号は予約表で **0015** に予約済み (W6-MIG2 = 0016 と独立)
- 本 revision は **DDL のみ**で完結 (データ移行は ``migrate_v1_to_v2.py``
  が担当)。線形 chain を保つために実体を持たない number-only revision
  ではなく、明示的に DROP TABLE を発行する

## SQLite 互換性

CI / 単体テストは SQLite を使う (``conftest.py``)。SQLite では
``DROP TABLE IF EXISTS`` が動くため、本 revision は方言非依存。
``_v1_backup_*`` テーブルは Wave 1 の migration が SQLite では実行されない
(テストは ``Base.metadata.create_all`` を使うため) 関係で、SQLite 上は
そもそも存在しない。``DROP TABLE IF EXISTS`` で安全にスキップされる。

PostgreSQL 本番では Wave 1 の migration を経由しているため、テーブルが
存在する前提で DROP TABLE を発行する。
"""

# ruff: noqa: I001, UP007, UP035
# 既存の Wave 1 / v1 alembic revisions と同じスタイル (typing.Union / typing.Sequence)
# を維持するため lint を抑制する。
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0015_v2_drop_legacy_backup_tables"
down_revision: Union[str, Sequence[str], None] = "0014_v2_ai_scope_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 削除対象テーブル名 (Wave 1 の expand-contract 戦略で作成済み)
_PATIENT_BACKUP = "_v1_backup_patients_w1be1"
_STAFF_BACKUP = "_v1_backup_staff_w1be2"


def upgrade() -> None:
    """v1 バックアップテーブルを物理 DROP する (不可逆)."""
    # IF EXISTS で SQLite テスト環境での冪等性を担保。本番 PostgreSQL では
    # Wave 1 で作成済みのため必ず存在する。
    op.execute(sa.text(f'DROP TABLE IF EXISTS "{_PATIENT_BACKUP}"'))
    op.execute(sa.text(f'DROP TABLE IF EXISTS "{_STAFF_BACKUP}"'))


def downgrade() -> None:
    """形式的にバックアップ表を再作成する (中身は空、データ復元は不可能).

    本来 expand-contract の第 2 段は不可逆だが、Alembic chain の整合性を
    保つために空テーブルを再作成する。データの復元は本 ``downgrade`` では
    なく、運用上の DB スナップショット復元で行う前提。
    """
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # _v1_backup_patients_w1be1 (0009 と同じ shape) を再作成
    # ------------------------------------------------------------------
    if is_pg:
        op.create_table(
            _PATIENT_BACKUP,
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
    else:
        op.create_table(
            _PATIENT_BACKUP,
            sa.Column(
                "patient_id",
                sa.String(length=36),
                primary_key=True,
                nullable=False,
            ),
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

    # ------------------------------------------------------------------
    # _v1_backup_staff_w1be2 (0010 と同じ shape) を再作成
    # ------------------------------------------------------------------
    if is_pg:
        op.create_table(
            _STAFF_BACKUP,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("can_double_team", sa.Boolean(), nullable=True),
            sa.Column("home_address", sa.String(length=255), nullable=True),
            sa.Column("home_lat", sa.Numeric(10, 7), nullable=True),
            sa.Column("home_lng", sa.Numeric(10, 7), nullable=True),
            sa.Column(
                "areas",
                postgresql.ARRAY(sa.String(length=16)),
                nullable=True,
            ),
            sa.Column("max_per_day", sa.Integer(), nullable=True),
            sa.Column("skill_level", sa.String(length=16), nullable=True),
            sa.Column("assignment_volume", sa.String(length=16), nullable=True),
            sa.Column(
                "backed_up_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id", name=f"pk_{_STAFF_BACKUP}"),
        )
    else:
        op.create_table(
            _STAFF_BACKUP,
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("can_double_team", sa.Boolean(), nullable=True),
            sa.Column("home_address", sa.String(length=255), nullable=True),
            sa.Column("home_lat", sa.Numeric(10, 7), nullable=True),
            sa.Column("home_lng", sa.Numeric(10, 7), nullable=True),
            sa.Column("areas", sa.JSON(), nullable=True),
            sa.Column("max_per_day", sa.Integer(), nullable=True),
            sa.Column("skill_level", sa.String(length=16), nullable=True),
            sa.Column("assignment_volume", sa.String(length=16), nullable=True),
            sa.Column(
                "backed_up_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.PrimaryKeyConstraint("id", name=f"pk_{_STAFF_BACKUP}"),
        )
