"""CareFlow Wave Next 2 cross-review [C1]: 全 active office に M template を seed.

Revision ID: 0033_v2_seed_m_template_all_offices
Revises: 0032_staff_code_unique_partial
Create Date: 2026-05-18

## このマイグレーションの責務 (CRITICAL [C1])

0022 は ``active manager 数 >= 1`` の拠点にしか M ラベル template を seed しない
ため、manager 不在の active office には M template が存在しない. その状態で
``_resolve_course_for_code`` が ``code="M"`` を解決しようとすると:

- 旧実装: 拠点の最初の有効 template (= A や B) を template_id に充ててしまい、
  Course(code="M", template_id=A) のような不整合な行が生成される
  (FE は template.label の先頭文字と Course.code を照合するため UI に出ない)
- 新実装 (本 PR): code に一致する template が無いと None を返し apply 拒否
  → manager 不在 office で M overflow が発生すると Course 作成不能になる

本マイグレーションは **manager 数に関係なく** 全 active office (= deleted_at
IS NULL) に対して、M label の template が無ければ INSERT する.

### upgrade ロジック

1. ``offices.deleted_at IS NULL`` を全行走査
2. 拠点ごとに ``course_templates`` で ``label='M' AND deleted_at IS NULL`` の
   行を探す.
3. 存在しなければ M template を INSERT する.
   - capacity = 月7/火7/水7/木7/金7/土5/日0 (0022 と同じ標準枠)
   - notes = 'auto-seeded (W-Next2): manager course (manager-less offices)'
     (0022 と区別できる識別子. downgrade で本 migration 由来分だけ削除する)

### downgrade ロジック

* 本 migration で seed した M template のみ物理 DELETE する.
* 0022 が seed した M template (``notes`` = 'auto-seeded (W16): manager course')
  は **触らない** (0022 の downgrade に委ねる).
* ``courses.template_id`` は ON DELETE SET NULL なので FK 違反は起きない.

## SQLite 互換

* ``op.execute`` で生 SQL を流す. SQLite / PostgreSQL 両対応の標準 SQL.
* gen_random_uuid() は PostgreSQL のみ. SQLite では Python uuid4 で生成.

## migration chain

down_revision = "0032_staff_code_unique_partial"
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import uuid
from typing import Sequence, Union


import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0033_v2_seed_m_template_all_offices"
down_revision: Union[str, Sequence[str], None] = "0032_staff_code_unique_partial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 0022 と区別できる識別 notes. downgrade で本 migration 由来のみ DELETE するため.
_SEED_NOTES: str = "auto-seeded (W-Next2): manager course (manager-less offices)"


def upgrade() -> None:
    bind = op.get_bind()

    # 全 active office を取得
    offices = bind.execute(
        sa.text(
            """
            SELECT id FROM offices
            WHERE deleted_at IS NULL
            """
        )
    ).fetchall()

    for office_row in offices:
        office_id = office_row[0]

        # 既に M label template が active で存在するか確認
        existing = bind.execute(
            sa.text(
                """
                SELECT id FROM course_templates
                WHERE office_id = :office_id
                  AND label = 'M'
                  AND deleted_at IS NULL
                """
            ),
            {"office_id": str(office_id)},
        ).fetchone()
        if existing is not None:
            continue

        new_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO course_templates (
                    id, office_id, label,
                    capacity_mon, capacity_tue, capacity_wed,
                    capacity_thu, capacity_fri, capacity_sat, capacity_sun,
                    notes, deleted_at, created_at, updated_at
                ) VALUES (
                    :id, :office_id, 'M',
                    7, 7, 7,
                    7, 7, 5, 0,
                    :notes, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": new_id,
                "office_id": str(office_id),
                "notes": _SEED_NOTES,
            },
        )


def downgrade() -> None:
    """本 migration で seed された M template のみを物理 DELETE する.

    notes で 0022 由来 ('auto-seeded (W16): manager course') と区別する.
    courses.template_id は ON DELETE SET NULL なので FK 違反は起きない.
    """
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM course_templates
            WHERE label = 'M'
              AND notes = :notes
            """
        ),
        {"notes": _SEED_NOTES},
    )
