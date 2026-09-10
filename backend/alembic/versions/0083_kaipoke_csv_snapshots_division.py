"""kaipoke_csv_snapshots に division (予定/実績) を追加 (月次 予実比較レポート).

Revision ID: 0083_kaipoke_csv_snapshots_division
Revises: 0082_patient_status_changed_at
Create Date: 2026-09-10

## このマイグレーションの責務

カイポケの **実績CSV** を、既存の予定CSV スナップショットと同じ棚に並べて保存する
ための 1 列。予実比較レポート (``plan_actual_compare``) は
「同じ月の 予定スナップショット × 実績スナップショット」を突き合わせるだけの
read-only 機能なので、既存の差分/反映/取り込みの挙動には一切触らない (追加のみ)。

* ``division`` VARCHAR(8) NOT NULL DEFAULT 'plan'
  - ``'plan'``   … 従来の現況CSV (= 予定)。既存行はすべてこれになる。
  - ``'actual'`` … 実績CSV (RPA ``/api/export`` の ``division='actual'``)。
  - 既定が ``'plan'`` なので、``save_snapshot`` / ``get_latest`` /
    ``drop_snapshots`` の既存呼び出しは **挙動が変わらない**。

upsert キー (0076 の COALESCE 式 UNIQUE インデックス) と検索インデックスにも
``division`` を足す。足さないと「実績を保存した瞬間に同月の予定が消える」
(= 未送信計算の土台が壊れる) ため、ここが本マイグレーションの肝。

## downgrade

インデックスを 0076 の形に戻し、列を drop する。実績スナップショットは
再取得できるキャッシュなので業務データは無傷。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0083_kaipoke_csv_snapshots_division"
down_revision: str | Sequence[str] | None = "0082_patient_status_changed_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UQ_PLAN_ONLY = (
    "CREATE UNIQUE INDEX uq_kaipoke_csv_snapshots_key "
    "ON kaipoke_csv_snapshots ("
    "COALESCE(office_id, '00000000-0000-0000-0000-000000000000'::uuid), "
    "month, "
    "COALESCE(week_start, DATE '1900-01-01'))"
)

_UQ_WITH_DIVISION = (
    "CREATE UNIQUE INDEX uq_kaipoke_csv_snapshots_key "
    "ON kaipoke_csv_snapshots ("
    "COALESCE(office_id, '00000000-0000-0000-0000-000000000000'::uuid), "
    "month, "
    "COALESCE(week_start, DATE '1900-01-01'), "
    "division)"
)


def upgrade() -> None:
    op.add_column(
        "kaipoke_csv_snapshots",
        sa.Column(
            "division",
            sa.String(length=8),
            nullable=False,
            server_default="plan",
            comment="'plan'=予定(従来の現況CSV) / 'actual'=実績CSV",
        ),
    )
    op.drop_index(
        "ix_kaipoke_csv_snapshots_office_month", table_name="kaipoke_csv_snapshots"
    )
    op.create_index(
        "ix_kaipoke_csv_snapshots_office_month",
        "kaipoke_csv_snapshots",
        ["office_id", "month", "division"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_kaipoke_csv_snapshots_key")
        op.execute(_UQ_WITH_DIVISION)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_kaipoke_csv_snapshots_key")
        # 予定以外を消してから 0076 の一意性へ戻す (実績行が残っていると衝突する)。
        op.execute("DELETE FROM kaipoke_csv_snapshots WHERE division <> 'plan'")
        op.execute(_UQ_PLAN_ONLY)
    op.drop_index(
        "ix_kaipoke_csv_snapshots_office_month", table_name="kaipoke_csv_snapshots"
    )
    op.create_index(
        "ix_kaipoke_csv_snapshots_office_month",
        "kaipoke_csv_snapshots",
        ["office_id", "month"],
    )
    op.drop_column("kaipoke_csv_snapshots", "division")
