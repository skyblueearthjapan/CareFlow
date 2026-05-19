"""LLM (Gemini etc.) prompt/response audit log (Phase 5-1, P-06).

Stores the rendered prompt, raw response JSON, model id, latency, and the
user who triggered the call so we can replay / investigate later.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AiInterpretLog(Base, TimestampMixin):
    __tablename__ = "ai_interpret_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_ai_interpret_logs_created_at", "created_at"),
        Index("ix_ai_interpret_logs_user_id", "user_id"),
    )
