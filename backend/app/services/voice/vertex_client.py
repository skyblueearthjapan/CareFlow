"""Async Vertex AI (Gemini) client for visit voice transcription + summary.

設計書 `docs/plans/visit-voice-record-design-2026-09-17.md` §1-i / §10-1 / §10-4。

作法は `app/services/kaipoke_client.py` に揃える:
  * 専用ラッパ (ルータ/ジョブに生の httpx を漏らさない)
  * カスタム例外 `VoiceAiError` (kind で分類)
  * `set_test_client()` シーム

**リトライはここでは行わない**: 再実行の判断は `services/voice/jobs.py` の
`_call_with_retry` に一本化する (2 箇所で数えると合計回数が掛け算になり、
240 秒 x 2 x 2 = 16 分 のジョブが生まれて stale 判定と食い違う)。この層は
1 リクエスト = 1 回だけ投げ、失敗を `VoiceAiError(kind)` に翻訳する。

実測 (2026-09-17): 東京 `asia-northeast1` で `gemini-2.5-flash` が 200。
音声は `inlineData` (base64) で 20 MB まで。`responseMimeType=application/json`
＋ `thinkingConfig.thinkingBudget=0`。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC
from decimal import Decimal
from typing import Any

import httpx

from app.services.voice import prompts
from app.services.voice.pricing import estimate_cost_usd, output_tokens, split_prompt_tokens

try:  # pragma: no cover - import guard
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover - google-auth 未導入の環境
    GoogleAuthRequest = None  # type: ignore[assignment]
    service_account = None  # type: ignore[assignment]

DEFAULT_LOCATION = "asia-northeast1"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 240.0
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_TEMPERATURE = 0.2

#: inlineData で送れる音声の上限 (これを超えたら将来 GCS 経由にする)。
MAX_INLINE_AUDIO_BYTES = 20 * 1024 * 1024

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
#: トークンの期限切れ手前で先に更新する余裕 (秒)。
_TOKEN_SKEW_SECONDS = 120.0

#: アクセストークンのキャッシュ (credentials_path -> (token, expires_at))。
#:
#: **モジュールレベル** に置くのが要点: クライアントは録音 1 件ごとに
#: ``get_voice_client()`` で作り直され、``aclose()`` で捨てられる。インスタンス
#: 属性に持つとキャッシュは一度も効かず、録音のたびに SA キーの読み込みと
#: トークン発行 (ネットワーク往復) が走っていた。プロセス内で共有する。
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def clear_token_cache() -> None:
    """アクセストークンのキャッシュを捨てる (認証失敗時 / テスト用)。"""
    _TOKEN_CACHE.clear()


#: VoiceAiError.kind に使う値。
ERROR_KINDS = ("auth", "timeout", "http", "parse", "too_large")


class VoiceAiError(RuntimeError):
    """Raised for every failure of the voice AI call.

    `kind` is one of: auth | timeout | http | parse | too_large.
    """

    def __init__(
        self,
        kind: str,
        message: str | None = None,
        *,
        status_code: int | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message or f"voice ai error: {kind}")
        self.kind = kind
        self.status_code = status_code
        self.body = body


@dataclass
class VoiceAiResult:
    """One successful transcription + summary."""

    transcript: str
    transcript_segments: list[dict[str, Any]]
    summary: dict[str, Any]
    summary_text: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    model: str
    prompt_version: str
    raw_usage: dict[str, Any] = field(default_factory=dict)


def render_summary_text(summary: dict[str, Any] | None) -> str:
    """Render the summary JSON as a human-readable bullet list (記録の貼り付け用)."""
    if not summary:
        return ""
    lines: list[str] = []

    def _bullets(title: str, value: Any) -> None:
        if isinstance(value, str):
            items = [value] if value.strip() else []
        elif isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
        else:
            items = []
        if not items:
            return
        lines.append(f"【{title}】")
        lines.extend(f"・{item}" for item in items)

    _bullets("主訴・様子", summary.get("主訴・様子"))

    vitals = summary.get("バイタル")
    if isinstance(vitals, dict):
        measured = [
            f"・{key}: {str(vitals.get(key)).strip()}"
            for key in prompts.VITAL_KEYS
            if str(vitals.get(key) or "").strip()
        ]
        # スキーマ外の項目も落とさない。
        measured += [
            f"・{key}: {str(value).strip()}"
            for key, value in vitals.items()
            if key not in prompts.VITAL_KEYS and str(value or "").strip()
        ]
        if measured:
            lines.append("【バイタル】")
            lines.extend(measured)

    _bullets("処置・ケア", summary.get("処置・ケア"))
    _bullets("申し送り", summary.get("申し送り"))

    next_visit = str(summary.get("次回") or "").strip()
    if next_visit:
        lines.append("【次回】")
        lines.append(f"・{next_visit}")

    free = str(summary.get("free") or "").strip()
    if free:
        lines.append("【備考】")
        lines.extend(f"・{line.strip()}" for line in free.splitlines() if line.strip())

    return "\n".join(lines)


class VertexVoiceClient:
    """Thin async wrapper around Vertex AI `generateContent` for audio."""

    def __init__(
        self,
        *,
        project: str,
        location: str = DEFAULT_LOCATION,
        model: str = DEFAULT_MODEL,
        credentials_path: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._project = project
        self._location = location or DEFAULT_LOCATION
        self._model = model or DEFAULT_MODEL
        self._credentials_path = credentials_path
        self._timeout = float(timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout)

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1"
            f"/projects/{self._project}/locations/{self._location}"
            f"/publishers/google/models/{self._model}:generateContent"
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> VertexVoiceClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    # --- Public surface ---------------------------------------------------

    async def transcribe_and_summarize(
        self,
        audio_bytes: bytes,
        mime_type: str,
        prompt: str,
    ) -> VoiceAiResult:
        """Send one audio file + prompt, return the parsed transcript/summary."""
        if len(audio_bytes) > MAX_INLINE_AUDIO_BYTES:
            raise VoiceAiError(
                "too_large",
                f"audio is {len(audio_bytes)} bytes (inline limit {MAX_INLINE_AUDIO_BYTES})",
            )

        body = self._build_body(audio_bytes, mime_type, prompt)
        payload = await self._post(body)
        return self._parse_result(payload)

    # --- Internals --------------------------------------------------------

    def _build_body(self, audio_bytes: bytes, mime_type: str, prompt: str) -> dict[str, Any]:
        return {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": DEFAULT_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseSchema": prompts.response_schema(),
                "thinkingConfig": {"thinkingBudget": 0},
                "temperature": DEFAULT_TEMPERATURE,
            },
        }

    def _credentials_file(self) -> str:
        """Resolve the SA key path (also the cache key). Raises on missing."""
        path = self._credentials_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not path:
            raise VoiceAiError("auth", "GOOGLE_APPLICATION_CREDENTIALS is not configured")
        return path

    @staticmethod
    def _load_token(path: str) -> tuple[str, float]:
        """Blocking credential load/refresh (call through asyncio.to_thread)."""
        if service_account is None or GoogleAuthRequest is None:
            raise VoiceAiError("auth", "google-auth is not installed")
        try:
            creds = service_account.Credentials.from_service_account_file(path, scopes=_SCOPES)
            creds.refresh(GoogleAuthRequest())
        except VoiceAiError:
            raise
        except Exception as exc:  # noqa: BLE001 - どの失敗も auth として扱う
            raise VoiceAiError("auth", f"failed to obtain access token: {exc}") from exc
        token = getattr(creds, "token", None)
        if not token:
            raise VoiceAiError("auth", "service account returned an empty token")
        expiry = getattr(creds, "expiry", None)
        expires_at = time.time() + 3000.0
        if expiry is not None:
            try:
                # google-auth の expiry は tz を持たない UTC。naive のまま
                # timestamp() を呼ぶとローカル時刻扱いになり、JST では 9 時間
                # 過去に見えてキャッシュが毎回失効する。
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                expires_at = expiry.timestamp()
            except (AttributeError, TypeError, ValueError, OSError):
                pass
        return token, expires_at

    async def _access_token(self) -> str:
        """Cached (process-wide) access token for this SA key."""
        path = self._credentials_file()
        cached = _TOKEN_CACHE.get(path)
        if cached is not None and time.time() < cached[1] - _TOKEN_SKEW_SECONDS:
            return cached[0]
        token, expires_at = await asyncio.to_thread(self._load_token, path)
        _TOKEN_CACHE[path] = (token, expires_at)
        return token

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """One request, one attempt. Retries live in ``voice/jobs.py``."""
        token = await self._access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = await self._client.post(self.endpoint, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise VoiceAiError("timeout", str(exc) or "vertex ai request timed out") from exc
        except httpx.RequestError as exc:
            raise VoiceAiError("http", f"network error: {exc}") from exc

        if resp.status_code in (401, 403):
            # 期限切れトークンの取り違えを避けるため、この SA キーの
            # キャッシュだけ捨てる (次の呼び出しで取り直す)。
            _TOKEN_CACHE.pop(self._credentials_file(), None)
            raise VoiceAiError(
                "auth",
                f"vertex ai rejected the credentials ({resp.status_code})",
                status_code=resp.status_code,
                body=_safe_text(resp),
            )
        if resp.status_code >= 400:
            raise VoiceAiError(
                "http",
                f"vertex ai error {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_text(resp),
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise VoiceAiError("parse", "vertex ai response was not JSON") from exc
        if not isinstance(payload, dict):
            raise VoiceAiError("parse", "vertex ai response was not a JSON object")
        return payload

    def _parse_result(self, payload: dict[str, Any]) -> VoiceAiResult:
        text = _extract_text(payload)
        if not text.strip():
            raise VoiceAiError("parse", "vertex ai returned no text part", body=payload)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise VoiceAiError("parse", f"model output was not JSON: {exc}", body=text) from exc
        if not isinstance(data, dict):
            raise VoiceAiError("parse", "model output was not a JSON object", body=text)

        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        segments_raw = data.get("transcript_segments")
        segments: list[dict[str, Any]] = []
        if isinstance(segments_raw, list):
            segments = [s for s in segments_raw if isinstance(s, dict)]

        usage = payload.get("usageMetadata") or {}
        if not isinstance(usage, dict):
            usage = {}
        audio_in, text_in = split_prompt_tokens(usage)

        return VoiceAiResult(
            transcript=str(data.get("transcript") or ""),
            transcript_segments=segments,
            summary=summary,
            summary_text=render_summary_text(summary),
            tokens_in=audio_in + text_in,
            tokens_out=output_tokens(usage),
            cost_usd=estimate_cost_usd(self._model, usage),
            model=self._model,
            prompt_version=prompts.PROMPT_VERSION,
            raw_usage=usage,
        )


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.text[:2000]
    except Exception:  # noqa: BLE001 - エラー本文の取得失敗で例外を潰さない
        return ""


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise VoiceAiError("parse", "vertex ai response had no candidates", body=payload)
    first = candidates[0]
    content = first.get("content") if isinstance(first, dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise VoiceAiError("parse", "vertex ai candidate had no parts", body=payload)
    return "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))


# --- Module-level factory & override hook --------------------------------

_OVERRIDE: VertexVoiceClient | None = None


def set_test_client(client: VertexVoiceClient | None) -> None:
    """Test seam: inject a stub client without monkeypatching the constructor."""
    global _OVERRIDE
    _OVERRIDE = client


def get_voice_client(settings: Any) -> VertexVoiceClient | None:
    """Factory: build the client from settings, or None when disabled.

    `VOICE_AI_PROVIDER == 'none'` (受領のみ・AI 処理しない) では None を返す。
    設定フィールドは §10-1。BE-1 が `app/core/config.py` に追加するまでは
    `getattr` の既定値で動く。
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    # 正規化は jobs.py と同じ規則 (`.strip().lower()`)。`VOICE_AI_PROVIDER=" none"`
    # のような env の混入で片方だけ判定が変わると、ジョブは走るのに
    # クライアントが None という噛み合わせ事故になる。
    provider = str(getattr(settings, "voice_ai_provider", "vertex") or "vertex").strip().lower()
    if provider != "vertex":
        return None
    return VertexVoiceClient(
        project=str(getattr(settings, "vertex_project_id", "") or ""),
        location=str(getattr(settings, "vertex_location", DEFAULT_LOCATION) or DEFAULT_LOCATION),
        model=str(getattr(settings, "vertex_model_transcribe", DEFAULT_MODEL) or DEFAULT_MODEL),
        credentials_path=getattr(settings, "google_application_credentials", None)
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        timeout_seconds=float(
            getattr(settings, "voice_ai_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            or DEFAULT_TIMEOUT_SECONDS
        ),
    )
