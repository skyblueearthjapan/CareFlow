"""P3-②: suggestion_dismissals.ck_sd_kind に 'swap' を追加 (スワップ提案の却下記憶).

Revision ID: 0049_swap_dismissal_kind
Revises: 0048_visit_manual_staff_override
Create Date: 2026-07-03

## このマイグレーションの責務 (スワップ提案 P3-② データ基盤)

改善提案に「2 患者の入れ替え (swap)」を追加するに伴い、その却下記憶を
``suggestion_dismissals`` に kind='swap' として保存できるようにする.

* 変更点は 1 つだけ: ``ck_sd_kind`` CHECK を
  ``('time_change','day_change')`` → ``('time_change','day_change','swap')`` に拡張.
* 指紋 (patient_id, kind, target_weekday) / UNIQUE / 他 CHECK / FK は不変.
* ORM モデル (SuggestionDismissal) の CHECK も同期済 (create_all テーブルと一致).

## 適用状況 (0047/0048 の混在に対する 0049 の位置づけ)

* 0047 = 本番適用済 (movability + suggestion_dismissals 新設).
* 0048 = 未デプロイ (ローカルコミットのみ, visits.manual_staff_override).
* 0049 = 本 migration. down_revision=0048 として新規に ALTER する.

## PG / SQLite 互換 (0047 の作法を踏襲)

* PG: raw ``ALTER TABLE ... DROP/ADD CONSTRAINT`` で CHECK を張り替える.
* SQLite (テスト): ``batch_alter_table`` で drop → create (名前付き CHECK を再構築).

## downgrade

``ck_sd_kind`` を元の 2 値 (``time_change`` / ``day_change``) に戻す.
下げる前に kind='swap' 行が残っていると CHECK 復元で失敗しうるため、downgrade は
先に kind='swap' 行を削除してから CHECK を戻す (完全に元へ戻す).
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0049_swap_dismissal_kind"
down_revision: Union[str, Sequence[str], None] = "0048_visit_manual_staff_override"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_KIND_CHECK_NAME = "ck_sd_kind"
_KIND_CHECK_NEW = "kind IN ('time_change','day_change','swap')"
_KIND_CHECK_OLD = "kind IN ('time_change','day_change')"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        op.execute(
            f"ALTER TABLE suggestion_dismissals DROP CONSTRAINT {_KIND_CHECK_NAME}"
        )
        op.execute(
            f"ALTER TABLE suggestion_dismissals ADD CONSTRAINT {_KIND_CHECK_NAME} "
            f"CHECK ({_KIND_CHECK_NEW})"
        )
    else:
        # SQLite は ALTER TABLE ... DROP/ADD CONSTRAINT 不可のため batch 再構築.
        with op.batch_alter_table("suggestion_dismissals") as batch:
            batch.drop_constraint(_KIND_CHECK_NAME, type_="check")
            batch.create_check_constraint(_KIND_CHECK_NAME, _KIND_CHECK_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # CHECK を 2 値に戻す前に、拡張値 'swap' の行を削除 (残ると CHECK 復元で失敗).
    op.execute("DELETE FROM suggestion_dismissals WHERE kind = 'swap'")

    if is_pg:
        op.execute(
            f"ALTER TABLE suggestion_dismissals DROP CONSTRAINT {_KIND_CHECK_NAME}"
        )
        op.execute(
            f"ALTER TABLE suggestion_dismissals ADD CONSTRAINT {_KIND_CHECK_NAME} "
            f"CHECK ({_KIND_CHECK_OLD})"
        )
    else:
        with op.batch_alter_table("suggestion_dismissals") as batch:
            batch.drop_constraint(_KIND_CHECK_NAME, type_="check")
            batch.create_check_constraint(_KIND_CHECK_NAME, _KIND_CHECK_OLD)
