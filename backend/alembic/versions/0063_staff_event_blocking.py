"""イベント考慮提案: staff_events に blocking (🔒絶対に潰せない) フラグを追加.

2段階提案 (PO確定 2026-07-27):
- 提案エンジンはまずイベントを占有として空き枠を探す (パスA)
- ゼロ件のときだけソフトイベントを無視して再走査し衝突警告付きで提案する (パスB)
- blocking=true のイベントはパスBでも占有のまま (絶対に衝突提案を出さない)

既定 false (= 現場の実態「訪問優先・イベントは動かせる」)。カイポケ再取り込みの
update はこの列に触れないため、ユーザーが立てたフラグは取り込みを跨いで保持される。

Revision ID: 0063_staff_event_blocking
Revises: 0062_staff_event_source
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0063_staff_event_blocking"
down_revision: str | Sequence[str] | None = "0062_staff_event_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_events",
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("staff_events", "blocking")
