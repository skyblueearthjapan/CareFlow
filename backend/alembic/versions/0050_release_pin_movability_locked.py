"""P4-C: ピン留め解除済み行の movability='locked' 残骸を一括解放する.

Revision ID: 0050_release_pin_movability_locked
Revises: 0049_swap_dismissal_kind
Create Date: 2026-07-03

## このマイグレーションの責務 (P4-C 可動域解放 データ基盤)

P2-A で「is_pinned=True ⇒ movability='locked'」を migration 0047 backfill +
pfv_validator V6 矯正で導入したが、逆方向 (ピン留め解除時に locked を解放する処理) が
存在しなかった. 結果、ピン留めを外しても movability='locked' が残骸として残り、
改善提案が「完全固定N件で非表示」のままになる (P4-A の希望範囲内提案もブロックされる).

本 migration は既存データの一括クリーンアップを担う:

    UPDATE patient_fixed_visits
    SET movability = 'unknown'
    WHERE movability = 'locked' AND is_pinned = false

### 根拠 (なぜ非 pin の locked を全て unknown に落として安全か)

現時点で DB 上に存在する ``movability='locked'`` は、すべて 0047 backfill
(is_pinned=true の既存行を locked 化) もしくは pfv_validator V6 (PUT 時に
is_pinned=True の行を locked 矯正) 由来の「ピン由来ロック」である.

意図的に locked を選ぶ機能 = 可動域セレクタでの「完全固定」明示指定は、その利用実績が
本番でゼロであることを確認済 (= dismiss 昇格経路の locked も未使用). よって
``is_pinned=false ∧ movability='locked'`` の行は「ピンを外したのに残った残骸」だけであり、
一律 'unknown' へ戻して問題ない. ``is_pinned=true`` の locked は含意どおり維持する.

## PG / SQLite 互換 (0047/0049 の作法を踏襲)

* 既存の制約・カラムには一切触らない (ADD/DROP CONSTRAINT なし) ため、PG の命名規約
  (ck_%(table_name)s_%(constraint_name)s) 問題や SQLite の batch 再構築は不要.
* UPDATE の bool リテラルのみ dialect で分岐する (PG: ``false`` / SQLite: ``0``).
  0047 の backfill と同じ作法.

## downgrade

no-op (不可逆). backfill で 'unknown' に戻した行が、解除前に意図的 locked だったか
ピン由来残骸だったかを事後に判別する情報は残っていないため、機械的な復元はできない.
そもそも「ピンを外した行の locked 残骸解放」は正しい状態への是正であり、巻き戻す必要が
ない.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0050_release_pin_movability_locked"
down_revision: Union[str, Sequence[str], None] = "0049_swap_dismissal_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ピン留め解除済み (is_pinned=false) の locked 残骸を unknown に解放する.
    op.execute(
        "UPDATE patient_fixed_visits SET movability = 'unknown' "
        "WHERE movability = 'locked' AND is_pinned = "
        + ("false" if is_pg else "0")
    )


def downgrade() -> None:
    # 不可逆: 解放前の locked が意図的か残骸かを判別する情報が残らないため no-op.
    pass
