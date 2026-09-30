"""実績の時刻を合わせる: visit_time_adjustments テーブル.

Revision ID: 0088_visit_time_adjustments
Revises: 0087_visit_recordings_summary_edit
Create Date: 2026-09-30

## このマイグレーションの責務

正典設計書 ``docs/plans/actual-time-adjust-design-2026-09-30.md`` §4。

QR は家に入ってから読むので、記録上の到着が実際より遅くなる。スタッフまたは
管理者が実績の時刻を実際に合わせた記録を置く新テーブル ``visit_time_adjustments``
を新設する (additive・**追記専用**)。既存の表・行には触れない。

* ``visit_id`` は監査証跡として **ON DELETE RESTRICT** (``visit_checkins`` と同じ)。
* ``kind`` は ``arrival`` / ``departure`` (CHECK)。
* ``adjusted_at`` が NULL の行 = 「読取時刻に戻す」操作の記録。
* ``base_checkin_id`` は調整の元になった打刻 (SET NULL)。読み取りの無い退出を
  手で入れた場合は NULL。
* ``created_by_user_id`` / ``created_by_staff_id`` は SET NULL (人事異動でも記録は残す)。
* ``prev_visit_status`` / ``prev_visit_end_time`` は、読み取りの無い退出を手で入れる
  前の ``visits.status`` と予定外訪問の ``visits.end_time`` (読取時刻に戻すときに使う)。
* CHECK の名前は 0041 / 0044 と同じ書き方。PostgreSQL では ``op.create_table`` が
  ``target_metadata`` の命名規約を引き継ぐので、実名は
  ``ck_visit_time_adjustments_ck_visit_time_adjustments_kind`` になる
  (``visit_checkins`` の ``ck_visit_checkins_ck_visit_checkins_kind`` と同じ形。
  モデル ``VisitTimeAdjustment`` も同じ名前になる)。
* index ``(visit_id, kind, created_at DESC)`` = 「効いている調整」の取得用。

## SQLite 互換

0041 / 0044 と同じ dialect 分岐: PG = ``postgresql.UUID`` / ``gen_random_uuid()`` /
``now()``、SQLite = ``String(36)`` / ``CURRENT_TIMESTAMP``。

## downgrade

``visit_time_adjustments`` を drop する。downgrade で調整の記録は失われ、実績の
時刻は読取時刻に戻る (``visit_checkins`` は無傷)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0088_visit_time_adjustments"
down_revision: str | Sequence[str] | None = "0087_visit_recordings_summary_edit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "visit_time_adjustments"
_INDEX = "ix_visit_time_adjustments_visit_kind_created"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type: sa.types.TypeEngine = (
        postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    )
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()

    id_column = (
        sa.Column("id", uuid_type, primary_key=True, server_default=sa.text("gen_random_uuid()"))
        if is_pg
        else sa.Column("id", uuid_type, primary_key=True)
    )
    op.create_table(
        _TABLE,
        id_column,
        sa.Column(
            "visit_id",
            uuid_type,
            sa.ForeignKey(
                "visits.id",
                ondelete="RESTRICT",
                name="fk_visit_time_adjustments_visit_id_visits",
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column(
            "adjusted_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="合わせた時刻 (分単位)。NULL = 読取時刻に戻す",
        ),
        sa.Column(
            "base_checkin_id",
            uuid_type,
            sa.ForeignKey(
                "visit_checkins.id",
                ondelete="SET NULL",
                name="fk_visit_time_adjustments_base_checkin_id_visit_checkins",
            ),
            nullable=True,
        ),
        sa.Column("reason_code", sa.String(length=24), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=12), nullable=False),
        sa.Column(
            "created_by_user_id",
            uuid_type,
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_visit_time_adjustments_created_by_user_id_users",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_by_staff_id",
            uuid_type,
            sa.ForeignKey(
                "staff.id",
                ondelete="SET NULL",
                name="fk_visit_time_adjustments_created_by_staff_id_staff",
            ),
            nullable=True,
        ),
        sa.Column(
            "prev_visit_status",
            sa.String(length=16),
            nullable=True,
            comment="読み取りの無い退出を手で入れる前の visits.status",
        ),
        sa.Column(
            "prev_visit_end_time",
            sa.Time(),
            nullable=True,
            comment="同じく、予定外訪問の visits.end_time (通常の訪問は NULL)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.CheckConstraint(
            "kind IN ('arrival','departure')",
            name="ck_visit_time_adjustments_kind",
        ),
    )
    op.create_index(
        _INDEX,
        _TABLE,
        ["visit_id", "kind", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
