"""機械が付けた movability='locked' を 'unknown' へ戻す (PO 決定 2026-08-08).

Revision ID: 0065_reset_machine_set_movability_locked
Revises: 0064_special_visit_week
Create Date: 2026-08-08

## このマイグレーションの責務

``is_pinned=true AND movability='locked'`` の行を ``movability='unknown'`` に戻す。

## なぜ必要か

現時点でピン留め済み行に付いている 'locked' は、**現場が選んだ値ではない**。
次の 2 段階で機械的に付与されたものである。

  1. migration 0037 (2026-05-22): ``is_pinned`` 列を ``server_default=true`` で
     追加した。全行 UPDATE の lock 時間を避けるための技法であり、運用上の意図では
     ない。結果、当時存在した固定枠が全件ピン留め状態になった。
  2. migration 0047 backfill + ``pfv_validator`` の旧 V6: 「is_pinned=True ⇒
     movability='locked'」という含意のもと、ピン留め行の可動域を 'locked' に
     矯正した。V6 は PUT のたびに発火するため、以後も保存のたびに上書きされ続けた。

さらに FE (PatientFixedVisitsPanel) はピン留め行の可動域セレクタを隠し、送信時に
``is_pinned ? 'locked' : movability`` と強制していた。つまり **ピン留め行の可動域を
人が入力できる経路は存在しなかった**。よってこれらの 'locked' に現場の判断は一切
含まれていない。

## なぜ今戻すのか

同じリリースで可動域とピン留めを独立した 2 軸に変更した (旧 V6 の強制と
``_release_pin_lock`` のリセットを廃止)。可動域は「一度設定したら残る、現場の判断の
記録」になる。

ここで機械由来の 'locked' を残すと、一括ピン解除をしても大半の枠が「完全固定」の
ままとなり、「一括解除して全体最適化の提案を出させる」という運用が動かなくなる。
また、これから現場が入力していく **本物の判断** と機械由来の値が区別できなくなる。

## 対象外 (意図的に残すもの)

``is_pinned=false AND movability='locked'`` の行は **変更しない**。
migration 0050 が過去のピン由来残骸を一括解放済みであり、かつ旧 V6 は非 pinned 行を
locked にしないため、0050 以降に出現したこの状態は「現場が可動域セレクタで
『完全固定』を明示選択した」場合に限られる。= 本物の判断なので保持する。

## 安全性

* ピン留め (``is_pinned``) は変更しない。よって本 migration 単体では
  **どの訪問も動かない** (ピン留めが凍結を担保し続ける)。
* 可動域を再設定するまでの間に一括ピン解除を行うと、対象枠は動きうる。
  運用手順として「先に完全固定を設定してから一括解除する」ことを申し送る。

## downgrade

不可逆 (no-op)。戻した 'unknown' が元々機械由来だったか現場由来だったかを判別する
情報が残らないため。0050 と同じ方針。
"""

# ruff: noqa: I001
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0065_reset_machine_set_movability_locked"
down_revision: str | Sequence[str] | None = "0064_special_visit_week"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    true_literal = "true" if bind.dialect.name == "postgresql" else "1"
    op.execute(
        sa.text(
            "UPDATE patient_fixed_visits SET movability = 'unknown' "
            f"WHERE movability = 'locked' AND is_pinned = {true_literal}"
        )
    )


def downgrade() -> None:
    # 不可逆: 解放前の 'locked' が機械由来か現場由来かを判別する情報が残らない.
    pass
