"""らく助エージェントへの声の入力 — 短い録音を文字にして返す (admin のみ).

docs/plans/voice-input-and-karte-engine-research-2026-10-13.md §4-A (A-1)。

* ``GET  /agent/transcribe`` — 使えるか (``{"available": bool, ...}``)。画面は
  ``available`` が true の時だけ 🎙 を出す。
* ``POST /agent/transcribe`` — multipart の ``audio`` (2 MB・60 秒まで) を
  Vertex AI 東京の Gemini で文字にして ``{"text": "..."}`` を返す。入力欄に入れる
  だけで、送信は人が押す。

音声は **保存しない**: メモリの上で受け取り、Vertex に 1 回投げたら捨てる
(ファイルを作らない)。ログに残すのは長さ・大きさ・かかった時間だけ。監査の
ミドルウェアもこのパスの本文は読まない (``middleware/audit.py``)。

使うには訪問の音声記録と同じ設定が要る (新しい鍵は無い):
``VOICE_AI_PROVIDER=vertex``・``VERTEX_PROJECT_ID``・``VERTEX_LOCATION=asia-northeast1``・
``GOOGLE_APPLICATION_CREDENTIALS`` (SA キーのファイルが実在すること)。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.core.config import get_settings
from app.core.deps import require_role
from app.models.user import User
from app.services.voice import prompts, vertex_client
from app.services.voice.vertex_client import VoiceAiError

logger = logging.getLogger(__name__)

router = APIRouter()

AdminUser = Annotated[User, Depends(require_role("admin"))]

#: 受け取る音声の上限 (2 MB)。60 秒の録音は 32kbps で約 240 KB、iPhone の AAC でも 1 MB 前後。
MAX_AUDIO_BYTES = 2 * 1024 * 1024
#: 録音の長さの上限 (秒)。画面は 60 秒で自動で止める。少しだけ余裕を見る。
MAX_DURATION_SEC = 65
#: 1 人あたり 10 分に 30 回まで。
RATE_LIMIT_COUNT = 30
RATE_LIMIT_WINDOW_SEC = 600.0
#: 1 回の呼び出しの待ち時間の上限 (秒)。短い音声なので訪問の録音 (240 秒) より短く切る。
TRANSCRIBE_TIMEOUT_SEC = 30.0

_CHUNK_BYTES = 64 * 1024
_CONTENT_LENGTH_SLACK = 16 * 1024

#: user_id -> 呼び出した時刻 (monotonic)。uvicorn は 1 プロセスなのでメモリで足りる
#: (`core/rate_limit.py` と同じ前提・デプロイで空に戻る)。
_CALLS: dict[UUID, deque[float]] = {}


def reset_rate_limit() -> None:
    """テスト用: 回数の記録を空にする。"""
    _CALLS.clear()


def _take_slot(user_id: UUID) -> bool:
    now = time.monotonic()
    calls = _CALLS.setdefault(user_id, deque())
    while calls and now - calls[0] >= RATE_LIMIT_WINDOW_SEC:
        calls.popleft()
    if len(calls) >= RATE_LIMIT_COUNT:
        return False
    calls.append(now)
    return True


def _normalize_mime(raw: str | None) -> str:
    """``audio/webm;codecs=opus`` → ``audio/webm``。"""
    return (raw or "").split(";", 1)[0].strip().lower()


@router.get("/transcribe", summary="声の入力が使えるか")
async def transcribe_status(_user: AdminUser) -> dict[str, Any]:
    return {
        "available": vertex_client.is_configured(get_settings()),
        "max_seconds": 60,
        "max_bytes": MAX_AUDIO_BYTES,
    }


@router.post("/transcribe", summary="短い録音を文字にする (保存しない)")
async def transcribe(
    request: Request,
    user: AdminUser,
    audio: Annotated[UploadFile, File(...)],
    duration_sec: Annotated[float | None, Form()] = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not vertex_client.is_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="いまは声の入力が使えません。文字で入力してください。",
        )
    if duration_sec is not None and not 0 <= duration_sec <= MAX_DURATION_SEC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="録音は 60 秒までです。",
        )
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_AUDIO_BYTES + _CONTENT_LENGTH_SLACK:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="録音が長すぎます（60 秒まで）。",
        )
    mime = _normalize_mime(audio.content_type)
    if not mime.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="音声だけを受け付けます。",
        )

    # メモリの上だけで受け取る (ファイルに書かない)。上限を超えた時点で打ち切る。
    buf = bytearray()
    while True:
        chunk = await audio.read(_CHUNK_BYTES)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_AUDIO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="録音が長すぎます（60 秒まで）。",
            )
    await audio.close()
    if not buf:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="録音が空でした。もう一度お試しください。",
        )

    if not _take_slot(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="続けて使われたので、少し休ませてください（10 分に 30 回まで）。",
        )

    size = len(buf)
    started = time.monotonic()
    client = vertex_client.get_voice_client(settings)
    if client is None:  # is_configured と食い違った時の保険
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="いまは声の入力が使えません。文字で入力してください。",
        )
    try:
        try:
            text = await asyncio.wait_for(
                client.transcribe_plain(bytes(buf), mime, prompts.build_agent_instruction_prompt()),
                timeout=TRANSCRIBE_TIMEOUT_SEC,
            )
        except TimeoutError as exc:
            raise VoiceAiError("timeout", "agent transcribe timed out") from exc
    except VoiceAiError as exc:
        logger.warning(
            "agent transcribe failed kind=%s status=%s bytes=%d duration=%s latency_ms=%d",
            exc.kind,
            exc.status_code,
            size,
            duration_sec,
            int((time.monotonic() - started) * 1000),
        )
        if exc.kind == "timeout":
            detail = "文字にするのに時間がかかりすぎました。もう一度お試しください。"
        else:
            detail = "文字にできませんでした。少し待ってからもう一度お試しください。"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from None
    finally:
        buf.clear()
        await client.aclose()

    logger.info(
        "agent transcribe ok bytes=%d duration=%s latency_ms=%d chars=%d",
        size,
        duration_sec,
        int((time.monotonic() - started) * 1000),
        len(text),
    )
    return {"text": text}
