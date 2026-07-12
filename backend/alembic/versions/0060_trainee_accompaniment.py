"""trainee_accompaniment — 新人同行の既定 + 週リンク 2 テーブル (Phase 1).

docs/plans/trainee-accompaniment-design.md v1.1 §4:
新人 (staff.is_trainee=true) が先輩の訪問に「同行」として付く運用の専用テーブル。
- trainee_accompaniment_defaults: 毎週の既定 (course_templates アンカー)
- trainee_accompaniments:         週の実効リンク (course / visit を混在管理)

旧 staff_companion_assignments (向きが逆) の撤去は Phase 2 で別 migration。

Revision ID: 0060_trainee_accompaniment
Revises: 0059_office_sort_order_short_label
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_trainee_accompaniment"
down_revision: Union[str, Sequence[str], None] = "0059_office_sort_order_short_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 毎週の既定 (テンプレ層) ------------------------------------------
    op.create_table(
        "trainee_accompaniment_defaults",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "trainee_staff_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column(
            "course_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("course_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_tad_weekday"),
        sa.UniqueConstraint("trainee_staff_id", "weekday", name="uq_tad_trainee_weekday"),
    )
    op.create_index("ix_tad_trainee", "trainee_accompaniment_defaults", ["trainee_staff_id"])

    # ---- 週の実効リンク (唯一の表示/連携ソース) ---------------------------
    op.create_table(
        "trainee_accompaniments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "trainee_staff_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=8), nullable=False),
        sa.Column(
            "course_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courses.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "visit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visits.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "source",
            sa.String(length=8),
            server_default="manual",
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.CheckConstraint("target_type IN ('course', 'visit')", name="ck_ta_target_type"),
        sa.CheckConstraint(
            "(target_type = 'course') = (course_id IS NOT NULL)",
            name="ck_ta_course_presence",
        ),
        sa.CheckConstraint(
            "(target_type = 'visit') = (visit_id IS NOT NULL)",
            name="ck_ta_visit_presence",
        ),
        sa.CheckConstraint("source IN ('default', 'manual')", name="ck_ta_source"),
        sa.UniqueConstraint("trainee_staff_id", "course_id", name="uq_ta_trainee_course"),
        sa.UniqueConstraint("trainee_staff_id", "visit_id", name="uq_ta_trainee_visit"),
    )
    op.create_index("ix_ta_course", "trainee_accompaniments", ["course_id"])
    op.create_index("ix_ta_visit", "trainee_accompaniments", ["visit_id"])
    op.create_index("ix_ta_trainee", "trainee_accompaniments", ["trainee_staff_id"])


def downgrade() -> None:
    op.drop_index("ix_ta_trainee", table_name="trainee_accompaniments")
    op.drop_index("ix_ta_visit", table_name="trainee_accompaniments")
    op.drop_index("ix_ta_course", table_name="trainee_accompaniments")
    op.drop_table("trainee_accompaniments")

    op.drop_index("ix_tad_trainee", table_name="trainee_accompaniment_defaults")
    op.drop_table("trainee_accompaniment_defaults")
