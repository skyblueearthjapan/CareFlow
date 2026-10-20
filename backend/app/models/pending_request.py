"""PendingRequest (申請履歴) — v2 W2-BE5.

設計仕様書 v0.9 §3.5 / §4.4 に対応する申請履歴テーブル。
業務上の承認ワークフロー記録 (`docs/plans/v2-allocation-redesign.md` §4.4)。

JSONB クロスダイアレクト方針:
  本番は PostgreSQL JSONB、テストは SQLite の JSON にフォールバックする。
  ``patient.py`` と同じ ``with_variant`` パターンを踏襲する。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Cross-dialect JSONB: PostgreSQL gets native JSONB, SQLite (tests) gets JSON.
# patient.py / visit.py と同じ戦略。
JSONBish = JSONB().with_variant(JSON(), "sqlite")


# request_type / status / scope の値域は v2 enums (`app/schemas/v2/enums.py`) と
# 完全一致させる。本テーブルは ``String`` 列で保持する。
# request_type の CHECK は mig0099 でアプリの 12 種に貼り直し、試験の DB (create_all) でも
# 同じ CHECK が付くようモデルにも持つ (``ck_pending_requests_request_type``・設計 §8-1)。
# status / scope の CHECK は migration (Postgres) 側だけ。
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
    "staff_status_update",
    "patient_status_update",
    "patient_visit_add",
)
_REQUEST_STATUS_VALUES: tuple[str, ...] = ("pending", "approved", "rejected")
_REQUEST_SCOPE_VALUES: tuple[str, ...] = ("one_time", "permanent")


class PendingRequest(Base, TimestampMixin):
    """業務申請 (§4.4).

    ``payload`` JSONB に request_type ごとの依頼内容を構造化して格納する。
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

    # ----- 業務反映完了タイムスタンプ (W7-BE3 冪等性) -----
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="業務反映完了タイムスタンプ (W7-BE3 冪等性). NULL = 未反映",
    )

    # ----- 編集して承認の場合 -----
    edited_payload: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)

    __table_args__ = (
        # 命名規約で ``ck_pending_requests_request_type`` になる (migration と同じ名前)。
        CheckConstraint(
            "request_type IN (" + ", ".join(f"'{v}'" for v in _REQUEST_TYPE_VALUES) + ")",
            name="request_type",
        ),
        Index("ix_pending_requests_status_created_at", "status", "created_at"),
        Index("ix_pending_requests_target_staff_status", "target_staff_id", "status"),
        Index("ix_pending_requests_target_patient_status", "target_patient_id", "status"),
        Index("ix_pending_requests_target_date_status", "target_date", "status"),
        # 同じ職員・同じ日の申請中の休みの申請は 1 件だけ (mig 0099・休み設計 §2-3)。
        Index(
            "uq_pending_requests_staff_off_pending",
            "target_staff_id",
            "target_date",
            unique=True,
            postgresql_where=text("request_type = 'staff_off' AND status = 'pending'"),
            sqlite_where=text("request_type = 'staff_off' AND status = 'pending'"),
        ),
    )
