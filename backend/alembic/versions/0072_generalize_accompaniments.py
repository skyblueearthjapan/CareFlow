"""同行割付の一般化 — trainee_accompaniments を accompaniments へ汎化.

Revision ID: 0072_generalize_accompaniments
Revises: 0071_visit_is_unplanned
Create Date: 2026-08-17

## このマイグレーションの責務

正典設計書 ``docs/plans/general-accompaniment-design.md`` §2 のデータ層。
「新人 (is_trainee) 専用」だった同行テーブルを、**一般スタッフの同行にも使える**
汎用テーブルへ改名する。データは 1 行も動かさない (rename + 列追加のみ)。

* ``trainee_accompaniments`` → ``accompaniments``
* ``trainee_accompaniment_defaults`` → ``accompaniment_defaults``
* 両テーブルの ``trainee_staff_id`` → ``accompanying_staff_id``
  (「新人の」ではなく「同行する側の」スタッフ、という意味の一般化)
* 両テーブルに ``kind VARCHAR(8) NOT NULL DEFAULT 'trainee'``
  (CHECK: 'trainee' / 'support') を追加。**既存行は全て 'trainee'** =
  新人同行として登録されたものだから、既定値がそのまま正しい。

``kind`` は API 入力では受け取らず、保存時にサーバが ``staff.is_trainee`` から
自動判定する (設計 §2: 詐称防止・判定の一元化)。DB 側は既定値と CHECK のみ持つ。

## 制約 / インデックス名

PostgreSQL はテーブルを rename しても制約・インデックス名を引き継ぐため、
``*_ta_*`` / ``*_tad_*`` という旧名が残る。実体と名前の乖離は将来の調査を
誤らせるので、``*_acc_*`` / ``*_accd_*`` へ揃えて rename する。

## SQLite 互換

テスト DB は ``Base.metadata.create_all`` でモデル定義から直接作られるため、
本ファイルは SQLite で実行されない。それでも ``op.rename_table`` /
``batch_alter_table`` で書ける範囲は dialect 非依存にし、SQLite が構文的に
持たない ``ALTER ... RENAME CONSTRAINT`` / ``ALTER INDEX ... RENAME`` のみ
PostgreSQL に限定する (誤って sqlite で流したときに列と表だけは正しく揃う)。

## downgrade

upgrade の完全な逆順 (kind drop → 列名戻し → 表名戻し → 制約/索引名戻し)。
``kind`` を drop すると 'support' で登録された一般同行と新人同行の区別は
失われるが、リンク自体は残る (行は消さない)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0072_generalize_accompaniments"
down_revision: str | Sequence[str] | None = "0071_visit_is_unplanned"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (旧名, 新名) — 週リンク側 (accompaniments)。
_LINK_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("ck_ta_target_type", "ck_acc_target_type"),
    ("ck_ta_course_presence", "ck_acc_course_presence"),
    ("ck_ta_visit_presence", "ck_acc_visit_presence"),
    ("ck_ta_source", "ck_acc_source"),
    ("uq_ta_trainee_course", "uq_acc_staff_course"),
    ("uq_ta_trainee_visit", "uq_acc_staff_visit"),
)
_LINK_INDEXES: tuple[tuple[str, str], ...] = (
    ("ix_ta_course", "ix_acc_course"),
    ("ix_ta_visit", "ix_acc_visit"),
    ("ix_ta_trainee", "ix_acc_staff"),
)

# (旧名, 新名) — 既定側 (accompaniment_defaults)。
_DEFAULT_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("uq_tad_trainee_weekday", "uq_accd_staff_weekday"),
    ("ck_tad_weekday", "ck_accd_weekday"),
)
_DEFAULT_INDEXES: tuple[tuple[str, str], ...] = (("ix_tad_trainee", "ix_accd_staff"),)

_KIND_CHECK = "kind IN ('trainee', 'support')"

_KIND_COMMENT = (
    "同行の種別。'trainee' = 新人同行 (staff.is_trainee=true)、"
    "'support' = 一般スタッフの同行。保存時にサーバが staff.is_trainee から"
    "自動判定する (API 入力では受け取らない)。"
)


def _is_pg() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _rename_constraints(table: str, pairs: Sequence[tuple[str, str]]) -> None:
    """制約名を rename する (PostgreSQL のみ)."""
    if not _is_pg():
        return
    for old, new in pairs:
        op.execute(f'ALTER TABLE {table} RENAME CONSTRAINT "{old}" TO "{new}"')


def _rename_indexes(pairs: Sequence[tuple[str, str]]) -> None:
    """インデックス名を rename する (PostgreSQL のみ)."""
    if not _is_pg():
        return
    for old, new in pairs:
        op.execute(f'ALTER INDEX "{old}" RENAME TO "{new}"')


def upgrade() -> None:
    # ----- 1. テーブル rename -----
    op.rename_table("trainee_accompaniments", "accompaniments")
    op.rename_table("trainee_accompaniment_defaults", "accompaniment_defaults")

    # ----- 2. 列 rename (trainee_staff_id -> accompanying_staff_id) -----
    op.alter_column(
        "accompaniments",
        "trainee_staff_id",
        new_column_name="accompanying_staff_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True) if _is_pg() else sa.String(36),
        existing_nullable=False,
    )
    op.alter_column(
        "accompaniment_defaults",
        "trainee_staff_id",
        new_column_name="accompanying_staff_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True) if _is_pg() else sa.String(36),
        existing_nullable=False,
    )

    # ----- 3. kind 列追加 (既存行は既定値 'trainee' のまま = 新人同行) -----
    for table in ("accompaniments", "accompaniment_defaults"):
        op.add_column(
            table,
            sa.Column(
                "kind",
                sa.String(length=8),
                nullable=False,
                server_default=sa.text("'trainee'"),
                comment=_KIND_COMMENT,
            ),
        )
    op.create_check_constraint("ck_acc_kind", "accompaniments", _KIND_CHECK)
    op.create_check_constraint("ck_accd_kind", "accompaniment_defaults", _KIND_CHECK)

    # ----- 4. 制約 / インデックス名を *_acc_* / *_accd_* へ -----
    _rename_constraints("accompaniments", _LINK_CONSTRAINTS)
    _rename_constraints("accompaniment_defaults", _DEFAULT_CONSTRAINTS)
    _rename_indexes(_LINK_INDEXES)
    _rename_indexes(_DEFAULT_INDEXES)


def downgrade() -> None:
    # ----- 4' 制約 / インデックス名を旧名へ戻す -----
    _rename_indexes([(new, old) for old, new in _DEFAULT_INDEXES])
    _rename_indexes([(new, old) for old, new in _LINK_INDEXES])
    _rename_constraints("accompaniment_defaults", [(new, old) for old, new in _DEFAULT_CONSTRAINTS])
    _rename_constraints("accompaniments", [(new, old) for old, new in _LINK_CONSTRAINTS])

    # ----- 3' kind 列を落とす -----
    op.drop_constraint("ck_accd_kind", "accompaniment_defaults", type_="check")
    op.drop_constraint("ck_acc_kind", "accompaniments", type_="check")
    for table in ("accompaniment_defaults", "accompaniments"):
        op.drop_column(table, "kind")

    # ----- 2' 列名を戻す -----
    op.alter_column(
        "accompaniment_defaults",
        "accompanying_staff_id",
        new_column_name="trainee_staff_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True) if _is_pg() else sa.String(36),
        existing_nullable=False,
    )
    op.alter_column(
        "accompaniments",
        "accompanying_staff_id",
        new_column_name="trainee_staff_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True) if _is_pg() else sa.String(36),
        existing_nullable=False,
    )

    # ----- 1' テーブル名を戻す -----
    op.rename_table("accompaniment_defaults", "trainee_accompaniment_defaults")
    op.rename_table("accompaniments", "trainee_accompaniments")
