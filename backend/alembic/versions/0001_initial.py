"""initial schema (D1 backend skeleton)

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-05

Hand-authored from `app.models.*` so we can ship without running
`alembic revision --autogenerate` against a live PostgreSQL.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- staff (must come before users due to users.staff_id FK) -----
    op.create_table(
        "staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kana", sa.String(length=120), nullable=True),
        sa.Column("sex", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="staff"),
        sa.Column("primary_office_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("can_double_team", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("mentor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mentor_id"], ["staff.id"], ondelete="SET NULL", name="fk_staff_mentor_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_staff"),
    )
    op.create_index("ix_staff_status_office", "staff", ["status", "primary_office_id"])

    # ----- users -----
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="staff"),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="SET NULL", name="fk_users_staff_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ----- offices -----
    op.create_table(
        "offices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_offices"),
    )
    op.create_index("ix_offices_name", "offices", ["name"])
    # Partial index for active (non-soft-deleted) offices — PG-only.
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "ix_offices_active",
            "offices",
            ["id"],
            postgresql_where=sa.text("deleted_at IS NULL"),
        )

    # Now that offices exists, attach the deferred FK from staff.primary_office_id.
    with op.batch_alter_table("staff") as batch:
        batch.create_foreign_key(
            "fk_staff_primary_office_id_offices",
            "offices",
            ["primary_office_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # ----- cities -----
    op.create_table(
        "cities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prefecture", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("jis_code", sa.String(length=8), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cities"),
        sa.UniqueConstraint("jis_code", name="uq_cities_jis_code"),
    )

    # ----- office_cities (M:N) -----
    op.create_table(
        "office_cities",
        sa.Column("office_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("city_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"], ondelete="CASCADE", name="fk_office_cities_city_id_cities"),
        sa.ForeignKeyConstraint(["office_id"], ["offices.id"], ondelete="CASCADE", name="fk_office_cities_office_id_offices"),
        sa.PrimaryKeyConstraint("office_id", "city_id", name="pk_office_cities"),
    )

    # ----- patients -----
    op.create_table(
        "patients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kana", sa.String(length=120), nullable=True),
        sa.Column("sex", sa.String(length=8), nullable=True),
        sa.Column("age", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("insurance", sa.String(length=16), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("lng", sa.Numeric(10, 7), nullable=True),
        sa.Column("primary_office_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("required_staff_count", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("sex_restriction", sa.String(length=8), nullable=True),
        sa.Column("ng_time_start", sa.Time(), nullable=True),
        sa.Column("ng_time_end", sa.Time(), nullable=True),
        sa.Column("weekly_pattern", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("special_week", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["primary_office_id"], ["offices.id"], ondelete="SET NULL", name="fk_patients_primary_office_id_offices"),
        sa.PrimaryKeyConstraint("id", name="pk_patients"),
        sa.UniqueConstraint("code", name="uq_patients_code"),
    )
    op.create_index("ix_patients_status_office", "patients", ["status", "primary_office_id"])
    op.create_index("ix_patients_kana", "patients", ["kana"])

    # ----- patient_allowed_offices -----
    op.create_table(
        "patient_allowed_offices",
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("office_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["office_id"], ["offices.id"], ondelete="CASCADE", name="fk_patient_allowed_offices_office_id_offices"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE", name="fk_patient_allowed_offices_patient_id_patients"),
        sa.PrimaryKeyConstraint("patient_id", "office_id", name="pk_patient_allowed_offices"),
    )

    # ----- staff_secondary_offices -----
    op.create_table(
        "staff_secondary_offices",
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("office_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["office_id"], ["offices.id"], ondelete="CASCADE", name="fk_staff_secondary_offices_office_id_offices"),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE", name="fk_staff_secondary_offices_staff_id_staff"),
        sa.PrimaryKeyConstraint("staff_id", "office_id", name="pk_staff_secondary_offices"),
    )

    # ----- staff_shifts -----
    op.create_table(
        "staff_shifts",
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("is_on", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE", name="fk_staff_shifts_staff_id_staff"),
        sa.PrimaryKeyConstraint("staff_id", "weekday", name="pk_staff_shifts"),
    )

    # ----- staff_weekly_overrides -----
    op.create_table(
        "staff_weekly_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("iso_year", sa.Integer(), nullable=False),
        sa.Column("iso_week", sa.SmallInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("override_type", sa.String(length=16), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE", name="fk_staff_weekly_overrides_staff_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_staff_weekly_overrides"),
        sa.UniqueConstraint("staff_id", "iso_year", "iso_week", "weekday", name="uq_staff_week_override"),
    )
    op.create_index(
        "ix_staff_overrides_lookup",
        "staff_weekly_overrides",
        ["iso_year", "iso_week", "staff_id"],
    )

    # ----- staff_events -----
    op.create_table(
        "staff_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="CASCADE", name="fk_staff_events_staff_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_staff_events"),
    )
    op.create_index("ix_staff_events_when", "staff_events", ["staff_id", "starts_at"])

    # ----- mentor_assignments -----
    op.create_table(
        "mentor_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mentor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mentee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mentee_id"], ["staff.id"], ondelete="CASCADE", name="fk_mentor_assignments_mentee_id_staff"),
        sa.ForeignKeyConstraint(["mentor_id"], ["staff.id"], ondelete="CASCADE", name="fk_mentor_assignments_mentor_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_mentor_assignments"),
        sa.UniqueConstraint("mentor_id", "mentee_id", "start_date", name="uq_mentor_pair_start"),
    )

    # ----- visits -----
    op.create_table(
        "visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("primary_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("secondary_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("mentor_staff_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="planned"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("kaipoke_id", sa.String(length=64), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["mentor_staff_id"], ["staff.id"], ondelete="SET NULL", name="fk_visits_mentor_staff_id_staff"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT", name="fk_visits_patient_id_patients"),
        sa.ForeignKeyConstraint(["primary_staff_id"], ["staff.id"], ondelete="SET NULL", name="fk_visits_primary_staff_id_staff"),
        sa.ForeignKeyConstraint(["secondary_staff_id"], ["staff.id"], ondelete="SET NULL", name="fk_visits_secondary_staff_id_staff"),
        sa.PrimaryKeyConstraint("id", name="pk_visits"),
    )
    op.create_index("ix_visits_date", "visits", ["visit_date"])
    op.create_index("ix_visits_patient_date", "visits", ["patient_id", "visit_date"])
    op.create_index("ix_visits_primary_date", "visits", ["primary_staff_id", "visit_date"])
    op.create_index("ix_visits_status", "visits", ["status"])

    # ----- correction_sheets -----
    op.create_table(
        "correction_sheets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL", name="fk_correction_sheets_created_by_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_correction_sheets"),
    )
    op.create_index("ix_correction_sheets_month", "correction_sheets", ["target_month"])
    op.create_index("ix_correction_sheets_status", "correction_sheets", ["status"])

    # ----- correction_sheet_items -----
    op.create_table(
        "correction_sheet_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("include", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="SET NULL", name="fk_correction_sheet_items_patient_id_patients"),
        sa.ForeignKeyConstraint(["sheet_id"], ["correction_sheets.id"], ondelete="CASCADE", name="fk_correction_sheet_items_sheet_id_correction_sheets"),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.id"], ondelete="SET NULL", name="fk_correction_sheet_items_visit_id_visits"),
        sa.PrimaryKeyConstraint("id", name="pk_correction_sheet_items"),
    )
    op.create_index("ix_correction_items_sheet_action", "correction_sheet_items", ["sheet_id", "action"])
    op.create_index("ix_correction_items_sheet_include", "correction_sheet_items", ["sheet_id", "include"])

    # ----- audit_logs -----
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_table", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL", name="fk_audit_logs_actor_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_target", "audit_logs", ["target_table", "target_id"])
    # PG-only DESC index — guarded so the migration still loads on SQLite tests.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_audit_logs_recent ON audit_logs (created_at DESC)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_audit_logs_recent")
    op.drop_index("ix_audit_logs_target", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_created", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_correction_items_sheet_include", table_name="correction_sheet_items")
    op.drop_index("ix_correction_items_sheet_action", table_name="correction_sheet_items")
    op.drop_table("correction_sheet_items")

    op.drop_index("ix_correction_sheets_status", table_name="correction_sheets")
    op.drop_index("ix_correction_sheets_month", table_name="correction_sheets")
    op.drop_table("correction_sheets")

    op.drop_index("ix_visits_status", table_name="visits")
    op.drop_index("ix_visits_primary_date", table_name="visits")
    op.drop_index("ix_visits_patient_date", table_name="visits")
    op.drop_index("ix_visits_date", table_name="visits")
    op.drop_table("visits")

    op.drop_table("mentor_assignments")

    op.drop_index("ix_staff_events_when", table_name="staff_events")
    op.drop_table("staff_events")

    op.drop_index("ix_staff_overrides_lookup", table_name="staff_weekly_overrides")
    op.drop_table("staff_weekly_overrides")

    op.drop_table("staff_shifts")
    op.drop_table("staff_secondary_offices")
    op.drop_table("patient_allowed_offices")

    op.drop_index("ix_patients_kana", table_name="patients")
    op.drop_index("ix_patients_status_office", table_name="patients")
    op.drop_table("patients")

    op.drop_table("office_cities")
    op.drop_table("cities")

    with op.batch_alter_table("staff") as batch:
        batch.drop_constraint("fk_staff_primary_office_id_offices", type_="foreignkey")

    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("ix_offices_active", table_name="offices")
    op.drop_index("ix_offices_name", table_name="offices")
    op.drop_table("offices")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_staff_status_office", table_name="staff")
    op.drop_table("staff")
