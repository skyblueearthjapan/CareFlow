"""visits.is_unplanned (予定外訪問フラグ) 追加 — QR 打刻の開放 Phase B.

Revision ID: 0071_visit_is_unplanned
Revises: 0070_patient_ng_staff
Create Date: 2026-08-16

## このマイグレーションの責務

正典設計書 ``docs/plans/qr-open-checkin-design.md`` §3 のデータ層。
当日予定が無い患者宅で QR を読んだとき、その場で生成する「予定外訪問」を
既存 visit と区別するためのフラグ 1 列を足す。

* ``is_unplanned`` = true の visit は ``POST /visits/adhoc-checkin`` が
  打刻と同時に生成した行 (course_id NULL / primary_staff_id = 打刻スタッフ /
  status=in_progress / end_time は退出打刻で実時刻へ更新)。
* 既存行は全て false (= 予定として組まれた訪問) のまま。
* モニターは true の行だけを「📌予定外訪問」専用行へ集約する (§6)。

**代行打刻 (担当外スタッフが予定 visit に打刻) は列を足さない**: 予定側の担当は
書き換えず、実績は ``visit_checkins.staff_id`` が持つ (設計 §1 決定#4)。乖離は
既存スキーマだけで表現できる。

## downgrade

列 drop のみ (予定外だった事実は失われるが、visit 行と打刻は残る)。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0071_visit_is_unplanned"
down_revision: str | Sequence[str] | None = "0070_patient_ng_staff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    false_lit = "false" if is_pg else "0"
    op.add_column(
        "visits",
        sa.Column(
            "is_unplanned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text(false_lit),
            comment=(
                "予定外訪問。true = 当日予定が無い患者宅の QR 打刻 "
                "(POST /visits/adhoc-checkin) で生成された訪問。"
                "モニターは true の行を「📌予定外訪問」専用行へ集約する。"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("visits", "is_unplanned")
