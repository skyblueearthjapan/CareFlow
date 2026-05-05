"""Pydantic schemas for the AI interpret endpoint (D4 Phase E + W2-BE6).

The wire shape mirrors `docs/design/09-global-ai-input.md` § 9-9 / 9-12 — the
`interpreted` payload is the structured JSON returned by Gemini and almost
always contains an `actions` array (see design 09 § 9-7 for multi-action).

Note: the underlying `ai_interpret_logs` table only has prompt/response/
model/latency_ms/user_id columns. The `context_type` and `cost_usd` fields
needed by D4 Phase E are persisted inside the JSONB `response` column under
the `_meta` wrapper key so we don't need a fresh alembic migration.

W2-BE6 (`docs/plans/v2-allocation-redesign.md` v0.9 §3.5 / §3.5.7):
context_type を 4 種から 10 種に拡張。`event_create` / `override_create` は
deprecated として受け付け、内部で `staff_event` / `staff_off` に変換する。
- 新規: `staff_create`, `staff_mentor`, `staff_event`, `staff_off`,
        `patient_cancel`, `patient_reschedule`, `patient_special_week`
- 互換: `event_create` → `staff_event`, `override_create` → `staff_off`
- レスポンスに `missing_fields: list[str] | None` を追加（不足情報補完モーダル用）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# v2 公式の context_type 10 種（`backend/app/schemas/v2/enums.py::AiContextType`
# と完全一致 / 順序は対応表 §9 に揃える）。
ContextType = Literal[
    # マスタ操作系
    "patient_create",
    "staff_create",
    # スタッフ予定系（v2 改名後）
    "staff_event",
    "staff_off",
    "staff_mentor",
    # 患者対応系
    "patient_cancel",
    "patient_reschedule",
    "patient_special_week",
    # フォールバック / 範囲外
    "general",
    "out_of_scope",
]

# 旧 context_type → 新 context_type の互換マップ
# (D4 Phase E の `event_create` / `override_create` は v2 で改名された)
LEGACY_CONTEXT_TYPE_MAP: dict[str, str] = {
    "event_create": "staff_event",
    "override_create": "staff_off",
}

# 受け入れる入力の Literal（公式 10 種 + 旧 2 種）。
# レスポンス側は正規化済みの公式 10 種のみを返す。
RequestContextType = Literal[
    "patient_create",
    "staff_create",
    "staff_event",
    "staff_off",
    "staff_mentor",
    "patient_cancel",
    "patient_reschedule",
    "patient_special_week",
    "general",
    "out_of_scope",
    # Deprecated aliases — 内部で変換される
    "event_create",
    "override_create",
]


def normalize_context_type(value: str) -> str:
    """旧 context_type を v2 公式名に正規化する。

    `event_create` → `staff_event`, `override_create` → `staff_off`。
    それ以外はそのまま返す。
    """
    return LEGACY_CONTEXT_TYPE_MAP.get(value, value)


class InterpretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000, description="自然言語入力")
    context_type: RequestContextType = Field(default="general")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "追加コンテキスト (任意): today, iso_week, weekday, staff_list,"
            " patient_list 等を任意で渡せます。サーバ側でも自動補完します。"
        ),
    )

    @field_validator("context_type", mode="after")
    @classmethod
    def _strip_legacy_alias(cls, v: str) -> str:
        # Pydantic Literal バリデート後に旧名→新名へ寄せる。
        # 受け取り段階では旧名も許可するが、内部処理は常に正規化済みで進める。
        return normalize_context_type(v)


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
    missing_fields: list[str] | None = Field(
        default=None,
        description=(
            "AI が必須情報の欠落を検出したフィールド名一覧。"
            "フロントの不足情報補完モーダル (W5-FE12) が利用する。"
            "欠落なしの場合は省略 / null。"
        ),
    )


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
