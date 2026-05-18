"""v2 移行: ``weekday_priority`` (曜日優先度) を完全廃止する.

Revision ID: 0036_drop_weekday_priority
Revises: 0035_pfv_sub_office
Create Date: 2026-05-19

## このマイグレーションの責務

設計仕様書 v0.9 §4.1 で v2 schema から削除された "曜日優先度
(``weekday_priority``)" を、production DB に残る ``patients.weekly_pattern``
JSONB / ``patients.special_weekly_pattern`` JSONB の中から完全に取り除く.

### なぜ ``drop_column`` ではないか

``patients`` テーブルには ``weekday_priority`` という物理カラムは
**最初から存在しない** (0001_initial 参照). この概念は最初から
``weekly_pattern`` JSONB の 1 キーとしてのみ運用されていた:

```
weekly_pattern = {
    "frequency_per_week": ...,
    "preferred_weekdays": [...],
    "weekday_priority": "高|中|低",   ← これを消す
    ...
}
```

そのため本 migration は **JSONB 内のキー削除** で目的を達成する.
column drop は対象が存在しないため no-op.

### v2 移行コードとの関係

* application 層では既に ``WeeklyPatternV2`` (v2 schema) から
  ``weekday_priority`` 概念は削除済み. 読み側の参照はゼロだが
  ``extra='allow'`` で JSONB に残った旧キーは黙殺されていた.
* allocation engine (v1) の ``day_priority`` 参照、Excel importer の
  ``_norm_priority``, frontend ``WEEKDAY_PRIORITY_OPTIONS`` 等は本 PR で
  同時に削除済み.
* 残るのは production DB の JSONB に misshapen で残っている旧キーのみで、
  本 migration がそれを掃除する.

## PG 実装

``jsonb - 'weekday_priority'`` 演算子で対象キーを除いた新値を書き戻す.
``weekly_pattern`` および ``special_weekly_pattern`` の両方を対象とする
(両者は同じシェイプの JSONB).

```
UPDATE patients
SET weekly_pattern = weekly_pattern - 'weekday_priority'
WHERE weekly_pattern ? 'weekday_priority';
```

NULL 行 / 該当キー無しの行は除外する (``?`` 演算子は left NULL に対して
false を返すので安全).

## SQLite 実装

``jsonb - key`` 演算子は SQLite に存在しないため、Python 側で読み取って
書き戻す row-by-row 方針. SQLite は production では使われず、本 migration
の自動テスト用のみ. (テスト数件分の行のみ走るため非効率でも問題ない.)

## downgrade

破壊的削除 (旧データの "高/中/低" 値は復元不可能) なので、downgrade では
**何もしない** (no-op). 復元したい場合は別途データを供給する必要がある.
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0036_drop_weekday_priority"
down_revision: Union[str, Sequence[str], None] = "0035_pfv_sub_office"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_JSONB_COLUMNS: tuple[str, ...] = (
    "weekly_pattern",
    "special_weekly_pattern",
)


def _strip_key_sqlite(bind: sa.engine.Connection, column: str) -> None:
    """SQLite フォールバック: Python 側で read → modify → write."""
    rows = bind.execute(
        sa.text(f"SELECT id, {column} FROM patients WHERE {column} IS NOT NULL")
    ).fetchall()
    for row in rows:
        raw = row[1]
        if raw is None:
            continue
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except (TypeError, ValueError):
                continue
        else:
            value = raw
        if not isinstance(value, dict):
            continue
        if "weekday_priority" not in value:
            continue
        value.pop("weekday_priority", None)
        bind.execute(
            sa.text(f"UPDATE patients SET {column} = :val WHERE id = :id"),
            {"val": json.dumps(value, ensure_ascii=False), "id": row[0]},
        )


def upgrade() -> None:
    """旧 ``weekday_priority`` キーを weekly_pattern / special_weekly_pattern から除去."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    if is_pg:
        for column in _JSONB_COLUMNS:
            op.execute(
                sa.text(
                    f"""
                    UPDATE patients
                    SET {column} = {column} - 'weekday_priority'
                    WHERE {column} ? 'weekday_priority'
                    """
                )
            )
    else:
        for column in _JSONB_COLUMNS:
            _strip_key_sqlite(bind, column)


def downgrade() -> None:
    """No-op: 廃止済みキーの値は復元できない (情報損失)."""
    # 意図的に何もしない. 旧データを復元したい場合は外部バックアップから
    # 個別に書き戻す.
    return None
