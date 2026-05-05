"""Gemini API client wrapper (Phase 5-1 Wave 4-B / D4 Phase E).

Thin wrapper around `google-generativeai`'s `GenerativeModel`. The actual SDK
import is lazy / optional so test environments without the package (or
without a configured `GEMINI_API_KEY`) can still import this module — the
client raises `GeminiUnavailableError` only when `interpret()` is actually
called without an API key.

Cost is approximated locally via a static $/1K-token table — Gemini does not
report cost in the response so we estimate from input/output tokens to stay
within budget.

TODO (W5-F follow-up / out of scope for this sprint):
    Migrate from `google-generativeai` (deprecated upstream) to the new
    `google-genai` SDK. The new SDK changes the GenerativeModel construction
    pattern and the response shape (response.text → response.candidates[]),
    so the migration touches `_invoke()` end-to-end and warrants its own
    sprint with a fresh test pass. Tracking issue: TBD.

Default model rationale (W5-F, 2026-05):
    `_DEFAULT_MODEL` was bumped from `gemini-2.0-flash` to `gemini-2.5-flash`
    after W4-B verification surfaced that gemini-2.0-flash returns
    HTTP 404 ("model not found / not supported for generateContent") for
    *new* API users on certain regional billing accounts. gemini-2.5-flash
    is GA and accepts generateContent universally as of 2026-05.
    Operators on legacy projects can still pin `GEMINI_MODEL=gemini-2.0-flash`
    via env if pricing matters more than availability.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)


# Approx pricing per 1M tokens (USD). Source: Google AI pricing page,
# 2026-05 snapshot — update whenever Google publishes new tiers. The
# table is keyed by *model id* so switching `GEMINI_MODEL` env vars
# automatically picks the right cost line; unknown models fall back to
# the `_DEFAULT_MODEL` entry (logged at WARNING in `estimate_cost_usd`).
#
# Reference (USD / 1M tokens):
#   gemini-2.5-flash      input $0.30  output $2.50   (default, GA, 2026-05)
#   gemini-2.0-flash      input $0.10  output $0.40   (cheaper but 404 on
#                                                      some new accounts)
#   gemini-1.5-flash      input $0.075 output $0.30   (RETIRED on v1beta)
#   gemini-1.5-pro        input $1.25  output $5.00
_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
}

# Per-request request/response timeout (s).
_DEFAULT_TIMEOUT_S = 15.0
# 2026-05 W5-F bump: gemini-2.0-flash returns 404 generateContent for new
# users on some billing accounts; gemini-2.5-flash is GA and works
# universally. See module docstring for the full rationale.
_DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiError(RuntimeError):
    """Base for all Gemini-related failures (so callers can catch a single type)."""


class GeminiUnavailableError(GeminiError):
    """Raised when the Gemini SDK is missing or no API key is configured."""


class GeminiAPIKeyMissing(GeminiUnavailableError):  # noqa: N818
    """Alias for missing-API-key — distinct from SDK-missing semantically.

    Naming kept without ``Error`` suffix on purpose so callers grep for
    ``APIKeyMissing`` consistently with other auth-related sentinels.
    """


class GeminiInvalidResponseError(GeminiError):
    """Raised when Gemini returns content that is not parseable JSON."""


class GeminiRateLimitError(GeminiError):
    """Raised when Gemini rejects the request with a quota / 429 error."""


class GeminiQuotaExceeded(GeminiRateLimitError):  # noqa: N818
    """Specific subclass for daily/per-minute quota exhaustion (alias).

    Suffix omitted intentionally to mirror Google's own ``QuotaExceeded``
    response code naming.
    """


class GeminiModelNotFound(GeminiError):  # noqa: N818
    """Raised on 404 — the configured model is unknown / retired on this API
    version. Operators should update GEMINI_MODEL env.

    Naming follows Google's ``NotFound`` status convention; the ``Error``
    suffix is omitted on purpose.
    """


@dataclass
class InterpretResult:
    """Structured result from `GeminiClient.interpret()`.

    `interpreted` is the parsed JSON (always a dict; `actions` array lives
    inside under design 09's schema). `raw_response` is the full text the
    model returned (kept for the AI audit log + UI debugging).
    """

    interpreted: dict[str, Any]
    confidence: float
    raw_response: str
    model: str
    latency_ms: int
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# --- Prompt strategies (per context_type) ---------------------------------

_BASE_PROMPT = (
    "あなたは訪問看護スケジュール管理システム CareFlow のAIアシスタントです。\n"
    "ユーザーの自然言語入力を、以下の JSON スキーマに厳密に従って構造化してください。\n"
    '解釈不能な場合は action_type を "unknown" にし confidence を 0 にしてください。\n'
)

_OUTPUT_SCHEMA_HINT = (
    "【出力スキーマ】\n"
    "{\n"
    '  "actions": [\n'
    "    {\n"
    '      "action_type": "<context-specific>",\n'
    '      "confidence": 0.0-1.0,\n'
    '      "fields": { ... }\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "複数アクションが含まれる場合は actions を複数返してください。\n"
    "信頼度は: 完全一致 0.95+、推測 0.7-0.9、曖昧 <0.7。\n"
    "応答は必ず単一の JSON オブジェクトのみで、Markdown コードフェンスは禁止です。\n"
)


def build_prompt(context_type: str, user_text: str, context: dict[str, Any]) -> str:
    """Render the system prompt for the requested context_type.

    `context` may carry `today`, `iso_week`, `staff_list`, `patient_list`
    pre-rendered from the route handler — the prompt builder treats them as
    optional hints and falls back to generic placeholders when absent.
    """
    lines: list[str] = [_BASE_PROMPT]

    today = context.get("today")
    iso_week = context.get("iso_week")
    weekday = context.get("weekday")
    if today:
        suffix = ""
        if weekday and iso_week:
            suffix = f" ({weekday}・第{iso_week}週)"
        elif iso_week:
            suffix = f" (第{iso_week}週)"
        lines.append(f"【今日の日付】{today}{suffix}")

    staff_list = context.get("staff_list") or []
    if staff_list:
        rendered = "\n".join(f"- {s}" for s in staff_list[:200])
        lines.append(f"【利用可能なスタッフ一覧】\n{rendered}")

    patient_list = context.get("patient_list") or []
    if patient_list:
        rendered = "\n".join(f"- {p}" for p in patient_list[:200])
        lines.append(f"【利用可能な患者一覧】\n{rendered}")

    if context_type == "patient_create":
        lines.append(
            "【コンテキスト】患者新規登録。氏名・住所・電話・主要事業所・"
            "曜日別訪問希望を抽出してください。\n"
            "action_type は 'patient_create' を使用。"
        )
    elif context_type == "event_create":
        lines.append(
            "【コンテキスト】スタッフのイベント登録 (会議・研修・外勤など)。\n"
            "action_type は 'staff_event' を使用。"
            " fields: staff_id, weekday, time_start, time_end, reason"
        )
    elif context_type == "override_create":
        lines.append(
            "【コンテキスト】その週だけのスタッフ休み登録 (一日 / 時間帯)。\n"
            "action_type は 'staff_weekly_override' を使用。"
            " fields: staff_id, iso_week, weekday, time_start?, time_end?, reason?"
        )
    else:
        lines.append(
            "【コンテキスト】汎用。design 09 の action_type 一覧から最適なものを"
            "選んでください ('staff_weekly_override' | 'staff_event' |"
            " 'visit_cancel' | 'visit_postpone' | 'visit_add' | 'unknown')。"
        )

    lines.append(_OUTPUT_SCHEMA_HINT)
    lines.append(f"【ユーザー入力】{user_text}")
    return "\n\n".join(lines)


def estimate_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    model: str = _DEFAULT_MODEL,
) -> float:
    """Approximate USD cost using `_PRICING_USD_PER_1M` (input, output)."""
    pricing = _PRICING_USD_PER_1M.get(model)
    if pricing is None:
        logger.warning(
            "estimate_cost_usd: unknown model %r — falling back to %s pricing",
            model,
            _DEFAULT_MODEL,
        )
        pricing = _PRICING_USD_PER_1M[_DEFAULT_MODEL]
    in_price, out_price = pricing
    return round(
        (prompt_tokens / 1_000_000.0) * in_price + (completion_tokens / 1_000_000.0) * out_price,
        6,
    )


def _parse_json_payload(text: str) -> dict[str, Any]:
    """Best-effort JSON parsing — strips Markdown fences if Gemini ignores
    the no-fence instruction."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove the first fence line and the trailing ``` (with or without
        # language tag).
        first_nl = stripped.find("\n")
        if first_nl != -1:
            stripped = stripped[first_nl + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise GeminiInvalidResponseError(f"Gemini response is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise GeminiInvalidResponseError("Gemini response root must be a JSON object")
    return loaded


def _extract_confidence(payload: dict[str, Any]) -> float:
    """Pull the highest action confidence out of the payload (0.0 if none)."""
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        return 0.0
    best = 0.0
    for a in actions:
        if isinstance(a, dict):
            try:
                v = float(a.get("confidence", 0))
            except (TypeError, ValueError):
                v = 0.0
            best = max(best, v)
    return max(0.0, min(1.0, best))


class GeminiClient:
    """Lightweight wrapper around `google-generativeai`.

    Designed so tests can monkeypatch `_invoke()` without ever importing the
    SDK; production code paths import it lazily inside `_invoke()`.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        settings = get_settings()
        # Explicit ctor arg > env (settings.gemini_model) > module default.
        self.model = model or getattr(settings, "gemini_model", None) or _DEFAULT_MODEL
        self.timeout_s = timeout_s
        self._api_key = api_key if api_key is not None else settings.gemini_api_key

    def _invoke(self, prompt: str) -> tuple[str, int, int]:
        """Call Gemini and return (text, prompt_tokens, completion_tokens).

        Tests override this method via monkeypatch.
        """
        if not self._api_key:
            raise GeminiAPIKeyMissing("GEMINI_API_KEY is not set — cannot reach Gemini API")
        try:
            import google.generativeai as genai  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised only in env w/o SDK
            raise GeminiUnavailableError(
                "google-generativeai is not installed in this environment"
            ) from exc

        try:
            genai.configure(api_key=self._api_key)
            model = genai.GenerativeModel(self.model)
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "response_mime_type": "application/json",
                },
                request_options={"timeout": self.timeout_s},
            )
        except Exception as exc:  # pragma: no cover - SDK errors are network-dep
            msg = str(exc).lower()
            # 404 surfaces as "is not found for API version" / "is not
            # supported for generateContent" — flag those distinctly so
            # the API layer can return 503 rather than a misleading 429.
            if (
                "404" in msg
                or "is not found" in msg
                or "not supported for generatecontent" in msg
                or "model not found" in msg
            ):
                raise GeminiModelNotFound(
                    f"Gemini model {self.model!r} not found / unsupported "
                    f"on this API version: {exc}"
                ) from exc
            if "rate" in msg or "quota" in msg or "429" in msg:
                raise GeminiQuotaExceeded(str(exc)) from exc
            # Anything else: bubble as generic GeminiError so the API
            # layer maps it to 502 (upstream broken) rather than 422
            # (which implies the response body was the problem).
            raise GeminiError(str(exc)) from exc

        text = getattr(response, "text", None)
        if not text:
            # Fallback for SDK versions where .text raises on empty candidates.
            try:
                text = response.candidates[0].content.parts[0].text  # type: ignore[index]
            except Exception as exc:  # pragma: no cover
                raise GeminiInvalidResponseError("Gemini returned no candidate text") from exc
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        completion_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        return str(text), prompt_tokens, completion_tokens

    def interpret(
        self,
        *,
        prompt: str,
        context_type: str,
        context: dict[str, Any] | None = None,
    ) -> InterpretResult:
        """Build a system prompt, invoke Gemini, parse JSON, return result.

        `prompt` is the user's natural-language input. `context_type` selects
        the prompt strategy; `context` carries optional hints (today, staff
        list, etc.) that the prompt builder injects.
        """
        rendered = build_prompt(context_type, prompt, context or {})
        started = time.perf_counter()
        text, prompt_tokens, completion_tokens = self._invoke(rendered)
        latency_ms = int((time.perf_counter() - started) * 1000)

        payload = _parse_json_payload(text)
        confidence = _extract_confidence(payload)
        cost_usd = estimate_cost_usd(prompt_tokens, completion_tokens, self.model)

        return InterpretResult(
            interpreted=payload,
            confidence=confidence,
            raw_response=text,
            model=self.model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            extra={"rendered_prompt": rendered},
        )


def get_gemini_client() -> GeminiClient:
    """FastAPI dependency-friendly factory (one instance per call is fine —
    `genai.configure()` is cheap and idempotent)."""
    return GeminiClient()
