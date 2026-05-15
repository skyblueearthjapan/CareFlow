"""courses: add proposal_batch_id and decision_log for auto-schedule v1.

Revision ID: 0029_courses_proposal_batch
Revises: 0028_v2_patient_sex_restriction_widen
Create Date: 2026-05-16

Wave 41 Phase 0 (auto-schedule v1.0):
  * proposal_batch_id (UUID NULL, indexed) — identifies the auto-schedule
    proposal batch that generated this course instance.
  * decision_log (JSONB NULL) — structured JSON history of per-phase
    decisions made during automatic schedule generation.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0029_courses_proposal_batch"
down_revision = "0028_v2_patient_sex_restriction_widen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.add_column(
        "courses",
        sa.Column(
            "proposal_batch_id",
            PG_UUID(as_uuid=True) if is_pg else sa.String(36),
            nullable=True,
        ),
    )
    op.add_column(
        "courses",
        sa.Column(
            "decision_log",
            JSONB() if is_pg else sa.JSON(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_courses_proposal_batch_status",
        "courses",
        ["proposal_batch_id", "course_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_courses_proposal_batch_status", table_name="courses")
    op.drop_column("courses", "decision_log")
    op.drop_column("courses", "proposal_batch_id")
