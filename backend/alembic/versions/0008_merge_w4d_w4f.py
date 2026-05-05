"""merge W4-D mobile + W4-F audit

Revision ID: 0008_merge_w4d_w4f
Revises: 0006_w4d_mobile_features, 0007_audit_log_extension
Create Date: 2026-05-05

W4-D (0006_w4d_mobile_features) と W4-F (0007_audit_log_extension) が
両方 0005_special_weeks_unique を down_revision とした並列開発になり、
alembic head が 2 つに分岐していた。本 merge revision で両 head を統合し、
upgrade head が両 migration を順次適用できる状態に戻す。

スキーマ変更なし (merge only)。

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "0008_merge_w4d_w4f"
down_revision: Union[str, Sequence[str], None] = (
    "0006_w4d_mobile_features",
    "0007_audit_log_extension",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: merge revision only."""
    pass


def downgrade() -> None:
    """No-op: merge revision only."""
    pass
