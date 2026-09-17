"""Vertex AI (Gemini) token pricing for the visit voice recording feature.

Prices are USD per 1,000,000 tokens, as published 2026-09 (design doc
`docs/plans/visit-voice-record-design-2026-09-17.md` §1-e / §10-4).

Modalities are billed differently on the input side: audio input is far more
expensive than text input, so the cost estimate uses `promptTokensDetails`
(per-modality breakdown returned by Vertex) whenever it is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# USD per 1M tokens.
PRICE_PER_1M: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"audio_in": 1.00, "text_in": 0.30, "out": 2.50},
    "gemini-3.5-flash-lite": {"audio_in": 0.30, "text_in": 0.30, "out": 2.50},
    # 3.8 Flash は期間価格 (promotional)。正価に戻ったら差し替える。
    "gemini-3.8-flash": {"audio_in": 0.75, "text_in": 0.75, "out": 3.75},
}

#: 未知のモデル名を渡された場合に使う価格表 (東京リージョンの既定モデル)。
FALLBACK_MODEL = "gemini-2.5-flash"

#: 費用の丸め桁 (USD)。1 件 ≈ $0.0016 なので 6 桁あれば足りる。
_QUANT = Decimal("0.000001")

#: Vertex が返す modality 文字列 → 価格キー。
_AUDIO_MODALITIES = {"AUDIO"}


def price_table(model: str) -> dict[str, float]:
    """Return the price table for `model` (falling back to the default model)."""
    return PRICE_PER_1M.get(model, PRICE_PER_1M[FALLBACK_MODEL])


def _token_count(entry: Any) -> int:
    if not isinstance(entry, Mapping):
        return 0
    value = entry.get("tokenCount", entry.get("token_count", 0))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def split_prompt_tokens(usage_metadata: Mapping[str, Any] | None) -> tuple[int, int]:
    """Split prompt tokens into (audio_tokens, text_tokens).

    Uses `promptTokensDetails` when available. When the breakdown is missing
    (older API surface / truncated response) the whole `promptTokenCount` is
    billed at the **audio** rate: every call this feature makes carries one
    audio part, and audio is the more expensive modality, so guessing "text"
    would under-report the bill. An estimate that is too high is noticed;
    one that is too low is not.
    """
    usage = usage_metadata or {}
    details = usage.get("promptTokensDetails") or usage.get("prompt_tokens_details") or []
    audio = 0
    text = 0
    if isinstance(details, list) and details:
        for entry in details:
            if not isinstance(entry, Mapping):
                continue
            modality = str(entry.get("modality", "")).upper()
            count = _token_count(entry)
            if modality in _AUDIO_MODALITIES:
                audio += count
            else:
                text += count
    if audio == 0 and text == 0:
        audio = _int(usage.get("promptTokenCount", usage.get("prompt_token_count")))
    return audio, text


def output_tokens(usage_metadata: Mapping[str, Any] | None) -> int:
    """Total billable output tokens (candidates + thoughts, when reported)."""
    usage = usage_metadata or {}
    total = _int(usage.get("candidatesTokenCount", usage.get("candidates_token_count")))
    total += _int(usage.get("thoughtsTokenCount", usage.get("thoughts_token_count")))
    return total


def estimate_cost_usd(model: str, usage_metadata: Mapping[str, Any] | None) -> Decimal:
    """Estimate the USD cost of one generateContent call."""
    prices = price_table(model)
    audio_in, text_in = split_prompt_tokens(usage_metadata)
    out = output_tokens(usage_metadata)

    million = Decimal(1_000_000)
    cost = (
        Decimal(audio_in) * Decimal(str(prices["audio_in"]))
        + Decimal(text_in) * Decimal(str(prices["text_in"]))
        + Decimal(out) * Decimal(str(prices["out"]))
    ) / million
    return cost.quantize(_QUANT, rounding=ROUND_HALF_UP)
