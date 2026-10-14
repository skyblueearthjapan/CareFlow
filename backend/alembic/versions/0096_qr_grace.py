"""再発行した古い QR に猶予を残せるようにする (revoked_qr_tokens に valid_until / reason)。

Revision ID: 0096_qr_grace
Revises: 0095_user_display_name
Create Date: 2026-10-14

PO 2026-10-14 (``docs/plans/qr-manage-reissue-guard-2026-10-14.md`` §2・§4-1):
QR の再発行に理由を付け、理由によって古いシールに猶予を残す。

* ``revoked_qr_tokens.valid_until`` TIMESTAMPTZ NULL — この時刻まで古い QR でも打刻できる。
  NULL = 即時に無効 (既存の行はすべて NULL = 今と同じ)。
* ``revoked_qr_tokens.reason`` VARCHAR(16) NULL — ``damaged`` / ``lost`` / ``misuse``。
  理由なしの再発行 (古い画面) と既存の行は NULL。

downgrade は 2 列を drop する。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0096_qr_grace"
down_revision: str | Sequence[str] | None = "0095_user_display_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "revoked_qr_tokens",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("revoked_qr_tokens", sa.Column("reason", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("revoked_qr_tokens", "reason")
    op.drop_column("revoked_qr_tokens", "valid_until")
