"""kaipoke_credentials — カイポケ ログイン情報のアプリ内設定 (C-1 汎用化).

docs/plans/kaipoke-credentials-config-design.md:
法人ID・ユーザーID・パスワード(暗号化) を CareFlow DB に保存し、
RPA 側の固定設定 (.env / コード直書き) を置き換える。

Revision ID: 0058_kaipoke_credentials
Revises: 0057_course_code_rin
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0058_kaipoke_credentials"
down_revision: Union[str, Sequence[str], None] = "0057_course_code_rin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kaipoke_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("corp_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "updated_by_user_id",
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
    )


def downgrade() -> None:
    op.drop_table("kaipoke_credentials")
