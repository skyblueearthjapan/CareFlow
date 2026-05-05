"""add office prefecture/code columns + active-name unique partial index

Revision ID: 0002_add_office_prefecture_code
Revises: 0001_initial
Create Date: 2026-05-05

W1-C critic fix: silent data drop on `prefecture` / `code` columns missing.
Also adds an active-only unique partial index on `name` (M3).

## Preflight (production)

Before applying this migration on production data with active office rows,
check for duplicate active names:

    SELECT name, COUNT(*) FROM offices WHERE deleted_at IS NULL GROUP BY name HAVING COUNT(*) > 1;

If any rows are returned, resolve duplicates first (rename or soft-delete one)
before running `alembic upgrade head`. Otherwise the partial unique index
`uq_offices_name_active` creation will fail.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_office_prefecture_code"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "offices",
        sa.Column("prefecture", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "offices",
        sa.Column("code", sa.String(length=32), nullable=True),
    )
    op.create_unique_constraint("uq_offices_code", "offices", ["code"])

    # M3: active offices only — partial unique index on `name`. PG-only.
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "uq_offices_name_active",
            "offices",
            ["name"],
            unique=True,
            postgresql_where=sa.text("deleted_at IS NULL"),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("uq_offices_name_active", table_name="offices")
    op.drop_constraint("uq_offices_code", "offices", type_="unique")
    op.drop_column("offices", "code")
    op.drop_column("offices", "prefecture")
