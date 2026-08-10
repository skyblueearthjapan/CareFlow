"""patient_ng_staff — NG スタッフ (患者×スタッフ割当禁止) テーブル新設.

Revision ID: 0070_patient_ng_staff
Revises: 0069_retire_manager_role
Create Date: 2026-08-11

## このマイグレーションの責務

正典設計書 ``docs/plans/patient-ng-staff-design.md`` §3 のデータ層を作る。
患者ごとの「このスタッフは割り当てない」恒久ルールを 1 テーブルで表す
(一時的 NG は不要と PO 確定 = 決定3)。

* 複合主キー ``(patient_id, staff_id)``。行が存在する = NG
  (``patient_same_address_links`` (mig 0037) と同じ「NG のみ行を作る」方式)。
* ``note`` = 理由メモ (任意)、``decided_by_user_id`` = 設定者 (監査用)。
* ``ix_patient_ng_staff_staff`` は **逆引き** (staff_id → patients) 用。
  エンジンの一括ロード / スタッフ詳細サマリで使う。

## soft-delete の罠 (アプリ層で対応済)

FK は ``ON DELETE CASCADE`` だが、CareFlow の患者/スタッフ削除は ``deleted_at``
更新の soft delete なので CASCADE は **発火しない**。患者削除 API・スタッフ削除
API の両方でアプリ層から明示 DELETE している (mig 0037 と同じ既知罠)。

## SQLite 互換

pair_mode のような ENUM を持たないため、UUID 型のみ dialect 分岐する
(PG: ``postgresql.UUID`` / SQLite: ``String(36)``)。mig 0037 と同方針。

## downgrade

index → table の順に drop して完全に元へ戻す。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070_patient_ng_staff"
down_revision = "0069_retire_manager_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    uuid_type: sa.types.TypeEngine = postgresql.UUID(as_uuid=True) if is_pg else sa.String(36)
    now_default = sa.func.now() if is_pg else sa.func.current_timestamp()

    op.create_table(
        "patient_ng_staff",
        sa.Column(
            "patient_id",
            uuid_type,
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "staff_id",
            uuid_type,
            sa.ForeignKey("staff.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_user_id",
            uuid_type,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default,
        ),
        sa.PrimaryKeyConstraint("patient_id", "staff_id", name="pk_patient_ng_staff"),
    )
    # 逆引き用 (staff_id → patients). PK は patient→staff 方向しか効かない.
    op.create_index("ix_patient_ng_staff_staff", "patient_ng_staff", ["staff_id"])


def downgrade() -> None:
    op.drop_index("ix_patient_ng_staff_staff", table_name="patient_ng_staff")
    op.drop_table("patient_ng_staff")
