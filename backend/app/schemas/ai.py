"""Pydantic schemas for the AI interpret endpoint (D4 Phase E).

The wire shape mirrors `docs/design/09-global-ai-input.md` § 9-9 / 9-12 — the
`interpreted` payload is the structured JSON returned by Gemini and almost
always contains an `actions` array (see design 09 § 9-7 for multi-action).

Note: the underlying `ai_interpret_logs` table only has prompt/response/
model/latency_ms/user_id columns. The `context_type` and `cost_usd` fields
needed by D4 Phase E are persisted inside the JSONB `response` column under
the `_meta` wrapper key so we don't need a fresh alembic migration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ContextType = Literal[
    "patient_create", "event_create", "override_create", "general"
]


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000, description="自然言語入力")
    context_type: ContextType = Field(default="general")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "追加コンテキスト (任意): today, iso_week, weekday, staff_list,"
            " patient_list 等を任意で渡せます。サーバ側でも自動補完します。"
        ),
    )


class InterpretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interpreted: dict[str, Any] = Field(
        description="Gemini が返した構造化 JSON。通常は actions[] を含む"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="actions の最高信頼度")
    raw_response: str = Field(description="Gemini の生テキスト (デバッグ用)")
    log_id: UUID = Field(description="AiInterpretLog.id (監査ログ参照用)")
    model: str
    latency_ms: int
    cost_usd: float = Field(ge=0.0)
    context_type: ContextType


class AiLogRead(BaseModel):
    """Enriched read schema that surfaces context_type / cost from the JSONB
    `response._meta` wrapper for the integrations / AI logs page."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    prompt: str
    response: dict[str, Any] = Field(default_factory=dict)
    model: str
    latency_ms: int
    user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    context_type: str | None = None
    cost_usd: float | None = None
    confidence: float | None = None
