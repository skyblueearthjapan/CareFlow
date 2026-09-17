"""音声記録のジョブ — 文字起こし/要約の起動と stale 掃除.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-4。

``run_transcribe_job`` は API 層 (``POST /visit-recordings`` / ``retry``) から
``BackgroundTasks`` 経由で呼ばれる。``asyncio.create_task`` ではなく
BackgroundTasks を使うのは **DB セッションの寿命** のため (integrations.py の
plan-actual と同じ理由): FastAPI は yield 依存を応答送出の前に閉じ、
バックグラウンドはその後に走るので、自前セッションとリクエストのセッションが
同時に生きる瞬間が出来ない。

進行は専用のジョブテーブルを作らず ``visit_recordings.status`` で表す:
``uploaded``/``unlinked`` → ``transcribing`` → ``summarized``/``failed``。
プロセスごと消えたジョブは ``reap_stale_jobs`` が ``failed`` へ倒す。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models.notification import Notification
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User
from app.models.visit import Visit
from app.models.visit_recording import (
    RECORDING_STATUS_FAILED,
    RECORDING_STATUS_SUMMARIZED,
    RECORDING_STATUS_TRANSCRIBING,
    RECORDING_STATUS_UNLINKED,
    VisitRecording,
)
from app.services.checkin.judge import JST
from app.services.voice.prompts import build_transcribe_summary_prompt
from app.services.voice.vertex_client import (
    MAX_INLINE_AUDIO_BYTES,
    VoiceAiError,
    VoiceAiResult,
    get_voice_client,
)

logger = logging.getLogger(__name__)

# ``error_message`` は **利用者が読む日本語の固定文言**。AI 側の生メッセージや
# 例外の種別を画面へ流すと、看護師には意味が無いうえ内部構造が漏れる。
# 「何で落ちたか」は ``error_kind`` 列 (機械向け) と log に残す。
#
# stale で failed 化したときの文言 (FE はこれをそのまま出して再試行を促す)。
STALE_ERROR_MESSAGE = "処理が中断されました（サーバー再起動の可能性）。再試行してください"
ERROR_KIND_STALE = "stale"

# 音声ファイルが消えていた (パージ済み / bind-mount 外れ)。retry しても直らない。
AUDIO_MISSING_MESSAGE = "音声ファイルが見つからないため処理できません"
ERROR_KIND_AUDIO_MISSING = "audio_missing"

# AI に送れる上限を超えた。分割はしない (Phase 1 の割り切り)。
TOO_LARGE_MESSAGE = "録音が長すぎるため処理できません"
ERROR_KIND_TOO_LARGE = "too_large"

# AI の設定が壊れている (provider は vertex なのにクライアントが作れない)。
# 現場では直せないので管理者へ誘導する。
AI_CONFIG_ERROR_MESSAGE = "AI 設定が無効です（管理者に連絡してください）"
ERROR_KIND_CONFIG = "config"

# AI 呼び出しが落ちた (timeout / http / auth / parse)。retry で直ることがある。
AI_FAILED_MESSAGE = "AI 処理に失敗しました。再試行してください"

# ``VOICE_AI_PROVIDER`` が取り得る値。``none`` = 受領のみ。
PROVIDER_NONE = "none"

# 一過性とみなして 1 回だけ再実行する ``VoiceAiError.kind`` (合計 2 回)。
# auth / parse / too_large は投げ直しても同じ結果になるので再実行しない。
# **再実行の判断はここだけ** (vertex_client は 1 回しか投げない)。
RETRYABLE_KINDS: frozenset[str] = frozenset({"timeout", "http"})

# 429 (レートリミット) は即やり直しても弾かれるので、この秒数だけ待ってから 1 回。
RATE_LIMIT_BACKOFF_SECONDS = 5.0

# 通知 (モバイルのベル)。1 録音 1 通 = reference で冪等化する。
NOTIFY_TYPE_VOICE_SUMMARY = "voice_summary"
NOTIFY_REFERENCE_TYPE = "visit_recording"
NOTIFY_TITLE = "訪問記録の要約ができました"


async def run_transcribe_job(recording_id: uuid.UUID) -> None:
    """録音 1 件を文字起こし + 要約して ``visit_recordings`` に書き戻す.

    ``VOICE_AI_PROVIDER='none'`` なら何もしない (受領だけの運用)。

    例外は **投げない**: BackgroundTasks の中で送出すると応答済みの
    リクエストの後始末に混ざるため、ここで握って log に落とす。行と音声は
    残るので、admin の ``POST /visit-recordings/{id}/retry`` でやり直せる。
    """
    settings = get_settings()
    provider = (settings.voice_ai_provider or PROVIDER_NONE).strip().lower()
    if provider == PROVIDER_NONE:
        logger.info(
            "voice: VOICE_AI_PROVIDER=none のため処理をスキップ (recording_id=%s)", recording_id
        )
        return

    factory = get_session_factory()
    try:
        async with factory() as db:
            await _transcribe_one(db, recording_id, settings)
    except Exception:  # noqa: BLE001 — 黙って死なせない (ログだけ残してジョブは落とさない)
        logger.exception("voice: 文字起こしジョブが落ちました (recording_id=%s)", recording_id)


async def _transcribe_one(db: AsyncSession, recording_id: uuid.UUID, settings) -> None:
    """1 録音分の本体 (自前セッション付き)。status 遷移はすべてここで完結する。"""
    row = await db.get(VisitRecording, recording_id)
    if row is None:
        logger.warning("voice: recording_id=%s が見つかりません", recording_id)
        return
    if row.deleted_at is not None:
        logger.info("voice: recording_id=%s は削除済みのため処理しません", recording_id)
        return
    if not row.audio_path or row.audio_deleted_at is not None:
        logger.info("voice: recording_id=%s は音声が無いため処理しません", recording_id)
        return
    if row.status == RECORDING_STATUS_TRANSCRIBING:
        # 二重起動 (同じ録音に retry を続けて押した等) は後勝ちにしない。
        logger.info("voice: recording_id=%s は既に処理中のため起動しません", recording_id)
        return

    client = get_voice_client(settings)
    if client is None:
        # provider は none でない (呼び出し元で弾いている) のにクライアントが
        # 作れない = 設定ミス。黙って ``uploaded`` のまま放置すると、画面には
        # 「処理中」に見える行が永遠に残る (stale reap も transcribing しか
        # 拾わない)。失敗として見せて admin の retry を促す。
        logger.error(
            "voice: provider=%s のクライアントを作れません (recording_id=%s)",
            settings.voice_ai_provider,
            recording_id,
        )
        await _mark_failed(db, row, AI_CONFIG_ERROR_MESSAGE, kind=ERROR_KIND_CONFIG)
        return

    patient_name, staff_name, visit_date = await _context_for(db, row)
    audio_path = Path(row.audio_path)
    mime = row.audio_mime or "audio/webm"

    row.status = RECORDING_STATUS_TRANSCRIBING
    row.error_message = None
    row.error_kind = None
    await db.commit()

    try:
        try:
            audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        except OSError as exc:
            logger.warning("voice: 音声ファイルを読めません (%s): %s", audio_path, exc)
            await _mark_failed(db, row, AUDIO_MISSING_MESSAGE, kind=ERROR_KIND_AUDIO_MISSING)
            return
        if len(audio_bytes) > MAX_INLINE_AUDIO_BYTES:
            logger.warning(
                "voice: audio is %d bytes (inline limit %d・recording_id=%s)",
                len(audio_bytes),
                MAX_INLINE_AUDIO_BYTES,
                recording_id,
            )
            await _mark_failed(db, row, TOO_LARGE_MESSAGE, kind=ERROR_KIND_TOO_LARGE)
            return

        prompt = build_transcribe_summary_prompt(patient_name, staff_name, visit_date)
        try:
            result = await _call_with_retry(client, audio_bytes, mime, prompt, recording_id)
        except VoiceAiError as exc:
            # 生メッセージは log にだけ残す (画面は固定文言・種別は error_kind)。
            logger.warning(
                "voice: AI 呼び出しが失敗 kind=%s (recording_id=%s): %s",
                exc.kind,
                recording_id,
                exc,
            )
            await _mark_failed(db, row, AI_FAILED_MESSAGE, kind=exc.kind)
            return
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — 後始末の失敗で結果を捨てない
            logger.exception("voice: クライアントの close に失敗 (recording_id=%s)", recording_id)

    await _save_result(db, row, result)
    await _notify_summary_ready(db, row, patient_name=patient_name)


async def _call_with_retry(
    client,
    audio_bytes: bytes,
    mime: str,
    prompt: str,
    recording_id: uuid.UUID,
) -> VoiceAiResult:
    """``timeout`` / ``http`` だけ 1 回やり直す (合計 2 回)。他は即 raise。

    **再実行の唯一の場所**。``vertex_client._post`` は 1 回しか投げないので、
    最悪の所要時間は ``VOICE_AI_TIMEOUT_SECONDS x 2 + 待ち`` で読める
    (``VOICE_JOB_STALE_MINUTES`` はこれより長く取る)。

    429 (レートリミット) だけは即やり直しても弾かれるので
    ``RATE_LIMIT_BACKOFF_SECONDS`` 待ってから 1 回だけ投げ直す。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await client.transcribe_and_summarize(audio_bytes, mime, prompt)
        except VoiceAiError as exc:
            if exc.kind not in RETRYABLE_KINDS or attempt > 1:
                raise
            wait = RATE_LIMIT_BACKOFF_SECONDS if exc.status_code == 429 else 0.0
            logger.warning(
                "voice: AI 呼び出しが %s で失敗 → %.1f 秒待って 1 回だけ再実行 (recording_id=%s)",
                exc.kind,
                wait,
                recording_id,
            )
            if wait:
                await asyncio.sleep(wait)


async def _context_for(
    db: AsyncSession, row: VisitRecording
) -> tuple[str | None, str | None, str | None]:
    """プロンプトに載せる文脈 (患者名・看護師名・訪問日) を引く。"""
    patient_name: str | None = None
    if row.patient_id is not None:
        patient_name = await db.scalar(select(Patient.name).where(Patient.id == row.patient_id))
    staff_name = await db.scalar(select(Staff.name).where(Staff.id == row.staff_id))

    visit_day = None
    if row.visit_id is not None:
        visit_day = await db.scalar(select(Visit.visit_date).where(Visit.id == row.visit_id))
    if visit_day is None and row.recorded_at is not None:
        recorded_at = row.recorded_at
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        visit_day = recorded_at.astimezone(JST).date()
    return patient_name, staff_name, visit_day.isoformat() if visit_day else None


async def _save_result(db: AsyncSession, row: VisitRecording, result: VoiceAiResult) -> None:
    """成功した結果を書き戻す (患者未紐付けなら ``unlinked`` のまま要約だけ残す)。"""
    row.transcript = result.transcript or None
    row.transcript_json = result.transcript_segments or []
    row.summary = result.summary or {}
    row.summary_text = result.summary_text or None
    row.provider = "vertex"
    row.model = result.model
    row.prompt_version = result.prompt_version
    row.tokens_in = result.tokens_in
    row.tokens_out = result.tokens_out
    row.cost_usd = result.cost_usd
    row.error_message = None
    row.error_kind = None
    # 紐付け待ちの行は「未紐付け」の表示を守る (要約は保存する)。
    row.status = (
        RECORDING_STATUS_SUMMARIZED if row.patient_id is not None else RECORDING_STATUS_UNLINKED
    )
    await db.commit()


async def _mark_failed(db: AsyncSession, row: VisitRecording, message: str, *, kind: str) -> None:
    """``failed`` へ倒す。``message`` は画面用の日本語・``kind`` は機械用の種別。"""
    row.status = RECORDING_STATUS_FAILED
    row.error_message = message
    row.error_kind = kind[:32]
    await db.commit()


async def _notify_summary_ready(
    db: AsyncSession, row: VisitRecording, *, patient_name: str | None
) -> None:
    """録音した本人へ「要約ができました」を 1 通。失敗しても本体はコミット済み。"""
    try:
        user_id = await _target_user_id(db, row)
        if user_id is None:
            return
        exists = await db.scalar(
            select(Notification.id)
            .where(
                Notification.user_id == user_id,
                Notification.reference_type == NOTIFY_REFERENCE_TYPE,
                Notification.reference_id == row.id,
            )
            .limit(1)
        )
        if exists is not None:
            return
        recorded_at = row.recorded_at
        if recorded_at is not None and recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        when = ""
        if recorded_at is not None:
            # ``%-m`` は Windows で使えないので手組みする。
            local = recorded_at.astimezone(JST)
            when = f"{local.month}月{local.day}日 {local:%H:%M}"
        who = patient_name or "患者未選択"
        db.add(
            Notification(
                user_id=user_id,
                type=NOTIFY_TYPE_VOICE_SUMMARY,
                title=NOTIFY_TITLE,
                body=f"{who}（{when}）の訪問記録の要約ができました。内容を確認してください。",
                reference_type=NOTIFY_REFERENCE_TYPE,
                reference_id=row.id,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — 通知の失敗で要約を失わない
        logger.exception("voice: 要約完了通知に失敗 (recording_id=%s)", row.id)
        await db.rollback()


async def _target_user_id(db: AsyncSession, row: VisitRecording) -> uuid.UUID | None:
    """通知の宛先 = 受領した本人 → 無ければ録音者 staff の生存ユーザー。"""
    if row.created_by_user_id is not None:
        alive = await db.scalar(
            select(User.id).where(User.id == row.created_by_user_id, User.deleted_at.is_(None))
        )
        if alive is not None:
            return alive
    return await db.scalar(
        select(User.id).where(User.staff_id == row.staff_id, User.deleted_at.is_(None))
    )


async def reap_stale_jobs(db: AsyncSession, *, now: datetime | None = None) -> int:
    """``transcribing`` のまま放置された録音を ``failed`` にする (冪等).

    専用のジョブテーブルを持たない設計 (§2-4) なので、``status`` +
    ``updated_at`` だけで「プロセスごと消えたジョブ」を判定する。受領時と
    パージ時に呼ぶ想定で、**自前で commit する** (``run_gps_purge`` と同じ作法)。

    Returns:
        int: failed へ倒した行数。
    """
    settings = get_settings()
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=max(1, settings.voice_job_stale_minutes))

    stmt = (
        update(VisitRecording)
        .where(
            VisitRecording.status == RECORDING_STATUS_TRANSCRIBING,
            VisitRecording.updated_at < cutoff,
            VisitRecording.deleted_at.is_(None),
        )
        .values(
            status=RECORDING_STATUS_FAILED,
            error_message=STALE_ERROR_MESSAGE,
            error_kind=ERROR_KIND_STALE,
        )
        .execution_options(synchronize_session=False)
    )
    result = await db.execute(stmt)
    reaped = int(result.rowcount or 0)
    await db.commit()
    if reaped:
        logger.info("voice: stale ジョブ %d 件を failed 化 (cutoff=%s)", reaped, cutoff)
    return reaped


async def reap_stale_jobs_standalone() -> int:
    """``reap_stale_jobs`` を **自前セッション** で回す (BackgroundTasks 用)。

    受領 / 再処理のハンドラから直接呼ぶと、掃除の commit がリクエストの
    トランザクションを巻き込む (受領した行まで一緒に確定する / 掃除が転ぶと
    rollback で受領が消える)。応答を返した後に別セッションで走らせて、
    掃除の成否を受領の成否から切り離す。

    例外は投げない (BackgroundTasks の中で送出しても誰も拾わない)。
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            return await reap_stale_jobs(db)
    except Exception:  # noqa: BLE001 — 掃除の失敗でログ以外に影響を出さない
        logger.exception("voice: reap_stale_jobs failed (swallowed)")
        return 0
