"""patients に status_changed_at / status_changed_by を追加 (患者ステータス連動 Phase 1).

Revision ID: 0082_patient_status_changed_at
Revises: 0081_staff_events_external_id_len
Create Date: 2026-09-09

## このマイグレーションの責務

患者ステータス (稼働中 / 一時休止 / 入院中 / 開始前 / 解約済み) を変えたとき、
予定 (visits) と連動させる仕組み (docs/plans/patient-status-schedule-design-2026-09-09.md
§7) の土台。これまで ``patients`` にはステータスの変更時刻が無く、
``audit_logs`` の PATCH 行からしか追えなかった (しかもミドルウェアはベストエフォートで
2026-09-09 の作業では 21 件中 20 件しか残らなかった)。

- ``status_changed_at`` TIMESTAMPTZ NULL — 最後に ``status`` が変わった時刻。
- ``status_changed_by`` UUID NULL — 操作者 (users.id・ON DELETE SET NULL)。

既存行は NULL のまま (= 不明)。効果日 (未来の予約) は持たない (PO 決定 Q9)。
CHECK 制約は入れない (テスト/コメントに旧値 ``inactive`` が残るため、API 側の
Literal で守る)。

## downgrade

2 列を drop するだけ (データ損失は変更時刻・操作者のみ)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0082_patient_status_changed_at"
down_revision: str | Sequence[str] | None = "0081_staff_events_external_id_len"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "patients",
        sa.Column(
            "status_changed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("patients", "status_changed_by")
    op.drop_column("patients", "status_changed_at")
