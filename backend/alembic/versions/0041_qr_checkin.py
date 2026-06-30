"""QR 訪問チェックイン Phase 1: patients QR 列 + visit_checkins + checkin_settings.

Revision ID: 0041_qr_checkin
Revises: 0040_acceptance_calendar_week
Create Date: 2026-06-30

## このマイグレーションの責務

QR 訪問チェックインの記録基盤を新設する (additive)。

1. ``patients`` に ``qr_token`` (String(32) NULL) / ``qr_version`` (Integer NOT
   NULL default 1) を追加。非 NULL のみ重複を禁じる部分 UNIQUE
   ``uq_patients_qr_token`` (両 dialect の ``qr_token IS NOT NULL`` 述語)。
   トークン値は生成しない (遅延発行 / 一括スクリプトで採番 = 軽量・可逆)。
2. 新テーブル ``visit_checkins`` (append-only 監査ログ)。監査証跡を守るため
   ``visit_id`` / ``patient_id`` は **ON DELETE RESTRICT**、``staff_id`` /
   ``reviewed_by`` は SET NULL。kind / match_status の値域 CHECK。
3. 新テーブル ``checkin_settings`` (全社一律しきい値・シングルトン)。
   ``scheduling_settings`` を雛形 (nullable 列 = 既定 fallback、range CHECK、
   交差 CHECK ``review_gte_match``、is_singleton 部分 UNIQUE)。

## SQLite 互換

PG: ``postgresql.UUID`` / ``gen_random_uuid()`` / ``postgresql_where``。
SQLite: ``String(36)`` / ``sqlite_where``。``scanned_at DESC`` index は
``sa.text()`` で両 dialect 共通に表現。

## downgrade

visit_checkins / checkin_settings を drop、patients の qr_token / qr_version を
drop_column。downgrade で checkin データは失われる (適用前 ``pg_dump`` 必須)。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0041_qr_checkin"
down_revision: Union[str, Sequence[str], None] = "0040_acceptance_calendar_week"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type: sa.types.TypeEngine = (
        postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    )
    jsonb_type: sa.types.TypeEngine = postgresql.JSONB() if is_pg else sa.JSON()
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()
    singleton_default = sa.text("true") if is_pg else sa.text("1")
    id_server_default = sa.text("gen_random_uuid()") if is_pg else None

    # ---- 1. patients QR 列 --------------------------------------------------
    op.add_column("patients", sa.Column("qr_token", sa.String(length=32), nullable=True))
    op.add_column(
        "patients",
        sa.Column("qr_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    if is_pg:
        op.create_index(
            "uq_patients_qr_token",
            "patients",
            ["qr_token"],
            unique=True,
            postgresql_where=sa.text("qr_token IS NOT NULL"),
        )
    else:
        op.create_index(
            "uq_patients_qr_token",
            "patients",
            ["qr_token"],
            unique=True,
            sqlite_where=sa.text("qr_token IS NOT NULL"),
        )

    # ---- 2. visit_checkins --------------------------------------------------
    op.create_table(
        "visit_checkins",
        sa.Column("id", uuid_type, primary_key=True, server_default=id_server_default),
        sa.Column(
            "visit_id",
            uuid_type,
            sa.ForeignKey(
                "visits.id",
                ondelete="RESTRICT",
                name="fk_visit_checkins_visit_id_visits",
            ),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            uuid_type,
            sa.ForeignKey(
                "patients.id",
                ondelete="RESTRICT",
                name="fk_visit_checkins_patient_id_patients",
            ),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey(
                "staff.id",
                ondelete="SET NULL",
                name="fk_visit_checkins_staff_id_staff",
            ),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("accuracy_m", sa.Numeric(7, 1), nullable=True),
        sa.Column("distance_m", sa.Numeric(9, 1), nullable=True),
        sa.Column("match_status", sa.String(length=12), nullable=False),
        sa.Column("threshold_snapshot", jsonb_type, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "is_override",
            sa.Boolean(),
            nullable=False,
            # is_singleton (下記) と同じく dialect 分岐で揃える (SQLite は真偽
            # リテラルに 0/1 を使う; PG は false)。一貫性のため明示分岐する。
            server_default=sa.text("false") if is_pg else sa.text("0"),
        ),
        sa.Column(
            "checkin_source",
            sa.String(length=12),
            nullable=False,
            server_default=sa.text("'qr'"),
        ),
        sa.Column(
            "reviewed_by",
            uuid_type,
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_visit_checkins_reviewed_by_users",
            ),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind IN ('arrival','departure','no_show')",
            name="ck_visit_checkins_kind",
        ),
        sa.CheckConstraint(
            "match_status IN ('match','review','mismatch','no_gps')",
            name="ck_visit_checkins_match_status",
        ),
        sa.CheckConstraint(
            "checkin_source IN ('qr','manual')",
            name="ck_visit_checkins_checkin_source",
        ),
    )
    op.create_index(
        "ix_visit_checkins_visit_kind_scanned",
        "visit_checkins",
        ["visit_id", "kind", sa.text("scanned_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_visit_checkins_staff_scanned",
        "visit_checkins",
        ["staff_id", "scanned_at"],
        unique=False,
    )

    # ---- 3. checkin_settings (シングルトン) ----------------------------------
    id_column = (
        sa.Column("id", uuid_type, primary_key=True, server_default=id_server_default)
        if is_pg
        else sa.Column("id", uuid_type, primary_key=True)
    )
    op.create_table(
        "checkin_settings",
        id_column,
        sa.Column(
            "is_singleton",
            sa.Boolean(),
            nullable=False,
            server_default=singleton_default,
        ),
        sa.Column("match_m", sa.Integer(), nullable=True),
        sa.Column("review_m", sa.Integer(), nullable=True),
        sa.Column("accuracy_m", sa.Integer(), nullable=True),
        sa.Column("no_show_grace_min", sa.Integer(), nullable=True),
        sa.Column("late_min", sa.Integer(), nullable=True),
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
            "match_m IS NULL OR (match_m >= 10 AND match_m <= 1000)",
            name="ck_checkin_settings_match_m_range",
        ),
        sa.CheckConstraint(
            "review_m IS NULL OR (review_m >= 10 AND review_m <= 2000)",
            name="ck_checkin_settings_review_m_range",
        ),
        sa.CheckConstraint(
            "accuracy_m IS NULL OR (accuracy_m >= 5 AND accuracy_m <= 500)",
            name="ck_checkin_settings_accuracy_m_range",
        ),
        sa.CheckConstraint(
            "no_show_grace_min IS NULL OR (no_show_grace_min >= 0 AND no_show_grace_min <= 240)",
            name="ck_checkin_settings_no_show_grace_range",
        ),
        sa.CheckConstraint(
            "late_min IS NULL OR (late_min >= 0 AND late_min <= 240)",
            name="ck_checkin_settings_late_min_range",
        ),
        sa.CheckConstraint(
            "review_m IS NULL OR match_m IS NULL OR review_m >= match_m",
            name="ck_checkin_settings_review_gte_match",
        ),
    )
    if is_pg:
        op.create_index(
            "uq_checkin_settings_singleton",
            "checkin_settings",
            ["is_singleton"],
            unique=True,
            postgresql_where=sa.text("is_singleton = true"),
        )
    else:
        op.create_index(
            "uq_checkin_settings_singleton",
            "checkin_settings",
            ["is_singleton"],
            unique=True,
            sqlite_where=sa.text("is_singleton = 1"),
        )


def downgrade() -> None:
    """注意: 本番でこの migration を巻き戻すと checkin 記録は失われる.

    本番運用では downgrade 前に必ず ``pg_dump`` でバックアップを取得すること.
    """
    op.drop_index("uq_checkin_settings_singleton", table_name="checkin_settings")
    op.drop_table("checkin_settings")

    op.drop_index("ix_visit_checkins_staff_scanned", table_name="visit_checkins")
    op.drop_index("ix_visit_checkins_visit_kind_scanned", table_name="visit_checkins")
    op.drop_table("visit_checkins")

    op.drop_index("uq_patients_qr_token", table_name="patients")
    op.drop_column("patients", "qr_version")
    op.drop_column("patients", "qr_token")
