"""PendingRequest (申請履歴) — v2 W2-BE5.

設計仕様書 v0.9 §3.5 / §4.4 に対応する申請履歴テーブル。

責務分離 (`docs/plans/v2-allocation-redesign.md` §4.4):
  - ``ai_interpret_logs``  : Gemini API 呼び出しのデバッグ記録 (既存)
  - ``pending_requests``   : 業務上の承認ワークフロー記録 (本テーブル, 新規)

監査要件 (§3.5.3):
  AI 経由の **即時反映** の場合も ``status="approved"`` で同時作成し、
  業務反映と同一トランザクションで処理する (冪等性確保)。

JSONB クロスダイアレクト方針:
  本番は PostgreSQL JSONB、テストは SQLite の JSON にフォールバックする。
  ``patient.py`` と同じ ``with_variant`` パターンを踏襲する。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Cross-dialect JSONB: PostgreSQL gets native JSONB, SQLite (tests) gets JSON.
# patient.py / visit.py と同じ戦略。
JSONBish = JSONB().with_variant(JSON(), "sqlite")


# request_type / status / scope の値域は v2 enums (`app/schemas/v2/enums.py`) と
# 完全一致させる。本テーブルは ``String`` 列で保持し、CHECK 制約は migration 側で
# 追加する (Postgres のみ、SQLite テストでは省略)。
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


class PendingRequest(Base, TimestampMixin):
    """業務申請 (§4.4).

    ``payload`` JSONB に request_type ごとの依頼内容を構造化して格納する。
    AI 解釈経由の場合は ``ai_interpret_log_id`` でソースログを参照可能。
    手動入力の場合は ``ai_interpret_log_id = NULL``。
    """

    __tablename__ = "pending_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ----- 申請者 / メタ -----
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ----- 業務種別 + 内容 -----
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONBish, nullable=False, default=dict)

    # ----- 影響対象 (filter index 用) -----
    target_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ----- スコープ (patient_reschedule 専用) -----
    scope: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ----- ステータス + 承認/却下メタ -----
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )

    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ----- 編集して承認の場合 -----
    edited_payload: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)

    # ----- AI 解釈ログ参照 -----
    ai_interpret_log_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ai_interpret_logs.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_pending_requests_status_created_at", "status", "created_at"),
        Index("ix_pending_requests_target_staff_status", "target_staff_id", "status"),
        Index("ix_pending_requests_target_patient_status", "target_patient_id", "status"),
        Index("ix_pending_requests_target_date_status", "target_date", "status"),
    )
