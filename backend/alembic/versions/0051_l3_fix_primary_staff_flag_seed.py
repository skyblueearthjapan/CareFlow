"""Wave N-1: l3_fix_primary_staff フラグのデータ移行 (拠点名ハードコード除去).

Revision ID: 0051_l3_fix_primary_staff_flag_seed
Revises: 0050_release_pin_movability_locked
Create Date: 2026-07-03

## このマイグレーションの責務 (Wave N-1 R-1)

``_build_fixed_assignments`` の step 2 は従来「拠点名に '都賀' を含む」ハードコード
で primary staff (active role=staff の code 昇順先頭 1 名) を A コースに固定割当していた.
Wave N-1 では OfficeFeatureFlag ``l3_fix_primary_staff`` (enabled_at IS NOT NULL) に
設定化した (マルチテナント対応 / 他社事業所への展開でも拠点名に依存しない).

本 migration は既存運用を完全保存するため、 **拠点名に '都賀' を含む office** に
``l3_fix_primary_staff`` フラグを INSERT する (重複 INSERT は skip).

### upgrade ロジック

1. ``offices`` テーブルから name LIKE '%都賀%' AND deleted_at IS NULL の行を取得.
2. 各 office について ``office_feature_flags`` に既存行 (office_id, feature_key の一致)
   がなければ INSERT する (冪等).

### downgrade ロジック

本 migration で INSERT した ``l3_fix_primary_staff`` フラグ行を DELETE する
(office_id が '都賀' 名に該当する行のみ対象. 管理者が別途手動追加したフラグは除外
するため、 note='2026-07 拠点名ハードコード除去の移行' の行のみ削除する).

## PG / SQLite 互換

* 既存の制約・カラムには一切触らない (DDL なし).
* office_feature_flags は (office_id, feature_key) UNIQUE のため重複 INSERT は
  skip することで冪等性を担保する.
* UUID の生成は PG: gen_random_uuid() / SQLite: Python uuid4() で分岐
  (0022 v2_manager_course_seed と同じ作法).
* CURRENT_TIMESTAMP は PG・SQLite 両方で動作する.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0051_l3_fix_primary_staff_flag_seed"
down_revision: Union[str, Sequence[str], None] = "0050_release_pin_movability_locked"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FEATURE_KEY: str = "l3_fix_primary_staff"
_NOTE: str = "2026-07 拠点名ハードコード除去の移行"


def _upgrade_with_bind(bind: sa.engine.Connection) -> None:  # type: ignore[type-arg]
    """upgrade ロジック本体 (テストから直接呼び出せるよう分離)."""
    # ----- 1) 拠点名に '都賀' を含む active offices を取得 -----
    offices = bind.execute(
        sa.text("SELECT id FROM offices WHERE name LIKE '%都賀%' AND deleted_at IS NULL")
    ).fetchall()

    for office_row in offices:
        office_id = str(office_row[0])

        # ----- 2) 既存フラグ行があれば skip (冪等) -----
        existing = bind.execute(
            sa.text(
                """
                SELECT id FROM office_feature_flags
                WHERE office_id = :office_id
                  AND feature_key = :feature_key
                """
            ),
            {"office_id": office_id, "feature_key": _FEATURE_KEY},
        ).fetchone()
        if existing is not None:
            continue

        # ----- 3) INSERT -----
        new_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO office_feature_flags (
                    id, office_id, feature_key,
                    enabled_at, enabled_by_user_id, note,
                    created_at, updated_at
                ) VALUES (
                    :id, :office_id, :feature_key,
                    CURRENT_TIMESTAMP, NULL, :note,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "id": new_id,
                "office_id": office_id,
                "feature_key": _FEATURE_KEY,
                "note": _NOTE,
            },
        )


def _downgrade_with_bind(bind: sa.engine.Connection) -> None:  # type: ignore[type-arg]
    """downgrade ロジック本体 (テストから直接呼び出せるよう分離).

    note='2026-07 拠点名ハードコード除去の移行' の行のみ削除するため、
    管理者が別途手動追加したフラグには影響しない.
    """
    bind.execute(
        sa.text(
            """
            DELETE FROM office_feature_flags
            WHERE feature_key = :feature_key
              AND note = :note
            """
        ),
        {"feature_key": _FEATURE_KEY, "note": _NOTE},
    )


def upgrade() -> None:
    _upgrade_with_bind(op.get_bind())


def downgrade() -> None:
    _downgrade_with_bind(op.get_bind())
