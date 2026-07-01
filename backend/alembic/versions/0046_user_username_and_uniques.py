"""users.username + email nullable + partial UNIQUE indexes (staff account linking P1a).

Revision ID: 0046_user_username_and_uniques
Revises: 0045_notification_reference
Create Date: 2026-07-01

## このマイグレーションの責務 (スタッフ×アカウント紐付け P1a)

管理者が ``users`` にスタッフを紐付けられる土台を作るため、``users`` に以下を加える:

1. ``username`` (String(32), NULL 可) 追加。スタッフはメール無しでもコードで
   識別できるようにする (ログイン識別子化そのものは P1b)。
2. ``email`` を **nullable 化** (NOT NULL 撤去)。スタッフ用アカウントはメール無し。
   既存 admin/manager は値を保持する (**行レベルは非破壊**)。
3. 既存の email UNIQUE 制約 / 通常インデックスを drop し、**部分 UNIQUE**
   ``ix_users_email_unique_alive`` (``email IS NOT NULL AND deleted_at IS NULL``)
   へ張り替える。NULL 複数を許しつつ有効行の一意性を担保する。
4. ``username`` の部分 UNIQUE ``ix_users_username_unique_alive``
   (``username IS NOT NULL AND deleted_at IS NULL``)。
5. ``staff_id`` の部分 UNIQUE ``ix_users_staff_id_unique_alive``
   (``staff_id IS NOT NULL AND deleted_at IS NULL``) — **1 スタッフ = 1 アカウント**
   を DB レベルで保証。作成前に既存重複が無いか pre-check (0032 パターン)。

いずれの部分 UNIQUE も既存 ``ix_staff_code_unique_alive`` (models/staff.py) と同型。
PostgreSQL / SQLite (3.8.0+) いずれも同一 raw SQL で partial UNIQUE INDEX を張れる。

## PostgreSQL / SQLite 両対応

* ``email`` nullable 化 + UNIQUE 制約 drop:
  - PG: ``op.alter_column`` + ``op.drop_constraint`` (制約名は inspector で実際の名を
    特定して drop するため ``uq_users_email`` / ``users_email_key`` いずれでも安全)。
  - SQLite: ``batch_alter_table`` でテーブル再構築 (0021 / 0035 パターン)。
* 部分 UNIQUE INDEX 3 本は両ダイアレクト共通の raw SQL (0032 パターン)。

## downgrade

部分 UNIQUE 3 本を drop し、``email`` の通常 UNIQUE 制約 + index を復元、
``email`` を NOT NULL に戻し、``username`` 列を drop する。
**注意**: ``email`` を NOT NULL に戻す際、``email IS NULL`` の行 (スタッフ用アカウント)
が 1 件でも存在すると downgrade は失敗する。先にそれらの行を退避 / 削除すること。
"""

# ruff: noqa: I001, UP007, UP035
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0046_user_username_and_uniques"
down_revision: Union[str, Sequence[str], None] = "0045_notification_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # pre-check: staff_id の重複 (alive) が無いことを確認してから
    # 部分 UNIQUE を張る (0032 パターン)。現状 linked=0 なので通る。
    # ------------------------------------------------------------------
    duplicates = bind.execute(
        sa.text(
            "SELECT staff_id, COUNT(*) AS cnt FROM users "
            "WHERE staff_id IS NOT NULL AND deleted_at IS NULL "
            "GROUP BY staff_id HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            "Cannot add UNIQUE constraint on users.staff_id: duplicate links found: "
            f"{[(str(row[0]), row[1]) for row in duplicates]}. "
            "Resolve duplicate staff linkage before migrating."
        )

    if is_pg:
        # username 追加 + email nullable 化。
        op.add_column("users", sa.Column("username", sa.String(length=32), nullable=True))
        op.alter_column(
            "users",
            "email",
            existing_type=sa.String(length=255),
            nullable=True,
        )
        # 既存 email UNIQUE 制約を実名で特定して drop (命名規約 uq_users_email /
        # SQLAlchemy 既定 users_email_key いずれでも拾えるよう inspector を使う)。
        insp = sa.inspect(bind)
        for uc in insp.get_unique_constraints("users"):
            if uc.get("column_names") == ["email"]:
                op.drop_constraint(uc["name"], "users", type_="unique")
        # 通常 index ix_users_email が残っていれば drop (部分 UNIQUE に張り替え)。
        existing_idx = {ix["name"] for ix in insp.get_indexes("users")}
        if "ix_users_email" in existing_idx:
            op.drop_index("ix_users_email", table_name="users")
    else:
        # SQLite (テスト): テーブル再構築で列追加 / nullable 化 / UNIQUE 制約 drop。
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("username", sa.String(length=32), nullable=True))
            batch.alter_column(
                "email",
                existing_type=sa.String(length=255),
                nullable=True,
            )
            batch.drop_constraint("uq_users_email", type_="unique")
        # batch 再構築で復元された通常 index を落とす (部分 UNIQUE に張り替え)。
        op.execute("DROP INDEX IF EXISTS ix_users_email")

    # ------------------------------------------------------------------
    # 部分 UNIQUE INDEX 3 本 (PG / SQLite 共通 raw SQL, 0032 パターン)。
    # ------------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX ix_users_email_unique_alive ON users (email) "
        "WHERE email IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_users_username_unique_alive ON users (username) "
        "WHERE username IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_users_staff_id_unique_alive ON users (staff_id) "
        "WHERE staff_id IS NOT NULL AND deleted_at IS NULL"
    )

    # ------------------------------------------------------------------
    # CHECK 制約: ログイン識別子が最低1つあることを DB レベルで保証 (多層防御)。
    # アプリ層バリデーション (admin.py) と二重で担保する。
    # ------------------------------------------------------------------
    _check_name = "ck_users_has_login_identifier"
    _check_cond = "email IS NOT NULL OR username IS NOT NULL"
    if is_pg:
        op.execute(f"ALTER TABLE users ADD CONSTRAINT {_check_name} CHECK ({_check_cond})")
    else:
        # SQLite は ALTER TABLE ... ADD CONSTRAINT 不可のため batch 再構築。
        with op.batch_alter_table("users") as batch:
            batch.create_check_constraint(_check_name, _check_cond)


def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 安全確認: NULL email のユーザーが残っていると email NOT NULL 復元が失敗する。
    # NULL 行を先に退避 / 削除してから downgrade すること。
    # ------------------------------------------------------------------
    null_email_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE email IS NULL")
    ).scalar()
    if null_email_count:
        raise RuntimeError(
            f"Cannot downgrade 0046: {null_email_count} user(s) with NULL email found. "
            "Remove or migrate those rows before downgrading "
            "(restoring email NOT NULL constraint would fail)."
        )

    op.execute("DROP INDEX IF EXISTS ix_users_staff_id_unique_alive")
    op.execute("DROP INDEX IF EXISTS ix_users_username_unique_alive")
    op.execute("DROP INDEX IF EXISTS ix_users_email_unique_alive")

    if is_pg:
        op.drop_constraint("ck_users_has_login_identifier", "users", type_="check")
        # email を NOT NULL に戻す (NULL 行があると失敗する — 上記 docstring 参照)。
        op.alter_column(
            "users",
            "email",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        op.create_unique_constraint("uq_users_email", "users", ["email"])
        op.create_index("ix_users_email", "users", ["email"])
        op.drop_column("users", "username")
    else:
        with op.batch_alter_table("users") as batch:
            batch.drop_constraint("ck_users_has_login_identifier", type_="check")
            # email を NOT NULL に戻す (NULL 行があると失敗する)。
            batch.alter_column(
                "email",
                existing_type=sa.String(length=255),
                nullable=False,
            )
            batch.create_unique_constraint("uq_users_email", ["email"])
            batch.drop_column("username")
        op.create_index("ix_users_email", "users", ["email"])
