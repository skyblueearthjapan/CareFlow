"""W3-A master extensions: patients/staff added cols + special_weeks/items

Revision ID: 0004_w3_master_extensions
Revises: 0003_kaipoke_jobs_geocoding_ai
Create Date: 2026-05-05

Wave 3-A (Master schema extension):
  * patients: + area, ng_staff_ids, preferred_staff_ids, specified_type,
              continuous_request
  * staff:    + home_address, home_lat, home_lng, areas, max_per_day,
              skill_level, assignment_volume
              (`note` was already present from 0001 — not re-added)
  * special_weeks: 1 ヘッダ (週単位) + N 明細 (special_week_items)
                   ADD/REPLACE モードで利用者の通常パターンを上書き / 追加。

## Preflight (production)

This migration only adds new columns and new tables — no destructive
changes — so it is safe to run on a populated DB.

`patients.continuous_request` and `staff.max_per_day` are NOT NULL with
server defaults so existing rows backfill cleanly. Array columns
(`patients.ng_staff_ids`, `patients.preferred_staff_ids`, `staff.areas`)
are nullable and default to NULL on existing rows; client code should
treat NULL as the empty list.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_w3_master_extensions"
down_revision: Union[str, None] = "0003_kaipoke_jobs_geocoding_ai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- patients: 5 added columns -----
    op.add_column(
        "patients",
        sa.Column("area", sa.String(length=16), nullable=True),
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
        sa.Column("specified_type", sa.String(length=16), nullable=True),
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

    # ----- staff: 8 added columns -----
    op.add_column(
        "staff",
        sa.Column("home_address", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("home_lat", sa.Numeric(10, 7), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("home_lng", sa.Numeric(10, 7), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column(
            "areas",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=True,
        ),
    )
    op.add_column(
        "staff",
        sa.Column(
            "max_per_day",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )
    op.add_column(
        "staff",
        sa.Column("skill_level", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("assignment_volume", sa.String(length=16), nullable=True),
    )
    # `staff.note` already exists from 0001 — do NOT re-add.

    # ----- special_weeks (parent header) -----
    op.create_table(
        "special_weeks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("week_end", sa.Date(), nullable=False),
        sa.Column(
            "apply_mode",
            sa.String(length=16),
            nullable=False,
            server_default="ADD",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
            name="fk_special_weeks_patient_id_patients",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_special_weeks"),
    )

    # ----- special_week_items (child rows) -----
    op.create_table(
        "special_week_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("special_week_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("row_label", sa.String(length=64), nullable=True),
        sa.Column(
            "time_type",
            sa.String(length=16),
            nullable=False,
            server_default="時間帯",
        ),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("preferred_earliest", sa.Time(), nullable=True),
        sa.Column("preferred_latest", sa.Time(), nullable=True),
        sa.Column(
            "service_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "required_staff_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "individual_change_handling", sa.String(length=16), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["special_week_id"],
            ["special_weeks.id"],
            ondelete="CASCADE",
            name="fk_special_week_items_special_week_id_special_weeks",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
            name="fk_special_week_items_patient_id_patients",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_special_week_items"),
    )
    op.create_index(
        "ix_special_week_items_week_date",
        "special_week_items",
        ["special_week_id", "visit_date"],
    )
    op.create_index(
        "ix_special_week_items_patient_date",
        "special_week_items",
        ["patient_id", "visit_date"],
    )


def downgrade() -> None:
    # ----- special_week_items / special_weeks -----
    op.drop_index(
        "ix_special_week_items_patient_date", table_name="special_week_items"
    )
    op.drop_index(
        "ix_special_week_items_week_date", table_name="special_week_items"
    )
    op.drop_table("special_week_items")
    op.drop_table("special_weeks")

    # ----- staff: remove 7 cols (note pre-existed and stays) -----
    op.drop_column("staff", "assignment_volume")
    op.drop_column("staff", "skill_level")
    op.drop_column("staff", "max_per_day")
    op.drop_column("staff", "areas")
    op.drop_column("staff", "home_lng")
    op.drop_column("staff", "home_lat")
    op.drop_column("staff", "home_address")

    # ----- patients: remove 5 cols -----
    op.drop_column("patients", "continuous_request")
    op.drop_column("patients", "specified_type")
    op.drop_column("patients", "preferred_staff_ids")
    op.drop_column("patients", "ng_staff_ids")
    op.drop_column("patients", "area")
