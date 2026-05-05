"""W2-BE5 v2 pending_requests

Revision ID: 0013_v2_pending_requests
Revises: 0012_v2_courses_visit_assignments
Create Date: 2026-05-06

設計仕様書 v0.9 §3.5 / §4.4 に基づき、業務承認ワークフロー用の新規テーブル
``pending_requests`` を追加する。

## このマイグレーションの責務

- ``pending_requests`` テーブル新設 (§4.4 のスキーマ通り)
- 5 つのインデックス追加:
    * ``ix_pending_requests_status_created_at``       — pending 一覧高速取得
    * ``ix_pending_requests_target_staff_status``      — スタッフ別フィルタ
    * ``ix_pending_requests_target_patient_status``    — 患者別フィルタ
    * ``ix_pending_requests_target_date_status``       — 日付別フィルタ
    * ``ix_pending_requests_requester_user_id``        — 自分の申請の取得用 (補助)
- ``request_type`` / ``request_status`` / ``request_scope`` の値域チェック制約
  (PostgreSQL のみ; SQLite テストでは省略)
- FK: requester_user_id, target_staff_id, target_patient_id, approved_by,
  rejected_by, ai_interpret_log_id

破壊的変更なし。``downgrade`` は ``pending_requests`` テーブルをそのまま DROP
する (rollback 容易)。enum は値域 CHECK 制約として実装するため、enum 型の
DROP は不要。

## Wave 0-A 予約番号

`docs/plans/v2-migration-reservations.md` の予約スロット 0013 に対応。
``down_revision`` は予約表の通り `0012_v2_courses_visit_assignments`
(W2-BE4) を指す。
"""

# ruff: noqa: I001, UP007, UP035
# 既存の Wave 1 / v1 alembic revisions と同じスタイル (typing.Union / typing.Sequence)
# を維持するため lint を抑制する。本リポジトリの ruff 設定では alembic/versions が
# extend-exclude 対象だが、pre-commit hook はリポジトリルートで ruff を実行するため
# pyproject の extend-exclude が効かない。
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0013_v2_pending_requests"
down_revision: Union[str, Sequence[str], None] = "0012_v2_courses_visit_assignments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REQUEST_TYPE_VALUES: tuple[str, ...] = (
    "staff_off",
    "staff_event",
    "staff_mentor",
    "staff_create",
    "patient_create",
    "patient_cancel",
    "patient_reschedule",
    "patient_special_week_on",
    "patient_special_week_off",
)
_REQUEST_STATUS_VALUES: tuple[str, ...] = ("pending", "approved", "rejected")
_REQUEST_SCOPE_VALUES: tuple[str, ...] = ("one_time", "permanent")


def _values_csv(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ------------------------------------------------------------------
    # 1) Create pending_requests table
    # ------------------------------------------------------------------
    if is_pg:
        op.create_table(
            "pending_requests",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "requester_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "users.id",
                    ondelete="RESTRICT",
                    name="fk_pending_requests_requester_user_id_users",
                ),
                nullable=False,
            ),
            sa.Column("request_type", sa.String(length=40), nullable=False),
            sa.Column(
                "payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "target_staff_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "staff.id",
                    ondelete="SET NULL",
                    name="fk_pending_requests_target_staff_id_staff",
                ),
                nullable=True,
            ),
            sa.Column(
                "target_patient_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "patients.id",
                    ondelete="SET NULL",
                    name="fk_pending_requests_target_patient_id_patients",
                ),
                nullable=True,
            ),
            sa.Column("target_date", sa.Date(), nullable=True),
            sa.Column("scope", sa.String(length=16), nullable=True),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "approved_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "users.id",
                    ondelete="SET NULL",
                    name="fk_pending_requests_approved_by_users",
                ),
                nullable=True,
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "rejected_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "users.id",
                    ondelete="SET NULL",
                    name="fk_pending_requests_rejected_by_users",
                ),
                nullable=True,
            ),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column(
                "edited_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "ai_interpret_log_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "ai_interpret_logs.id",
                    ondelete="SET NULL",
                    name="fk_pending_requests_ai_interpret_log_id_ai_interpret_logs",
                ),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            # CHECK 制約 (enum 相当)
            sa.CheckConstraint(
                f"request_type IN ({_values_csv(_REQUEST_TYPE_VALUES)})",
                name="ck_pending_requests_request_type",
            ),
            sa.CheckConstraint(
                f"status IN ({_values_csv(_REQUEST_STATUS_VALUES)})",
                name="ck_pending_requests_status",
            ),
            sa.CheckConstraint(
                f"scope IS NULL OR scope IN ({_values_csv(_REQUEST_SCOPE_VALUES)})",
                name="ck_pending_requests_scope",
            ),
        )
    else:
        # SQLite (テスト) フォールバック: JSONB → JSON, UUID → String(36).
        op.create_table(
            "pending_requests",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column(
                "requester_user_id",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("request_type", sa.String(length=40), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column(
                "target_staff_id",
                sa.String(length=36),
                sa.ForeignKey("staff.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "target_patient_id",
                sa.String(length=36),
                sa.ForeignKey("patients.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("target_date", sa.Date(), nullable=True),
            sa.Column("scope", sa.String(length=16), nullable=True),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column(
                "approved_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "rejected_by",
                sa.String(length=36),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("edited_payload", sa.JSON(), nullable=True),
            sa.Column(
                "ai_interpret_log_id",
                sa.String(length=36),
                sa.ForeignKey("ai_interpret_logs.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.current_timestamp(),
                nullable=False,
            ),
        )

    # ------------------------------------------------------------------
    # 2) Indexes (§4.4)
    # ------------------------------------------------------------------
    op.create_index(
        "ix_pending_requests_status_created_at",
        "pending_requests",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_pending_requests_target_staff_status",
        "pending_requests",
        ["target_staff_id", "status"],
    )
    op.create_index(
        "ix_pending_requests_target_patient_status",
        "pending_requests",
        ["target_patient_id", "status"],
    )
    op.create_index(
        "ix_pending_requests_target_date_status",
        "pending_requests",
        ["target_date", "status"],
    )
    op.create_index(
        "ix_pending_requests_requester_user_id",
        "pending_requests",
        ["requester_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_requests_requester_user_id", table_name="pending_requests")
    op.drop_index("ix_pending_requests_target_date_status", table_name="pending_requests")
    op.drop_index("ix_pending_requests_target_patient_status", table_name="pending_requests")
    op.drop_index("ix_pending_requests_target_staff_status", table_name="pending_requests")
    op.drop_index("ix_pending_requests_status_created_at", table_name="pending_requests")
    op.drop_table("pending_requests")
