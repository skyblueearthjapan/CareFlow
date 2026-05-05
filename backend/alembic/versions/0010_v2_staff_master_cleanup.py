"""W1-BE2: v2 staff master cleanup (drop 8 cols + status enum normalization)

Revision ID: 0010_v2_staff_master_cleanup
Revises: 0009_v2_patient_master_cleanup
Create Date: 2026-05-06

設計仕様書 v0.9 §4.2 / 実装手順書 v0.2 §2 W1-BE2 / migration 予約表 0010 番号。

## スコープ

スタッフマスタを v2 (§4.2) 整理に従い、以下の **6 論理項目 (= 8 物理カラム)**
を削除する:

  1. ``can_double_team``               — 全員可能。フラグ不要
  2. ``home_address``                  ┐
  3. ``home_lat``                      │ 自宅情報。v2 は主拠点起点運用に統一
  4. ``home_lng``                      ┘
  5. ``areas``                         — 拠点の担当市区町村で代替 (§4.3)
  6. ``max_per_day``                   — v2 は全員 6 件で固定
  7. ``skill_level``                   — 患者の指定タイプ廃止により未使用化
  8. ``assignment_volume``             — soft_cap 調整不要 (全員 6 件固定)

## 状態 (`status`) の 3 値正規化

`active` / `on_leave` / `retired` の 3 値に統一する (§4.2 確定)。

DB 物理型は ``String(16)`` のまま (Alembic 上は CHECK 制約を追加するに留める)。
ロジック層 (Pydantic / API) で Literal バリデートする方針。本マイグレーションは
**既存の値 (例: v1 の `inactive`) を新 3 値へマッピングするバックフィル** を
実施する:

  * ``active``    -> ``active``     (在籍)
  * ``inactive``  -> ``retired``    (退職と見做す。当面の運用上の暫定値)
  * その他       -> ``active``     (フォールバック)

なお、より精緻な状態切り分け (休職を別途記録) は本ブランチ範囲外。

## バックアップテーブル ``_v1_backup_staff_w1be2``

expand-contract 戦略 (実装手順書 §11 / 移行予約表 §3.3) に基づき、削除する
8 カラムは **物理 DROP の前にバックアップテーブルへコピー** する。`down`
ではバックアップから ``staff`` 本体へ書き戻し、列を再追加する。

* ``up``:   バックアップ表を新規作成 → 8 カラムを INSERT SELECT で移送 → DROP
* ``down``: 8 カラムを再追加 → バックアップ表から UPDATE で復元 → バックアップ表 DROP

## SQLite 互換性

CI / 単体テストは SQLite を使う (conftest.py 参照)。SQLite は
``ALTER TABLE DROP COLUMN`` を 3.35.0 (2021-03-12) でサポートしているが、
Alembic が出力する DDL は基本的に PostgreSQL を想定している。
本マイグレーションは ``op.drop_column`` を使い、双方の方言で動作することを
意図する (テスト conftest は ``Base.metadata.create_all`` を使うので、
本 migration の up/down は **明示的に alembic を実行する Phase でのみ走る**)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_v2_staff_master_cleanup"
down_revision: str | None = "0009_v2_patient_master_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 削除対象カラム (本ブランチ範囲: §4.2 削除 6 項目 = 8 物理カラム)
_DROP_COLUMNS: tuple[str, ...] = (
    "can_double_team",
    "home_address",
    "home_lat",
    "home_lng",
    "areas",
    "max_per_day",
    "skill_level",
    "assignment_volume",
)

# バックアップテーブル名 (実装手順書 §11 expand-contract 第 1 段)
_BACKUP_TABLE = "_v1_backup_staff_w1be2"


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. バックアップテーブル作成
    # -----------------------------------------------------------------
    # 本マイグレーション以前の値を復元できるよう、staff の id + 8 カラムを
    # 受け皿テーブルへコピーしておく。物理 DROP は Wave 6 (0015) で実施する
    # 想定だが、本ブランチ単体でも up/down が完結するように `down` で
    # バックアップから戻すロジックを併記する。
    op.create_table(
        _BACKUP_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("can_double_team", sa.Boolean(), nullable=True),
        sa.Column("home_address", sa.String(length=255), nullable=True),
        sa.Column("home_lat", sa.Numeric(10, 7), nullable=True),
        sa.Column("home_lng", sa.Numeric(10, 7), nullable=True),
        sa.Column(
            "areas",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=True,
        ),
        sa.Column("max_per_day", sa.Integer(), nullable=True),
        sa.Column("skill_level", sa.String(length=16), nullable=True),
        sa.Column("assignment_volume", sa.String(length=16), nullable=True),
        sa.Column(
            "backed_up_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_BACKUP_TABLE}"),
    )

    # -----------------------------------------------------------------
    # 2. staff -> backup へ移送
    # -----------------------------------------------------------------
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_BACKUP_TABLE} (
                id, can_double_team, home_address, home_lat, home_lng,
                areas, max_per_day, skill_level, assignment_volume
            )
            SELECT id, can_double_team, home_address, home_lat, home_lng,
                   areas, max_per_day, skill_level, assignment_volume
            FROM staff
            """
        )
    )

    # -----------------------------------------------------------------
    # 3. status の値を 3 値 (active / on_leave / retired) へ正規化
    # -----------------------------------------------------------------
    # 既存の ``inactive`` を ``retired`` に寄せる (休職は別途運用で再設定)。
    # その他、3 値以外の値は ``active`` にフォールバック。
    op.execute(
        sa.text(
            """
            UPDATE staff
               SET status = CASE
                   WHEN status = 'active'   THEN 'active'
                   WHEN status = 'on_leave' THEN 'on_leave'
                   WHEN status = 'retired'  THEN 'retired'
                   WHEN status = 'inactive' THEN 'retired'
                   ELSE 'active'
               END
            """
        )
    )

    # -----------------------------------------------------------------
    # 4. CHECK 制約で DB 側の 3 値も担保
    # -----------------------------------------------------------------
    # SQLite は ALTER TABLE ADD CONSTRAINT を直接サポートしないため、
    # batch モードで再作成する。
    with op.batch_alter_table("staff") as batch:
        batch.create_check_constraint(
            "ck_staff_status_v2",
            "status IN ('active', 'on_leave', 'retired')",
        )

    # -----------------------------------------------------------------
    # 5. 8 カラムを DROP
    # -----------------------------------------------------------------
    for col in _DROP_COLUMNS:
        op.drop_column("staff", col)


def downgrade() -> None:
    # -----------------------------------------------------------------
    # 1. 8 カラムを再追加 (元の型・nullable / server_default に揃える)
    # -----------------------------------------------------------------
    op.add_column(
        "staff",
        sa.Column(
            "can_double_team",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "staff",
        sa.Column("home_address", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("home_lat", sa.Numeric(10, 7), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("home_lng", sa.Numeric(10, 7), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column(
            "areas",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=True,
        ),
    )
    op.add_column(
        "staff",
        sa.Column(
            "max_per_day",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("6"),
        ),
    )
    op.add_column(
        "staff",
        sa.Column("skill_level", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "staff",
        sa.Column("assignment_volume", sa.String(length=16), nullable=True),
    )

    # -----------------------------------------------------------------
    # 2. CHECK 制約を解除 (元状態は CHECK なし)
    # -----------------------------------------------------------------
    with op.batch_alter_table("staff") as batch:
        batch.drop_constraint("ck_staff_status_v2", type_="check")

    # -----------------------------------------------------------------
    # 3. バックアップから復元
    # -----------------------------------------------------------------
    op.execute(
        sa.text(
            f"""
            UPDATE staff AS s
               SET can_double_team   = COALESCE(b.can_double_team, false),
                   home_address      = b.home_address,
                   home_lat          = b.home_lat,
                   home_lng          = b.home_lng,
                   areas             = b.areas,
                   max_per_day       = COALESCE(b.max_per_day, 6),
                   skill_level       = b.skill_level,
                   assignment_volume = b.assignment_volume
              FROM {_BACKUP_TABLE} AS b
             WHERE s.id = b.id
            """
        )
    )

    # -----------------------------------------------------------------
    # 4. status の値を v1 形式に戻す
    # -----------------------------------------------------------------
    # 退職 (retired) を v1 の inactive に寄せる。
    op.execute(
        sa.text(
            """
            UPDATE staff
               SET status = CASE
                   WHEN status = 'retired'  THEN 'inactive'
                   WHEN status = 'on_leave' THEN 'inactive'
                   ELSE status
               END
            """
        )
    )

    # -----------------------------------------------------------------
    # 5. バックアップテーブル DROP
    # -----------------------------------------------------------------
    op.drop_table(_BACKUP_TABLE)
