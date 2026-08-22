"""patients.visit_category (訪問看護区分: 精神科 / 一般) の追加.

Revision ID: 0077_patients_visit_category
Revises: 0076_kaipoke_csv_snapshots
Create Date: 2026-08-23

## このマイグレーションの責務

正典設計書 ``docs/plans/kaipoke-service-content-design.md`` §1-1 (S1) のデータ層。
カイポケ「サービス内容」を **患者の区分 × 職員1の資格** から自動判定するための
患者側マスタ項目を 1 列だけ足す。

* ``patients.visit_category`` — ``'psychiatric'`` (精神科・既定) / ``'general'`` (一般)。
  サービス内容のベース文字列を決める: 精神科 → 「精神基本療養費Ⅰ」/
  一般 → 「基本療養費Ⅰ」。``String(16) NOT NULL DEFAULT 'psychiatric'`` で、
  既存の全患者は既定値 (精神科) のまま移行する。実態が「一般」の数名だけ
  移行後に管理画面 (患者編集 → 保険・拠点) から切り替える運用。

既存の ``patients.kaipoke_service_content`` (mig 0054) は **例外時の完全文字列
上書き** として存続する (非 NULL なら分岐を無視してそのまま出力)。この列は
触らない。

## downgrade

列 drop のみ。区分の設定内容は失われるが、既定 (精神科) 前提の従来挙動に戻る。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0077_patients_visit_category"
down_revision: str | Sequence[str] | None = "0076_kaipoke_csv_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patients",
        sa.Column(
            "visit_category",
            sa.String(length=16),
            nullable=False,
            server_default="psychiatric",
            comment="訪問看護区分 (psychiatric=精神科 / general=一般)。サービス内容のベース",
        ),
    )


def downgrade() -> None:
    op.drop_column("patients", "visit_category")
