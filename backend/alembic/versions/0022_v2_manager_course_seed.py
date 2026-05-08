"""W16-BE1: マネージャー対応コーステンプレート (M) 自動 seed.

Revision ID: 0022_v2_w16_manager_course_seed
Revises: 0021_v2_course_templates_courses_uniqueness
Create Date: 2026-05-08

## このマイグレーションの責務 (Wave 16 Phase A / A-1)

Wave 16 ではスケジュール UI を「コース行」から「スタッフ別テーブル」に転換する。
コース概念は裏方として維持しつつ、マネージャー (``staff.role='manager'``) は
専用の **M コース** に固定で割り付けるため、各拠点に対し M ラベルの
``course_templates`` を seed する。

### upgrade ロジック

1. ``offices`` テーブル全行を走査 (deleted_at IS NULL)
2. 拠点 (office_id) ごとに、active manager 数 N を集計
   - active manager = ``staff.status='active' AND staff.role='manager'
     AND staff.deleted_at IS NULL AND staff.primary_office_id = office.id``
3. N >= 1 の拠点について、
   ``course_templates`` に ラベル "M", "M2", ..., "MN" を **存在しなければ INSERT**
   - capacity = 月7/火7/水7/木7/金7/土5/日0 (本店 B 同等の標準枠)
   - notes = 'auto-seeded (W16): manager course'
4. 既存 (UPSERT pattern) の M ラベル行があれば skip (= 何もしない)

### downgrade ロジック

* W16 で seed した M / M2 / .. / M9 の course_templates 行を **実 delete**
  (論理削除ではなく物理 DELETE)
* 既存 ``courses.template_id`` は ON DELETE SET NULL なので
  template 削除に伴う FK 違反は起きない

## SQLite 互換

* ``op.execute`` で生 SQL を流す。SQLite / PostgreSQL 両対応の標準 SQL のみ使用。
* gen_random_uuid() は PostgreSQL のみ。SQLite テスト環境では SQLAlchemy 経由で
  insert するときに UUID をアプリ側生成 (Python uuid4) する。

## migration chain

down_revision = "0021_v2_course_templates_courses_uniqueness"
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import uuid
from typing import Sequence, Union


import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0022_v2_w16_manager_course_seed"
down_revision: Union[str, Sequence[str], None] = "0021_v2_course_templates_courses_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 拠点ごとに作成する最大 manager 数 (M, M2, .., M9 まで)
_MAX_MANAGERS_PER_OFFICE: int = 9


def _label_for(index: int) -> str:
    """0-based index を ラベル "M" / "M2" / "M3" / ... に変換する."""
    if index == 0:
        return "M"
    return f"M{index + 1}"


def upgrade() -> None:
    bind = op.get_bind()

    # ----- 1) 全 active office を取得 -----
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

        # ----- 2) 拠点ごとの active manager 数を集計 -----
        manager_count_row = bind.execute(
            sa.text(
                """
                SELECT COUNT(*) FROM staff
                WHERE status = 'active'
                  AND role = 'manager'
                  AND deleted_at IS NULL
                  AND primary_office_id = :office_id
                """
            ),
            {"office_id": str(office_id)},
        ).fetchone()
        manager_count = int(manager_count_row[0]) if manager_count_row else 0

        if manager_count <= 0:
            continue

        # 上限ガード (= 設計上の安全装置)
        manager_count = min(manager_count, _MAX_MANAGERS_PER_OFFICE)

        # ----- 3) M / M2 / ... / MN を upsert -----
        for i in range(manager_count):
            label = _label_for(i)

            # 既存行 (deleted_at IS NULL) があれば skip
            existing = bind.execute(
                sa.text(
                    """
                    SELECT id FROM course_templates
                    WHERE office_id = :office_id
                      AND label = :label
                      AND deleted_at IS NULL
                    """
                ),
                {"office_id": str(office_id), "label": label},
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
                        :id, :office_id, :label,
                        7, 7, 7,
                        7, 7, 5, 0,
                        :notes, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "id": new_id,
                    "office_id": str(office_id),
                    "label": label,
                    "notes": "auto-seeded (W16): manager course",
                },
            )


def downgrade() -> None:
    """W16 で seed された M / M2 / .. / M9 を物理 DELETE する.

    courses.template_id は ON DELETE SET NULL なので FK 違反は起きない。
    """
    bind = op.get_bind()

    # M / M2 / ... / M9 の全ラベル
    labels = [_label_for(i) for i in range(_MAX_MANAGERS_PER_OFFICE)]
    for label in labels:
        bind.execute(
            sa.text(
                """
                DELETE FROM course_templates
                WHERE label = :label
                  AND notes = 'auto-seeded (W16): manager course'
                """
            ),
            {"label": label},
        )
