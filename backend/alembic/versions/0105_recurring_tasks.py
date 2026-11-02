"""毎月のやること (繰り返しの設定) の表 2 つと、patient_tasks に足す列 2 つ.

Revision ID: 0105_recurring_tasks
Revises: 0104_monthly_actual
Create Date: 2026-11-02

正典: ``docs/plans/patient-tasks-recurring-design-2026-11-02.md`` §4 (PO 決定 2026-11-02)。

* ``recurring_task_rules``: どの項目を・どの区分の方に・毎月何日から・いつの月から・有効か。
  ``target_categories`` は区分の配列 (``psychiatric``／``general``)。SQLite の試験でも
  同じ形で持てるよう JSON にする (本番は JSONB)。CHECK で「項目も伝言も空」と
  「1〜28 日の外」を弾く。
* ``recurring_task_exclusions``: 設定ごとに個別に外す方 (主キー = 設定 + 患者)。
* ``patient_tasks.recurring_rule_id`` (SET NULL)・``recurring_month`` (その月の 1 日)。
  一意の索引 (設定・患者・月、設定がある行だけ) で「同じ設定・同じ方・同じ月は 1 件」を守る。

初期データは入れない (事業所の情報を migration に書かない)。downgrade は列 2 つと表 2 つを
drop する (設定は失われる。作り済みのやることは手で付けたものと同じ形で残る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0105_recurring_tasks"
down_revision: str | Sequence[str] | None = "0104_monthly_actual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _uuid() -> sa.types.TypeEngine:
    return postgresql.UUID(as_uuid=True) if _is_pg() else sa.Uuid()


def _json() -> sa.types.TypeEngine:
    return postgresql.JSONB() if _is_pg() else sa.JSON()


def _fk(name: str, target: str, ondelete: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, _uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "recurring_task_rules",
        sa.Column("id", _uuid(), primary_key=True),
        _fk("item_type_id", "task_item_types.id", "RESTRICT"),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("target_categories", _json(), nullable=False),
        sa.Column("start_month", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _fk("created_by", "users.id", "SET NULL"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "item_type_id IS NOT NULL OR memo IS NOT NULL",
            name=op.f("ck_recurring_task_rules_item_or_memo"),
        ),
        sa.CheckConstraint(
            "day_of_month BETWEEN 1 AND 28",
            name=op.f("ck_recurring_task_rules_day_of_month_range"),
        ),
    )

    op.create_table(
        "recurring_task_exclusions",
        _fk("rule_id", "recurring_task_rules.id", "CASCADE", nullable=False),
        _fk("patient_id", "patients.id", "CASCADE", nullable=False),
        _fk("created_by", "users.id", "SET NULL"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("rule_id", "patient_id", name=op.f("pk_recurring_task_exclusions")),
    )

    with op.batch_alter_table("patient_tasks") as batch:
        batch.add_column(sa.Column("recurring_rule_id", _uuid(), nullable=True))
        batch.add_column(sa.Column("recurring_month", sa.Date(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_patient_tasks_recurring_rule_id_recurring_task_rules"),
            "recurring_task_rules",
            ["recurring_rule_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "uq_patient_tasks_recurring_once",
        "patient_tasks",
        ["recurring_rule_id", "patient_id", "recurring_month"],
        unique=True,
        postgresql_where=sa.text("recurring_rule_id IS NOT NULL"),
        sqlite_where=sa.text("recurring_rule_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_patient_tasks_recurring_once", table_name="patient_tasks")
    with op.batch_alter_table("patient_tasks") as batch:
        batch.drop_constraint(
            op.f("fk_patient_tasks_recurring_rule_id_recurring_task_rules"), type_="foreignkey"
        )
        batch.drop_column("recurring_month")
        batch.drop_column("recurring_rule_id")
    op.drop_table("recurring_task_exclusions")
    op.drop_table("recurring_task_rules")
