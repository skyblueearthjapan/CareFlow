"""Audit log for write-side operations.

Wave 4-F extension: this model originally captured before/after diffs for
hand-instrumented mutations, but never had any callers (audit pass found 0
references). The middleware-based capture in `app/middleware/audit.py` adds
HTTP-level columns (path / method / status / latency / IP / user-agent /
redacted request body) so every state-changing API call is recorded with no
explicit per-handler instrumentation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Legacy "domain action" columns — kept for forward compat with hand-coded
    # callers (e.g. password reset). Middleware fills them with HTTP method +
    # URL path so the table has a uniform projection.
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_table: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)

    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ---- Wave 4-F middleware columns ----
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_target", "target_table", "target_id"),
        # Quick filter on recent activity.
        Index(
            "ix_audit_logs_recent",
            text("created_at DESC"),
        ),
        Index("ix_audit_logs_method_path", "method", "path"),
        Index("ix_audit_logs_status_code", "status_code"),
    )
