"""W2-BE4 v2 courses + visit extension + visit_staff_assignments

Revision ID: 0012_v2_courses_and_visit_extension
Revises: 0011_v2_office_seed_and_assigner
Create Date: 2026-05-06

設計仕様書 v0.9 §3.3 / §4.5 / API 契約 §4 / §5 / 実装手順書 §3 W2-BE4 /
migration 予約表 0012 番号。

## 変更内容

### 1) ``courses`` テーブル新設 (§4.5)

| 列                  | 型        | 制約                                  |
| ---                 | ---       | ---                                   |
| id                  | UUID      | PK                                    |
| iso_year            | SMALLINT  | NOT NULL                              |
| iso_week            | SMALLINT  | NOT NULL                              |
| weekday             | SMALLINT  | NOT NULL, 0..6                        |
| code                | STRING(2) | NOT NULL, IN ('A','B','C','D','M')   |
| course_status       | STRING(16)| NOT NULL, IN ('proposed','course_fixed','staff_assigned') |
| assigned_staff_id   | UUID      | FK staff.id ON DELETE SET NULL        |
| course_fixed_at     | TIMESTAMP | NULL                                  |
| staff_assigned_at   | TIMESTAMP | NULL                                  |
| note                | TEXT      | NULL                                  |
| deleted_at          | TIMESTAMP | NULL                                  |
| created_at          | TIMESTAMP | NOT NULL DEFAULT now()                |
| updated_at          | TIMESTAMP | NOT NULL DEFAULT now()                |

UNIQUE (iso_year, iso_week, weekday, code)

### 2) ``visits`` テーブル拡張 (§3.3 / §4.5) — expand-only

新規カラムを追加するだけで、削除はしない (expand-contract 第 1 段)。

| 列                     | 型       | 制約                              |
| ---                    | ---      | ---                               |
| course_id              | UUID     | FK courses.id ON DELETE SET NULL  |
| required_staff_count   | SMALLINT | NOT NULL DEFAULT 1, IN (1, 2)     |
| visit_group_id         | UUID     | NULL                              |

既存行は ``required_staff_count = 1`` でバックフィル (server_default 1)。

### 3) ``visit_staff_assignments`` テーブル新設 (§4.5)

| 列         | 型        | 制約                          |
| ---        | ---       | ---                           |
| visit_id   | UUID      | PK / FK visits.id CASCADE     |
| staff_id   | UUID      | PK / FK staff.id CASCADE      |
| created_at | TIMESTAMP | NOT NULL DEFAULT now()        |

PK = (visit_id, staff_id) — 複合主キー。

## expand-contract

本マイグレーションは **追加のみ** で削除を伴わないため、Wave 6 (W6-MIG1)
での second pass は不要。バックアップテーブルも作成しない。

## SQLite 互換性

CI / 単体テストは SQLite (conftest)。``op.batch_alter_table`` を使い
PostgreSQL / SQLite の双方で動作するように記述する。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_v2_courses_and_visit_extension"
down_revision: str | None = "0011_v2_office_seed_and_assigner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # -----------------------------------------------------------------
    # 1) courses テーブル新設 (§4.5)
    # -----------------------------------------------------------------
    op.create_table(
        "courses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("iso_year", sa.SmallInteger(), nullable=False),
        sa.Column("iso_week", sa.SmallInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column(
            "course_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column(
            "assigned_staff_id",
            postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
            nullable=True,
        ),
        sa.Column("course_fixed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("staff_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["assigned_staff_id"],
            ["staff.id"],
            name="fk_courses_assigned_staff_id_staff",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "iso_year",
            "iso_week",
            "weekday",
            "code",
            name="uq_courses_year_week_weekday_code",
        ),
        sa.CheckConstraint(
            "code IN ('A', 'B', 'C', 'D', 'M')",
            name="ck_courses_code_v2",
        ),
        sa.CheckConstraint(
            "course_status IN ('proposed', 'course_fixed', 'staff_assigned')",
            name="ck_courses_status_v2",
        ),
        sa.CheckConstraint(
            "weekday >= 0 AND weekday <= 6",
            name="ck_courses_weekday_range",
        ),
    )
    op.create_index("ix_courses_year_week", "courses", ["iso_year", "iso_week"], unique=False)
    op.create_index(
        "ix_courses_assigned_staff",
        "courses",
        ["assigned_staff_id"],
        unique=False,
    )

    # -----------------------------------------------------------------
    # 2) visits テーブル拡張 (§3.3 / §4.5) — expand-only
    # -----------------------------------------------------------------
    # course_id (FK courses.id NULL ON DELETE SET NULL)
    # required_staff_count SMALLINT NOT NULL DEFAULT 1 (CHECK IN (1,2))
    # visit_group_id UUID NULL
    with op.batch_alter_table("visits") as batch:
        batch.add_column(
            sa.Column(
                "course_id",
                postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "required_staff_count",
                sa.SmallInteger(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )
        batch.add_column(
            sa.Column(
                "visit_group_id",
                postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_visits_course_id_courses",
            "courses",
            ["course_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_visits_required_staff_count_v2",
            "required_staff_count IN (1, 2)",
        )

    # 既存データのバックフィル: required_staff_count = 1 を明示的に設定
    # (server_default で既に 1 が入っているはずだが、保険として明示)
    op.execute(
        sa.text("UPDATE visits SET required_staff_count = 1 WHERE required_staff_count IS NULL")
    )

    # 補助インデックス (W2-BE4 で追加)
    op.create_index("ix_visits_course", "visits", ["course_id"], unique=False)
    op.create_index("ix_visits_group", "visits", ["visit_group_id"], unique=False)

    # -----------------------------------------------------------------
    # 3) visit_staff_assignments テーブル新設 (§4.5)
    # -----------------------------------------------------------------
    op.create_table(
        "visit_staff_assignments",
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            postgresql.UUID(as_uuid=True) if is_pg else sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"],
            ["visits.id"],
            name="fk_visit_staff_assignments_visit_id_visits",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff.id"],
            name="fk_visit_staff_assignments_staff_id_staff",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("visit_id", "staff_id", name="pk_visit_staff_assignments"),
    )
    op.create_index(
        "ix_visit_staff_assignments_staff",
        "visit_staff_assignments",
        ["staff_id"],
        unique=False,
    )


def downgrade() -> None:
    # -----------------------------------------------------------------
    # 3) visit_staff_assignments DROP
    # -----------------------------------------------------------------
    op.drop_index(
        "ix_visit_staff_assignments_staff",
        table_name="visit_staff_assignments",
    )
    op.drop_table("visit_staff_assignments")

    # -----------------------------------------------------------------
    # 2) visits 拡張カラム DROP
    # -----------------------------------------------------------------
    op.drop_index("ix_visits_group", table_name="visits")
    op.drop_index("ix_visits_course", table_name="visits")
    with op.batch_alter_table("visits") as batch:
        batch.drop_constraint("ck_visits_required_staff_count_v2", type_="check")
        batch.drop_constraint("fk_visits_course_id_courses", type_="foreignkey")
        batch.drop_column("visit_group_id")
        batch.drop_column("required_staff_count")
        batch.drop_column("course_id")

    # -----------------------------------------------------------------
    # 1) courses DROP
    # -----------------------------------------------------------------
    op.drop_index("ix_courses_assigned_staff", table_name="courses")
    op.drop_index("ix_courses_year_week", table_name="courses")
    op.drop_table("courses")
