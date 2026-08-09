"""アカウント権限の二軸分離 — 旧 'manager' 権限を 'admin' へ移行 (PO 決定 2026-08-09).

Revision ID: 0069_retire_manager_role
Revises: 0068_inbound_snapshots
Create Date: 2026-08-09

## このマイグレーションの責務

アカウント権限 (users.role) を「管理者(admin) / 一般(staff)」の 2 値へ統一する。
旧 'manager' 権限のアカウントは admin へ昇格 (対象: 本番 1 件・川名様 s001。
PO 確認済み — admin 限定だった削除系・カイポケ連携等も可能になる)。

スタッフマスタの業務ロール (staff.role: staff/manager) は **別物** であり
一切変更しない (スケジュールエンジン専用)。

## downgrade

no-op。どの行が旧 manager だったかを復元する情報を持たないため。
"""

from __future__ import annotations

from alembic import op

revision = "0069_retire_manager_role"
down_revision = "0068_inbound_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'admin' WHERE role = 'manager'")


def downgrade() -> None:
    pass
