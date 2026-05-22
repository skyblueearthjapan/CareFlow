"""Phase G-21 T1: PFV.is_pinned + same-address links + office_feature_flags.

Revision ID: 0037_g21_pfv_is_pinned_and_same_address_links
Revises: 0036_drop_weekday_priority
Create Date: 2026-05-22

## このマイグレーションの責務

自動算出ロジック大規模改修 (G-21) のデータ層を整備する.
3 つの独立した変更を 1 リビジョンで適用する.

### 変更 1: patient_fixed_visits.is_pinned カラム追加

"完全固定フラグ" を表す boolean カラムを新設する.

* 2 段階で適用 (= UPDATE 全行 backfill を避ける):
  1. ``server_default=true`` で NOT NULL カラム追加.
     → 既存全行は ADD COLUMN 時点で true で埋まる (= 後方互換, 既存運用「PFV=固定」を維持).
  2. ``server_default=false`` に切替.
     → 今後の新規 INSERT は false (= default 非 pinned).
* この 2 段階方式により 134 行 (本番想定) を UPDATE する lock 時間を回避.
* `ix_pfv_pinned_patient` partial index: ``is_pinned = true`` の行のみ
  patient_id で高速検索可能にする (PG only).

### 変更 2: patient_same_address_links テーブル新設

同住所患者の紐付け 3 モード (blocked / preferred / required) を扱う.

* 複合主キー (patient_a_id, patient_b_id)
* CHECK 制約 ``patient_a_id < patient_b_id`` で順序を強制 (= 重複行防止)
* pair_mode は PG では ENUM ``same_address_pair_mode`` を作成
* デフォルト ``preferred`` はアプリ層で扱う (DB 行が無ければ preferred 扱い).
  ``blocked`` / ``required`` のみ DB 記録する運用で行数を抑える.

### 変更 3: office_feature_flags テーブル新設

拠点単位の feature flag (canary 3 phase 用).

* (office_id, feature_key) UNIQUE
* feature_key で索引 (例: 'g21_new_algorithm')

## SQLite 互換

* PG: ``postgresql.UUID``, ENUM, partial index を使う.
* SQLite: ``String(36)``, ENUM の代わりに CHECK 制約, partial index は通常 index.

## downgrade

* 変更 3 → 2 → 1 の逆順で完全に元に戻す.
* PG ENUM は ``DROP TYPE`` で削除.
* PFV.is_pinned カラム drop. backfill した値は失われる.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0037_g21_pfv_is_pinned_and_same_address_links"
down_revision: Union[str, Sequence[str], None] = "0036_drop_weekday_priority"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PAIR_MODE_VALUES = ("blocked", "preferred", "required")
_PAIR_MODE_ENUM_NAME = "same_address_pair_mode"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ----- 変更 1: PFV.is_pinned -----
    # Step 1: server_default=true で NOT NULL カラム追加.
    # ADD COLUMN 時点で既存全行が true で埋まる (= UPDATE 全行 backfill 不要).
    op.add_column(
        "patient_fixed_visits",
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment=(
                "Phase G-21: 完全固定フラグ. true = 自動算出で絶対動かさない. "
                "既存全 PFV は ADD COLUMN 時点で true で埋まる (後方互換). "
                "新規 PFV は default false (Step 2 で server_default 切替)."
            ),
        ),
    )
    # Step 2: server_default を false に切り替え (今後の新規 INSERT は非 pinned).
    if is_pg:
        op.alter_column(
            "patient_fixed_visits",
            "is_pinned",
            server_default=sa.text("false"),
        )
    else:
        # SQLite: alter_column の server_default 変更は batch_alter_table 必須.
        with op.batch_alter_table("patient_fixed_visits") as batch_op:
            batch_op.alter_column(
                "is_pinned",
                server_default=sa.text("false"),
                existing_type=sa.Boolean(),
                existing_nullable=False,
            )

    # 部分 index (pinned のみ高速検索) — PG only.
    if is_pg:
        op.create_index(
            "ix_pfv_pinned_patient",
            "patient_fixed_visits",
            ["patient_id"],
            postgresql_where=sa.text("is_pinned = true"),
        )
    else:
        # SQLite: 部分 index は WHERE 句付き CREATE INDEX で同等表現.
        op.create_index(
            "ix_pfv_pinned_patient",
            "patient_fixed_visits",
            ["patient_id"],
            sqlite_where=sa.text("is_pinned = 1"),
        )

    # ----- 変更 2: patient_same_address_links -----
    if is_pg:
        pair_mode_enum = postgresql.ENUM(
            *_PAIR_MODE_VALUES,
            name=_PAIR_MODE_ENUM_NAME,
            create_type=False,
        )
        pair_mode_enum.create(bind, checkfirst=True)
        pair_mode_col: sa.types.TypeEngine = pair_mode_enum
        uuid_type: sa.types.TypeEngine = postgresql.UUID(as_uuid=True)
    else:
        # SQLite: ENUM 相当は CHECK 制約で表現.
        pair_mode_col = sa.String(16)
        uuid_type = sa.String(36)

    same_address_kwargs: list[sa.schema.SchemaItem] = [
        sa.PrimaryKeyConstraint(
            "patient_a_id",
            "patient_b_id",
            name="pk_patient_same_address_links",
        ),
        sa.CheckConstraint(
            "patient_a_id < patient_b_id",
            name="ck_same_address_links_ordered",
        ),
    ]
    if not is_pg:
        # SQLite: ENUM 相当の CHECK.
        same_address_kwargs.append(
            sa.CheckConstraint(
                "pair_mode IN ('blocked','preferred','required')",
                name="ck_same_address_links_pair_mode",
            )
        )

    op.create_table(
        "patient_same_address_links",
        sa.Column(
            "patient_a_id",
            uuid_type,
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "patient_b_id",
            uuid_type,
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pair_mode", pair_mode_col, nullable=False),
        sa.Column(
            "decided_by_user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now() if is_pg else sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now() if is_pg else sa.func.current_timestamp(),
        ),
        *same_address_kwargs,
    )
    op.create_index(
        "ix_pl_partner_a",
        "patient_same_address_links",
        ["patient_a_id"],
    )
    op.create_index(
        "ix_pl_partner_b",
        "patient_same_address_links",
        ["patient_b_id"],
    )

    # ----- 変更 3: office_feature_flags -----
    if is_pg:
        op.create_table(
            "office_feature_flags",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "office_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("offices.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "feature_key",
                sa.String(64),
                nullable=False,
                comment="例: 'g21_new_algorithm'",
            ),
            sa.Column(
                "enabled_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "enabled_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("office_id", "feature_key", name="uq_office_feature_flags"),
        )
    else:
        op.create_table(
            "office_feature_flags",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "office_id",
                sa.String(36),
                sa.ForeignKey("offices.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("feature_key", sa.String(64), nullable=False),
            sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "enabled_by_user_id",
                sa.String(36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.current_timestamp(),
            ),
            sa.UniqueConstraint("office_id", "feature_key", name="uq_office_feature_flags"),
        )

    op.create_index(
        "ix_off_flag_key",
        "office_feature_flags",
        ["feature_key"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ----- 変更 3: office_feature_flags -----
    op.drop_index("ix_off_flag_key", table_name="office_feature_flags")
    op.drop_table("office_feature_flags")

    # ----- 変更 2: patient_same_address_links -----
    op.drop_index("ix_pl_partner_b", table_name="patient_same_address_links")
    op.drop_index("ix_pl_partner_a", table_name="patient_same_address_links")
    op.drop_table("patient_same_address_links")

    if is_pg:
        pair_mode_enum = postgresql.ENUM(
            *_PAIR_MODE_VALUES,
            name=_PAIR_MODE_ENUM_NAME,
            create_type=False,
        )
        pair_mode_enum.drop(bind, checkfirst=True)

    # ----- 変更 1: PFV.is_pinned -----
    op.drop_index("ix_pfv_pinned_patient", table_name="patient_fixed_visits")
    op.drop_column("patient_fixed_visits", "is_pinned")
