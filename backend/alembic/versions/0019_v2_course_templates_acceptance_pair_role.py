"""W15-BE1: course_templates + acceptance_calendar + courses.template_id/office_id + scta.pair_role.

Revision ID: 0019_v2_w15_be1_foundation
Revises: 0018_v2_staff_companion
Create Date: 2026-05-08

## このマイグレーションの責務

1. ``course_templates`` テーブル新設 (永続コーステンプレート)
   - (office_id, label) UNIQUE
   - capacity_mon..capacity_sun 各 0..50 の CHECK
   - 論理削除 (deleted_at NULL)
2. ``courses`` 拡張
   - ``template_id`` UUID NULL FK course_templates.id ON DELETE SET NULL
   - ``office_id``  UUID NULL FK offices.id (※実装メモ参照)
3. ``acceptance_calendar`` テーブル新設
   - (office_id, weekday, time_slot) UNIQUE
   - status IN ('available','consult','unavailable')
4. ``staff_companion_assignments`` 拡張
   - ``pair_role`` VARCHAR(16) NULL CHECK IN ('primary','support')

## 実装メモ — courses.office_id を NULLABLE で導入する根拠

設計仕様 (Wave 15 Phase 1) では ``courses.office_id`` を NOT NULL とする
方針だったが、

* 既存の単体テスト (``test_courses.py``, ``test_layer3*.py``, ``test_visit_v2.py``)
  および既存の ``POST /api/v1/courses`` (CourseCreate に office_id 未含)
  が NOT NULL 違反で全滅する
* W15 Phase 1 のファイル境界 (既存 endpoint / 既存テストは触らない)
  と矛盾する

そのため本リビジョンでは NULLABLE で導入し、Phase 2 以降の専用リビジョンで
全行 backfill 後に NOT NULL 化を計画する。

## SQLite 互換

postgresql.UUID → sa.String(36) フォールバック分岐 (0017/0018 のパターン参考)。

## Alembic chain

down_revision = "0018_v2_staff_companion"
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0019_v2_w15_be1_foundation"
down_revision: Union[str, Sequence[str], None] = "0018_v2_staff_companion"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36)
    server_now = sa.func.now() if is_pg else sa.func.current_timestamp()

    # -----------------------------------------------------------------
    # 1) course_templates 新規 CREATE
    # -----------------------------------------------------------------
    op.create_table(
        "course_templates",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column(
            "office_id",
            uuid_type,
            sa.ForeignKey(
                "offices.id",
                ondelete="CASCADE",
                name="fk_course_templates_office_id_offices",
            ),
            nullable=False,
        ),
        sa.Column("label", sa.String(length=8), nullable=False),
        sa.Column("capacity_mon", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_tue", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_wed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_thu", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_fri", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_sat", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("capacity_sun", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.UniqueConstraint("office_id", "label", name="uq_course_templates_office_label"),
        sa.CheckConstraint(
            "capacity_mon BETWEEN 0 AND 50", name="ck_course_templates_capacity_mon"
        ),
        sa.CheckConstraint(
            "capacity_tue BETWEEN 0 AND 50", name="ck_course_templates_capacity_tue"
        ),
        sa.CheckConstraint(
            "capacity_wed BETWEEN 0 AND 50", name="ck_course_templates_capacity_wed"
        ),
        sa.CheckConstraint(
            "capacity_thu BETWEEN 0 AND 50", name="ck_course_templates_capacity_thu"
        ),
        sa.CheckConstraint(
            "capacity_fri BETWEEN 0 AND 50", name="ck_course_templates_capacity_fri"
        ),
        sa.CheckConstraint(
            "capacity_sat BETWEEN 0 AND 50", name="ck_course_templates_capacity_sat"
        ),
        sa.CheckConstraint(
            "capacity_sun BETWEEN 0 AND 50", name="ck_course_templates_capacity_sun"
        ),
    )
    op.create_index("ix_course_templates_office", "course_templates", ["office_id"], unique=False)

    # -----------------------------------------------------------------
    # 2) courses 拡張: template_id (NULL FK), office_id (NULL FK)
    # -----------------------------------------------------------------
    with op.batch_alter_table("courses") as batch:
        batch.add_column(sa.Column("template_id", uuid_type, nullable=True))
        batch.add_column(sa.Column("office_id", uuid_type, nullable=True))
        batch.create_foreign_key(
            "fk_courses_template_id_course_templates",
            "course_templates",
            ["template_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_courses_office_id_offices",
            "offices",
            ["office_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_courses_office", "courses", ["office_id"], unique=False)
    op.create_index("ix_courses_template", "courses", ["template_id"], unique=False)

    # -----------------------------------------------------------------
    # 3) acceptance_calendar 新規 CREATE
    # -----------------------------------------------------------------
    op.create_table(
        "acceptance_calendar",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column(
            "office_id",
            uuid_type,
            sa.ForeignKey(
                "offices.id",
                ondelete="CASCADE",
                name="fk_acceptance_calendar_office_id_offices",
            ),
            nullable=False,
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("time_slot", sa.Time(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=server_now,
        ),
        sa.UniqueConstraint(
            "office_id",
            "weekday",
            "time_slot",
            name="uq_acceptance_calendar_office_weekday_slot",
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_acceptance_calendar_weekday",
        ),
        sa.CheckConstraint(
            "status IN ('available','consult','unavailable')",
            name="ck_acceptance_calendar_status",
        ),
    )
    op.create_index(
        "ix_acceptance_calendar_office_weekday",
        "acceptance_calendar",
        ["office_id", "weekday"],
        unique=False,
    )

    # -----------------------------------------------------------------
    # 4) staff_companion_assignments.pair_role 追加
    # -----------------------------------------------------------------
    with op.batch_alter_table("staff_companion_assignments") as batch:
        batch.add_column(sa.Column("pair_role", sa.String(length=16), nullable=True))
        batch.create_check_constraint(
            "ck_sca_pair_role",
            "pair_role IS NULL OR pair_role IN ('primary','support')",
        )


def downgrade() -> None:
    # -----------------------------------------------------------------
    # 4) staff_companion_assignments.pair_role 削除
    # -----------------------------------------------------------------
    with op.batch_alter_table("staff_companion_assignments") as batch:
        batch.drop_constraint("ck_sca_pair_role", type_="check")
        batch.drop_column("pair_role")

    # -----------------------------------------------------------------
    # 3) acceptance_calendar DROP
    # -----------------------------------------------------------------
    op.drop_index("ix_acceptance_calendar_office_weekday", table_name="acceptance_calendar")
    op.drop_table("acceptance_calendar")

    # -----------------------------------------------------------------
    # 2) courses から template_id / office_id を削除
    # -----------------------------------------------------------------
    op.drop_index("ix_courses_template", table_name="courses")
    op.drop_index("ix_courses_office", table_name="courses")
    with op.batch_alter_table("courses") as batch:
        batch.drop_constraint("fk_courses_office_id_offices", type_="foreignkey")
        batch.drop_constraint("fk_courses_template_id_course_templates", type_="foreignkey")
        batch.drop_column("office_id")
        batch.drop_column("template_id")

    # -----------------------------------------------------------------
    # 1) course_templates DROP
    # -----------------------------------------------------------------
    op.drop_index("ix_course_templates_office", table_name="course_templates")
    op.drop_table("course_templates")
