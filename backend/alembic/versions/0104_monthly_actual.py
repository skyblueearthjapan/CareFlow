"""月の実績の作業 (らく助の QR 実績 × カイポケの実績) の表 2 つ.

Revision ID: 0104_monthly_actual
Revises: 0103_patient_tasks
Create Date: 2026-11-01

正典: ``docs/plans/kaipoke-actual-write-design-2026-11-01.md`` §5-3 (訪問記録の「月の実績」タブ)。

* ``monthly_actual_runs``: 月ごとに 1 行 (``month`` は UNIQUE)。状態・カイポケの実績を
  読み取った時刻・件数・取り直しと反映のジョブ・最後の反映の結果・確かめた結果・夜の予約。
* ``monthly_actual_rows``: 判定の 1 行 (らく助の側・カイポケの側・印・「反映する／しない」・
  送った結果)。作業を消すと行も消える (CASCADE)。

初期データは入れない。downgrade は表 2 つを drop する (作業の記録は失われる。カイポケの
実績の CSV は ``kaipoke_csv_snapshots`` に残る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0104_monthly_actual"
down_revision: str | Sequence[str] | None = "0103_patient_tasks"
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


def _hm(name: str) -> sa.Column:
    return sa.Column(name, sa.String(5), nullable=True)


def upgrade() -> None:
    op.create_table(
        "monthly_actual_runs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="judged"),
        sa.Column("snapshot_id", _uuid(), nullable=True),
        sa.Column("kaipoke_fetched_at", _TS, nullable=True),
        sa.Column("judged_at", _TS, nullable=True),
        sa.Column("counts", _json(), nullable=True),
        _fk("refresh_job_id", "kaipoke_jobs.id", "SET NULL"),
        _fk("apply_job_id", "kaipoke_jobs.id", "SET NULL"),
        sa.Column("last_apply", _json(), nullable=True),
        sa.Column("verify_result", _json(), nullable=True),
        sa.Column("scheduled_at", _TS, nullable=True),
        sa.Column("scheduled_dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        _fk("scheduled_by", "users.id", "SET NULL"),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("month", name=op.f("uq_monthly_actual_runs_month")),
    )

    op.create_table(
        "monthly_actual_rows",
        sa.Column("id", _uuid(), primary_key=True),
        _fk("run_id", "monthly_actual_runs.id", "CASCADE", nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("flags", _json(), nullable=False),
        sa.Column("item_key", sa.String(200), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=False),
        sa.Column("patient", sa.String(100), nullable=False),
        _fk("visit_id", "visits.id", "SET NULL"),
        sa.Column("patient_code", sa.String(32), nullable=True),
        sa.Column("rk_staff", sa.String(200), nullable=True),
        _hm("rk_arrival"),
        _hm("rk_departure"),
        sa.Column("rk_adjusted", sa.String(20), nullable=True),
        sa.Column("rk_stay", sa.Integer(), nullable=True),
        _hm("new_start"),
        _hm("new_end"),
        sa.Column("kp_patient", sa.String(100), nullable=True),
        _hm("kp_start"),
        _hm("kp_end"),
        sa.Column("kp_staff1", sa.String(100), nullable=True),
        sa.Column("kp_staff", sa.String(200), nullable=True),
        sa.Column("kp_service", sa.String(100), nullable=True),
        sa.Column("kp_minutes", sa.Integer(), nullable=True),
        _hm("k2_start"),
        _hm("k2_end"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        _fk("selected_by", "users.id", "SET NULL"),
        sa.Column("selected_at", _TS, nullable=True),
        sa.Column("apply_status", sa.String(16), nullable=True),
        sa.Column("apply_reason", sa.String(40), nullable=True),
        sa.Column("apply_message", sa.Text(), nullable=True),
        sa.Column("apply_dry_run", sa.Boolean(), nullable=True),
        sa.Column("applied_at", _TS, nullable=True),
        sa.Column("dry_run_ok_sig", sa.String(240), nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_monthly_actual_rows_run_seq", "monthly_actual_rows", ["run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_monthly_actual_rows_run_seq", table_name="monthly_actual_rows")
    op.drop_table("monthly_actual_rows")
    op.drop_table("monthly_actual_runs")
