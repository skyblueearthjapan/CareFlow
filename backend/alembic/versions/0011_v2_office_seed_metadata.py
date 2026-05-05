"""W1-BE3 office seed metadata (no-op slot)

Revision ID: 0011_v2_office_seed_and_assigner
Revises: 0010_v2_staff_master_cleanup
Create Date: 2026-05-06

W1-BE3 / 拠点 (Office) 整備 (`docs/plans/v2-allocation-redesign.md` v0.9 §4.3).

## このマイグレーションの責務

Wave 0-A の予約表 (`docs/plans/v2-migration-reservations.md`) で予約された
0011 番のスロット。実装上、稲毛 / 都賀のシードは **DML を伴うスキーマ変更ではなく**
`backend/scripts/seed_offices_v2.py` で運用するため、本マイグレーション自体は
**スキーマ変更を含まない no-op** とする。

理由:
- offices テーブルは v1 (0001_initial / 0002_add_office_prefecture_code) で完成済み。
- office_cities (M2M) も既に存在し、追加の DDL 不要。
- 拠点シードは複数都市・将来的な拠点追加を考慮し、Alembic ではなく
  独立した冪等スクリプトで管理する方が運用上扱いやすい (cities seed と同方針)。

## 番号予約の意義

Wave 1 並行マージ時の番号衝突を物理的に防ぐため、down_revision を予約表の
`0010_v2_staff_master_cleanup` で固定する。本ファイルが空でも `alembic upgrade head`
は番号順に通過する。

将来 office_cities へインデックス追加等の DDL が必要になった場合は、別 revision
(0011 ではなく後続番号) で追加する想定。

"""

# ruff: noqa: I001, UP007, UP035
# 既存の Wave 1 / v1 alembic revisions と同じスタイル (typing.Union / typing.Sequence)
# を維持するため lint を抑制する。本リポジトリの ruff 設定では alembic/versions が
# extend-exclude 対象だが、pre-commit hook はリポジトリルートで ruff を実行するため
# pyproject の extend-exclude が効かない。よってファイル単位で抑制する。
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "0011_v2_office_seed_and_assigner"
down_revision: Union[str, Sequence[str], None] = "0010_v2_staff_master_cleanup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: シードは scripts/seed_offices_v2.py で運用する."""
    pass


def downgrade() -> None:
    """No-op: スキーマ変更を含まないため rollback も不要."""
    pass
