"""W2-BE6 AI scope metadata (no-op slot)

Revision ID: 0014_v2_ai_scope_metadata
Revises: 0013_v2_pending_requests
Create Date: 2026-05-06

W2-BE6 / AI scope 拡張 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.5 / §3.5.7).

## このマイグレーションの責務

Wave 0-A の予約表 (`docs/plans/v2-migration-reservations.md`) で予約された
0014 番のスロット。`ai_interpret_logs` テーブルへのスキーマ変更は不要で、
context_type は **JSONB `response._meta.context_type`** に格納されるため、
本マイグレーションは **DDL を伴わない no-op** とする。

## 番号予約の意義

W2-BE6 では context_type の enum 拡張 (4→10) と
`out_of_scope` / `missing_fields` プロンプト応答の対応を行うが、いずれも
アプリケーションレベルの変更で完結する：

- enum 値はフロント (`frontend/lib/schemas/v2/enums.ts`) と
  バックエンド (`backend/app/schemas/v2/enums.py::AiContextType`) の
  StrEnum / Literal で両側を揃えて担保する。
- `ai_interpret_logs.response` JSONB カラムにはスキーマがないため、
  context_type 値の追加はカラム定義に影響しない。
- 旧名 (`event_create` / `override_create`) は Pydantic バリデータ層で
  新名 (`staff_event` / `staff_off`) に正規化されるため、過去ログを
  書き換える migration も不要 (履歴は履歴のまま残す)。

そのため実体としての DDL はゼロだが、Alembic の linear chain を保ち、
Wave 2 並行マージ時の番号衝突を防ぐために本ファイルを予約する。
`alembic upgrade head` は番号順に通過する。

将来 AI 関連で本格的な DDL が必要になった場合は、別 revision (0014 ではなく
後続番号) で追加する想定。

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
revision: str = "0014_v2_ai_scope_metadata"
down_revision: Union[str, Sequence[str], None] = "0013_v2_pending_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: context_type は JSONB `_meta` で持つためスキーマ変更不要."""
    pass


def downgrade() -> None:
    """No-op: スキーマ変更を含まないため rollback も不要."""
    pass
