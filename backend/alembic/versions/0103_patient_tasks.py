"""事務所からのやること (看護師へのタスク) の表 2 つ.

Revision ID: 0103_patient_tasks
Revises: 0102_pfv_visit_week
Create Date: 2026-11-01

正典: ``docs/plans/patient-tasks-design-2026-11-01.md`` §2・§4 (PO 決定 2026-11-01)。

* ``task_item_types``: やることの項目 (名前・並び順・使う/使わない・作った人)。
  **初期データは入れない** (事業所の情報を migration に書かない。項目は本番に出した後、
  画面かエージェントで入れる)。
* ``patient_tasks``: 患者 + 日付 (この日から出す)・項目 (NULL なら伝言)・伝言・作った人・
  完了 (人・職員・訪問・時刻)・取消 (人・時刻)。CHECK で「項目も伝言も空」を弾く。
  出す訪問は保存しない (見るたびに決める)。

downgrade は表 2 つを drop する (やることと項目は失われる)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0103_patient_tasks"
down_revision: str | Sequence[str] | None = "0102_pfv_visit_week"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _uuid() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.UUID(as_uuid=True)
    return sa.Uuid()


def _fk(name: str, target: str, ondelete: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, _uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "task_item_types",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _fk("created_by", "users.id", "SET NULL"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "patient_tasks",
        sa.Column("id", _uuid(), primary_key=True),
        _fk("patient_id", "patients.id", "CASCADE", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        _fk("item_type_id", "task_item_types.id", "RESTRICT"),
        sa.Column("memo", sa.Text(), nullable=True),
        _fk("created_by", "users.id", "SET NULL"),
        sa.Column("done_at", _TS, nullable=True),
        _fk("done_by_user_id", "users.id", "SET NULL"),
        _fk("done_by_staff_id", "staff.id", "SET NULL"),
        _fk("done_visit_id", "visits.id", "SET NULL"),
        sa.Column("cancelled_at", _TS, nullable=True),
        _fk("cancelled_by", "users.id", "SET NULL"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "item_type_id IS NOT NULL OR memo IS NOT NULL",
            name=op.f("ck_patient_tasks_item_or_memo"),
        ),
    )
    op.create_index("ix_patient_tasks_patient_start", "patient_tasks", ["patient_id", "start_date"])
    op.create_index("ix_patient_tasks_done_visit", "patient_tasks", ["done_visit_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_tasks_done_visit", table_name="patient_tasks")
    op.drop_index("ix_patient_tasks_patient_start", table_name="patient_tasks")
    op.drop_table("patient_tasks")
    op.drop_table("task_item_types")
