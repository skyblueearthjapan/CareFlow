"""kaipoke_csv_snapshots (最終取得CSVの保存) + correction_sheets.origin 追加.

Revision ID: 0076_kaipoke_csv_snapshots
Revises: 0075_staff_events_cancelled_at
Create Date: 2026-08-22

## このマイグレーションの責務

正典設計書 ``docs/plans/week-cockpit-design.md`` §1 D3 / §2-4 のデータ層。
「●未送信」を **RPA を呼ばずに** 出すための土台。

* ``kaipoke_csv_snapshots`` — カイポケ export に成功したときの現況CSVを丸ごと保存する。
  同 (office_id, month, week_start) は最新 1 件だけを残す (保存時に古い行を削除)。
  未送信 = ``build_local_diff(current_csv=保存分)`` をローカル実行した結果、という
  1 本の式で表せるようになる (調査報告 §3-4 案 b)。
  - ``office_id`` は NULL 可 — 単一事業所運用では拠点を指定せず export するため。
  - ``week_start`` は NULL 可 — 月まるごとの export は NULL、週マージ export
    (``export_current_week_csv``) はその週の月曜。週を跨ぐCSVを別の週の
    「現況」と誤認しないための識別子。upsert キーにも含める (月CSVと週CSVは共存)。
  - 一意性は COALESCE 式の UNIQUE インデックスで担保する (PostgreSQL のみ。
    office_id/week_start が NULL 可のため素の UNIQUE では効かない)。
* ``correction_sheets.origin`` — シートの出所 (NULL=従来の差分計算 /
  ``'cached'``=保存CSVからのローカル再計算 / ``'reverse'``=inbound シートの反転)。
  週空間の同期バーが毎回作る 'cached' シートを掃除するための目印。

## downgrade

テーブル drop + 列 drop のみ。保存CSVは再取得できるキャッシュなので業務データは無傷。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0076_kaipoke_csv_snapshots"
down_revision: str | Sequence[str] | None = "0075_staff_events_cancelled_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)

    op.create_table(
        "kaipoke_csv_snapshots",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "office_id",
            uuid_type,
            sa.ForeignKey("offices.id", ondelete="CASCADE"),
            nullable=True,
            comment="対象事業所 (NULL = 拠点無指定でのexport)",
        ),
        sa.Column("month", sa.String(length=7), nullable=False, comment="YYYY-MM"),
        sa.Column(
            "week_start",
            sa.Date(),
            nullable=True,
            comment="週マージexportの対象週 (月曜)。月まるごとのexportは NULL",
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("csv_text", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_op",
            sa.String(length=32),
            nullable=False,
            comment="どの経路のexportか (diff-local / diff-inbound / smart-preview ...)",
        ),
    )
    op.create_index(
        "ix_kaipoke_csv_snapshots_office_month",
        "kaipoke_csv_snapshots",
        ["office_id", "month"],
    )
    if is_pg:
        # upsert キー (office_id, month, week_start) の一意性を DB でも担保する。
        # office_id / week_start が NULL 可のため素の UNIQUE では効かない
        # (PG は NULL 同士を「別の値」として扱う) → COALESCE 式の一意インデックス。
        # 番兵値は実データと衝突しない値を選ぶ (全ゼロUUID / 1900-01-01)。
        op.execute(
            "CREATE UNIQUE INDEX uq_kaipoke_csv_snapshots_key "
            "ON kaipoke_csv_snapshots ("
            "COALESCE(office_id, '00000000-0000-0000-0000-000000000000'::uuid), "
            "month, "
            "COALESCE(week_start, DATE '1900-01-01'))"
        )

    op.add_column(
        "correction_sheets",
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=True,
            comment="シートの出所 (NULL=通常 / 'cached'=保存CSVからの再計算 / 'reverse'=反転)",
        ),
    )


def downgrade() -> None:
    op.drop_column("correction_sheets", "origin")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS uq_kaipoke_csv_snapshots_key")
    op.drop_index("ix_kaipoke_csv_snapshots_office_month", table_name="kaipoke_csv_snapshots")
    op.drop_table("kaipoke_csv_snapshots")
