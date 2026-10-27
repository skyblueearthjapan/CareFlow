"""Tests for the Vertex AI voice client (no real network, no real credentials).

認証は `google.oauth2.service_account` をモンキーパッチし、HTTP は
`httpx.MockTransport` で差し替える。
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.services.voice import pricing, prompts, vertex_client
from app.services.voice.vertex_client import (
    MAX_INLINE_AUDIO_BYTES,
    VertexVoiceClient,
    VoiceAiError,
    render_summary_text,
)

# --- Fakes ----------------------------------------------------------------


class _FakeCredentials:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        self.refreshed = 0

    def refresh(self, _request: object) -> None:
        self.refreshed += 1
        self.token = "fake-access-token"


class _FakeServiceAccountModule:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []
        self.creds = _FakeCredentials()
        module = self

        class Credentials:  # noqa: N801 - google の API 名に合わせる
            @staticmethod
            def from_service_account_file(path: str, scopes: list[str] | None = None):
                module.calls.append((path, scopes))
                return module.creds

        self.Credentials = Credentials


@pytest.fixture(autouse=True)
def fake_google_auth(monkeypatch: pytest.MonkeyPatch) -> _FakeServiceAccountModule:
    fake = _FakeServiceAccountModule()
    monkeypatch.setattr(vertex_client, "service_account", fake)
    monkeypatch.setattr(vertex_client, "GoogleAuthRequest", lambda *a, **k: object())
    return fake


@pytest.fixture(autouse=True)
def clear_override() -> None:
    vertex_client.set_test_client(None)
    # トークンのキャッシュは **モジュールレベル** (クライアントは録音ごとに
    # 作り直されるため)。テスト間で持ち越すと refresh 回数が数えられない。
    vertex_client.clear_token_cache()
    yield
    vertex_client.set_test_client(None)
    vertex_client.clear_token_cache()


SAMPLE_MODEL_JSON = {
    "transcript": "看護師: 今日の体調はいかがですか。\n患者: 少しだるいです。",
    "transcript_segments": [
        {"speaker": "看護師", "start_sec": 0, "text": "今日の体調はいかがですか。"},
        {"speaker": "患者", "start_sec": 3.5, "text": "少しだるいです。"},
        "ゴミ行 (dict でないので落ちる)",
    ],
    "summary": {
        "主訴・様子": ["だるさの訴えあり", "食欲は普段どおり"],
        "バイタル": {"血圧": "132/78", "体温": "36.7", "脈拍": "", "SpO2": "98", "血糖": ""},
        "処置・ケア": ["褥瘡の包交を実施"],
        "申し送り": ["主治医へ脈拍測定の可否を確認"],
        "次回": "来週水曜 10 時",
        "free": "",
    },
}

SAMPLE_USAGE = {
    "promptTokenCount": 820,
    "candidatesTokenCount": 264,
    "totalTokenCount": 1084,
    "promptTokensDetails": [
        {"modality": "AUDIO", "tokenCount": 800},
        {"modality": "TEXT", "tokenCount": 20},
    ],
}


def _ok_payload(model_json: dict | str | None = None, usage: dict | None = None) -> dict:
    text = (
        model_json
        if isinstance(model_json, str)
        else json.dumps(
            model_json if model_json is not None else SAMPLE_MODEL_JSON, ensure_ascii=False
        )
    )
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
        "usageMetadata": usage if usage is not None else SAMPLE_USAGE,
    }


def _client(handler, *, model: str = "gemini-2.5-flash") -> VertexVoiceClient:
    transport = httpx.MockTransport(handler)
    return VertexVoiceClient(
        project="rakusuke-voice",
        location="asia-northeast1",
        model=model,
        credentials_path="C:/secrets/fake-sa.json",
        timeout_seconds=5.0,
        client=httpx.AsyncClient(transport=transport, timeout=5.0),
    )


# --- Happy path -----------------------------------------------------------


async def test_transcribe_lines_success() -> None:
    """① 音声 + 指示 → 行の JSON の文字 (読むのは transcribe.py)・上限は 65535・費用は音声の値段。"""
    seen: dict = {}
    lines = [{"start": 0, "end": 2.5, "speaker": "看護師", "text": "今日の体調はいかがですか。"}]

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type")
        seen["body"] = json.loads(request.content)
        payload = _ok_payload(json.dumps(lines, ensure_ascii=False))
        payload["candidates"][0]["finishReason"] = "STOP"
        return httpx.Response(200, json=payload)

    client = _client(handler)
    result = await client.transcribe_lines(b"\x00audio-bytes", "audio/webm", "PROMPT")
    await client.aclose()

    # エンドポイント (東京・publishers/google/models/{model}:generateContent)
    assert seen["url"] == (
        "https://asia-northeast1-aiplatform.googleapis.com/v1"
        "/projects/rakusuke-voice/locations/asia-northeast1"
        "/publishers/google/models/gemini-2.5-flash:generateContent"
    )
    assert seen["auth"] == "Bearer fake-access-token"
    assert seen["content_type"] == "application/json"

    body = seen["body"]
    parts = body["contents"][0]["parts"]
    assert parts[0]["text"] == "PROMPT"
    assert parts[1]["inlineData"]["mimeType"] == "audio/webm"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"\x00audio-bytes"
    gen = body["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["thinkingConfig"]["thinkingBudget"] == 0
    assert gen["temperature"] == 0.2
    assert gen["maxOutputTokens"] == 65535
    assert gen["responseSchema"] == prompts.transcript_schema()

    assert json.loads(result.text) == lines
    assert result.finish_reason == "STOP"
    assert result.model == "gemini-2.5-flash"
    assert result.tokens_in == 820
    assert result.tokens_out == 264
    assert result.raw_usage == SAMPLE_USAGE
    # 800 * 1.00 + 20 * 0.30 + 264 * 2.50 (per 1M)
    assert result.cost_usd == Decimal("0.001466")


async def test_transcribe_lines_reports_max_tokens_and_keeps_cut_text() -> None:
    """上限で切れた答えも投げずに返す (続きを頼むのは呼び出し側)。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        payload = _ok_payload('[{"start": 0, "end": 1, "speaker": "看護師", "text": "途')
        payload["candidates"][0]["finishReason"] = "MAX_TOKENS"
        return httpx.Response(200, json=payload)

    client = _client(handler)
    result = await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()
    assert result.finish_reason == "MAX_TOKENS"
    assert result.text.startswith('[{"start": 0')


async def test_generate_text_sends_text_only_and_bills_text() -> None:
    """② ③ 文字だけ: 音声を付けない・JSON に縛らない・内訳が無ければ文字の値段で数える。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_ok_payload(
                "下書きです", usage={"promptTokenCount": 1000, "candidatesTokenCount": 100}
            ),
        )

    client = _client(handler)
    result = await client.generate_text("直してください")
    await client.aclose()

    parts = seen["body"]["contents"][0]["parts"]
    assert parts == [{"text": "直してください"}]
    gen = seen["body"]["generationConfig"]
    assert "responseSchema" not in gen and "responseMimeType" not in gen
    assert gen["maxOutputTokens"] == 65535 and gen["temperature"] == 0.2
    assert gen["thinkingConfig"]["thinkingBudget"] == 0
    assert result.text == "下書きです"
    # 1000 * 0.30 + 100 * 2.50 (per 1M)
    assert result.cost_usd == Decimal("0.000550")


async def test_access_token_is_cached_across_calls(
    fake_google_auth: _FakeServiceAccountModule,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    client = _client(handler)
    await client.transcribe_lines(b"a", "audio/webm", "p")
    await client.transcribe_lines(b"b", "audio/webm", "p")
    await client.aclose()

    assert len(fake_google_auth.calls) == 1
    assert fake_google_auth.calls[0][0] == "C:/secrets/fake-sa.json"
    assert fake_google_auth.calls[0][1] == ["https://www.googleapis.com/auth/cloud-platform"]


async def test_access_token_is_cached_across_client_instances(
    fake_google_auth: _FakeServiceAccountModule,
) -> None:
    """**録音ごとに作り直されるクライアント** の間でもトークンを共有する.

    ジョブは 1 件ごとに ``get_voice_client()`` で新しいクライアントを作り
    ``aclose()`` で捨てる。キャッシュがインスタンス属性だった頃は一度も効かず、
    録音のたびに SA キーの読み込みとトークン発行が走っていた。
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    for _ in range(3):
        client = _client(handler)
        await client.transcribe_lines(b"a", "audio/webm", "p")
        await client.aclose()

    assert len(fake_google_auth.calls) == 1


# --- Failures -------------------------------------------------------------


async def test_5xx_raises_http_without_retrying_here() -> None:
    """**この層はやり直さない** (再実行は voice/jobs._call_with_retry の担当)。

    両方で数えると合計回数が掛け算になり、240 秒 x 2 x 2 のジョブが生まれて
    VOICE_JOB_STALE_MINUTES の見積もりと食い違う。
    """
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "unavailable"})

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "http"
    assert exc.value.status_code == 503
    assert calls["n"] == 1


async def test_429_surfaces_status_code_for_the_job_layer() -> None:
    """429 は ``status_code`` を載せて上げる (jobs が 5 秒待ちを選ぶ根拠)。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "http"
    assert exc.value.status_code == 429


async def test_timeout_raises_timeout_without_retrying_here() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("read timeout", request=request)

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "timeout"
    assert calls["n"] == 1


async def test_401_raises_auth_and_drops_cached_token(
    fake_google_auth: _FakeServiceAccountModule,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")

    assert exc.value.kind == "auth"
    # この SA キーのキャッシュだけ捨てる → 次の呼び出しは取り直す。
    with pytest.raises(VoiceAiError):
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()
    assert len(fake_google_auth.calls) == 2


async def test_non_json_http_body_raises_parse() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "parse"


async def test_model_text_is_returned_as_is() -> None:
    """答えの文字の読み取りはこの層ではしない (transcribe.py / summary.py の仕事)。"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload("これは JSON ではありません"))

    client = _client(handler)
    result = await client.generate_text("p")
    await client.aclose()

    assert result.text == "これは JSON ではありません"
    assert result.finish_reason is None


async def test_missing_candidates_raises_parse() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"usageMetadata": SAMPLE_USAGE})

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "parse"


async def test_audio_over_20mb_raises_too_large() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_ok_payload())

    client = _client(handler)
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"\x00" * (MAX_INLINE_AUDIO_BYTES + 1), "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "too_large"
    assert calls["n"] == 0  # HTTP に出る前に弾く


async def test_missing_credentials_path_raises_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    client = VertexVoiceClient(
        project="p",
        credentials_path=None,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(VoiceAiError) as exc:
        await client.transcribe_lines(b"audio", "audio/webm", "p")
    await client.aclose()

    assert exc.value.kind == "auth"


# --- Pricing --------------------------------------------------------------


def test_estimate_cost_uses_modality_breakdown() -> None:
    cost = pricing.estimate_cost_usd("gemini-2.5-flash", SAMPLE_USAGE)
    assert cost == Decimal("0.001466")


def test_estimate_cost_without_details_bills_prompt_as_audio() -> None:
    """modality の内訳が無いときは **音声単価** (高い方) で見積もる.

    この機能の呼び出しは必ず音声を含む。安い text 単価で数えると請求より
    小さい見積もりが残り、費用の異常に気付けない。
    """
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 100}
    # 1000 * 1.00 + 100 * 2.50 (per 1M)
    assert pricing.estimate_cost_usd("gemini-2.5-flash", usage) == Decimal("0.001250")


def test_estimate_cost_text_only_bills_prompt_as_text() -> None:
    """② ③ (音声を送らない呼び出し) は、内訳が無いとき文字の値段で数える。"""
    usage = {"promptTokenCount": 1000, "candidatesTokenCount": 100}
    # 1000 * 0.30 + 100 * 2.50 (per 1M)
    assert pricing.estimate_cost_usd("gemini-2.5-flash", usage, has_audio=False) == Decimal(
        "0.000550"
    )
    assert pricing.split_prompt_tokens(usage, has_audio=False) == (0, 1000)


def test_estimate_cost_counts_thoughts_tokens() -> None:
    usage = {"promptTokenCount": 0, "candidatesTokenCount": 100, "thoughtsTokenCount": 100}
    assert pricing.estimate_cost_usd("gemini-2.5-flash", usage) == Decimal("0.000500")


def test_price_table_falls_back_for_unknown_model() -> None:
    assert pricing.price_table("gemini-9.9-unknown") == pricing.PRICE_PER_1M["gemini-2.5-flash"]
    assert pricing.PRICE_PER_1M["gemini-3.8-flash"]["out"] == 3.75
    assert pricing.estimate_cost_usd("gemini-3.5-flash-lite", SAMPLE_USAGE) == Decimal("0.000906")


def test_estimate_cost_with_empty_usage_is_zero() -> None:
    assert pricing.estimate_cost_usd("gemini-2.5-flash", None) == Decimal("0.000000")


# --- render_summary_text --------------------------------------------------


def test_render_summary_text_formats_sections_and_skips_empty() -> None:
    text = render_summary_text(SAMPLE_MODEL_JSON["summary"])
    assert text == "\n".join(
        [
            "【主訴・様子】",
            "・だるさの訴えあり",
            "・食欲は普段どおり",
            "【バイタル】",
            "・血圧: 132/78",
            "・体温: 36.7",
            "・SpO2: 98",
            "【処置・ケア】",
            "・褥瘡の包交を実施",
            "【申し送り】",
            "・主治医へ脈拍測定の可否を確認",
            "【次回】",
            "・来週水曜 10 時",
        ]
    )
    # 空欄の項目は出さない。
    assert "・脈拍:" not in text  # 測定値が無い項目は出さない
    assert "【備考】" not in text


def test_render_summary_text_v2_returns_body() -> None:
    summary = {"format": "v2", "body": "  腰の痛みは楽 [0:09]\n- 血圧 132/78 [0:03]  ", "facts": {}}
    assert render_summary_text(summary) == "腰の痛みは楽 [0:09]\n- 血圧 132/78 [0:03]"


def test_render_summary_text_handles_empty_and_free_text() -> None:
    assert render_summary_text(None) == ""
    assert render_summary_text({}) == ""
    text = render_summary_text({"free": "玄関の鍵の場所が変わった\n家族が不在だった"})
    assert text == "【備考】\n・玄関の鍵の場所が変わった\n・家族が不在だった"


# --- Factory / seam -------------------------------------------------------


class _Settings:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def test_get_voice_client_returns_none_when_provider_is_none() -> None:
    settings = _Settings(voice_ai_provider="none")
    assert vertex_client.get_voice_client(settings) is None


def test_get_voice_client_builds_from_settings() -> None:
    settings = _Settings(
        voice_ai_provider="vertex",
        vertex_project_id="rakusuke-voice",
        vertex_location="asia-northeast1",
        vertex_model_transcribe="gemini-2.5-flash",
        google_application_credentials="/opt/carelink/secrets/rakusuke-voice-sa.json",
        voice_ai_timeout_seconds=240,
    )
    client = vertex_client.get_voice_client(settings)
    assert client is not None
    assert client.model == "gemini-2.5-flash"
    assert "asia-northeast1-aiplatform.googleapis.com" in client.endpoint
    assert client._timeout == 240.0


def test_get_voice_client_defaults_when_settings_lack_fields() -> None:
    client = vertex_client.get_voice_client(_Settings())
    assert client is not None
    assert client.model == vertex_client.DEFAULT_MODEL


def test_set_test_client_overrides_factory() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload())

    stub = _client(handler)
    vertex_client.set_test_client(stub)
    assert vertex_client.get_voice_client(_Settings(voice_ai_provider="none")) is stub


async def test_transcribe_plain_returns_text_without_schema() -> None:
    """らく助エージェントへの声の入力: JSON にせず、文字だけを返す。東京の窓口。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_ok_payload("  田中さんが明日お休みです。\n"))

    client = _client(handler)
    text = await client.transcribe_plain(b"abc", "audio/mp4", "文字にして")
    await client.aclose()

    assert text == "田中さんが明日お休みです。"
    assert "asia-northeast1-aiplatform.googleapis.com" in seen["url"]
    config = seen["body"]["generationConfig"]
    assert "responseSchema" not in config
    assert "responseMimeType" not in config
    inline = seen["body"]["contents"][0]["parts"][1]["inlineData"]
    assert inline["mimeType"] == "audio/mp4"
    assert base64.b64decode(inline["data"]) == b"abc"
