"""special_weeks: enforce unique (patient_id, week_start)

Revision ID: 0005_special_weeks_unique
Revises: 0004_w3_master_extensions
Create Date: 2026-05-05

A patient should have at most one special-week header per `week_start`. The
absence of this constraint allowed duplicate rows to creep in via re-imports
(scripts/import_special_weeks.py) which then made downstream allocation
ambiguous about which header wins.

## Preflight (production)

Run **before** applying this migration:

```sql
SELECT patient_id, week_start, COUNT(*)
FROM special_weeks
GROUP BY patient_id, week_start
HAVING COUNT(*) > 1;
```

If any rows are returned, dedupe them first (keep latest `registered_at`)
otherwise this migration will fail with a UniqueViolation.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_special_weeks_unique"
down_revision: Union[str, None] = "0004_w3_master_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_special_weeks_patient_week_start",
        "special_weeks",
        ["patient_id", "week_start"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_special_weeks_patient_week_start",
        "special_weeks",
        type_="unique",
    )
