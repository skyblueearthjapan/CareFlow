"""visits.kaipoke_service_override (訪問単位のサービス内容上書き) の追加.

Revision ID: 0078_visits_kaipoke_service_override
Revises: 0077_patients_visit_category
Create Date: 2026-08-23

## このマイグレーションの責務

正典設計書 ``docs/plans/kaipoke-service-content-design.md`` §2。サービス内容は
**患者の区分 × 職員1の資格** から自動判定するのが原則だが、実運用では
「この 1 訪問だけカイポケ側の登録に合わせたい」場面がある (カイポケが正で
らく助のマスタが追いついていない・例外的な算定など)。マスタ (患者の区分 /
スタッフの資格) を動かすと **その人の全訪問** に波及してしまうため、
訪問 1 件だけを対象にした逃げ道を 1 列で用意する。

* ``visits.kaipoke_service_override`` — ``String(64) NULL``。非 NULL なら
  その訪問のサービス内容として **そのまま** 出力する。NULL (既定) は従来
  どおり 患者上書き → 区分 × 資格 の分岐。

優先順位 (``csv_builder.resolve_service_content``)::

    訪問上書き (visits.kaipoke_service_override)
      > 患者上書き (patients.kaipoke_service_content)
      > 患者の区分 (patients.visit_category) × 職員1の資格 (staff.qualification)

盤面上の位置 (日付/時刻/担当) は一切変えない列なので、青ピン (week_pinned) や
planned 以外の状態でも設定できる (API 側でも 422 にしない)。

## downgrade

列 drop のみ。訪問単位の上書きは失われ、区分 × 資格の分岐だけに戻る。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0078_visits_kaipoke_service_override"
down_revision: str | Sequence[str] | None = "0077_patients_visit_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "visits",
        sa.Column(
            "kaipoke_service_override",
            sa.String(length=64),
            nullable=True,
            comment="訪問単位のサービス内容上書き (非 NULL ならそのまま出力・最優先)",
        ),
    )


def downgrade() -> None:
    op.drop_column("visits", "kaipoke_service_override")
