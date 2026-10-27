"""音声記録のジョブ — ① 文字起こし → ② 補正 → ③ まとめ の起動と stale 掃除.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-4、
v2 の 3 段は ``docs/plans/visit-voice-record-v2-design-2026-10-27.md`` §3・§4。

``run_transcribe_job`` は API 層 (``POST /visit-recordings`` / ``retry``) から
``BackgroundTasks`` 経由で呼ばれる。``asyncio.create_task`` ではなく
BackgroundTasks を使うのは **DB セッションの寿命** のため (integrations.py の
plan-actual と同じ理由): FastAPI は yield 依存を応答送出の前に閉じ、
バックグラウンドはその後に走るので、自前セッションとリクエストのセッションが
同時に生きる瞬間が出来ない。

進行は専用のジョブテーブルを作らず ``visit_recordings.status`` で表す:
``uploaded``/``unlinked`` → ``transcribing`` → ``summarized``/``failed``。
プロセスごと消えたジョブは ``reap_stale_jobs`` が ``failed`` へ倒す。

v2 の流れと失敗の扱い (設計 §4):

* ① 文字起こし (音声 → 行・上限で切れたら続き) — 失敗 → ``failed`` (種別は今までどおり
  ``timeout`` / ``http`` / ``auth`` / ``parse`` と、読み切れない ``incomplete``)。
* ② 補正 (文字だけ・部分ごとに検査) — 失敗しても止めず、①の行のまま進む。
* ③ まとめ (席 or Gemini の v2) — 失敗 → ``failed`` (種別は ``summary_<kind>``)。再試行は①から。
* 費用・トークンは全部の呼び出しの合計。``model`` は文字起こしのモデル (席で書けたら席のモデル)。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

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
from app.services.voice import audio_split, correction, karte_seat, prompts, summary, transcribe
from app.services.voice.vertex_client import (
    MAX_INLINE_AUDIO_BYTES,
    ModelReply,
    VoiceAiError,
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

# ① で発言が 1 行も取れなかった (声が入っていない録音)。まとめは作らない。
EMPTY_TRANSCRIPT_MESSAGE = "会話を聞き取れませんでした（録音に声が入っていない可能性があります）"
ERROR_KIND_EMPTY_TRANSCRIPT = "transcript_empty"

# ③ の失敗の種別の頭 (例 ``summary_parse`` / ``summary_timeout``)。
SUMMARY_ERROR_PREFIX = "summary_"

# ``VOICE_AI_PROVIDER`` が取り得る値。``none`` = 受領のみ。
PROVIDER_NONE = "none"

# 一過性とみなして 1 回だけ再実行する ``VoiceAiError.kind`` (合計 2 回)。
# auth / parse / too_large / incomplete は投げ直しても同じ結果になるので再実行しない。
# **再実行の判断はここだけ** (vertex_client は 1 回しか投げない)。
RETRYABLE_KINDS: frozenset[str] = frozenset({"timeout", "http"})

# 429 (レートリミット) は即やり直しても弾かれるので、この秒数だけ待ってから 1 回。
RATE_LIMIT_BACKOFF_SECONDS = 5.0

# 通知 (モバイルのベル)。1 録音 1 通 = reference で冪等化する。
NOTIFY_TYPE_VOICE_SUMMARY = "voice_summary"
NOTIFY_REFERENCE_TYPE = "visit_recording"
NOTIFY_TITLE = "訪問記録の要約ができました"


@dataclass
class _Usage:
    """呼び出しの費用の合計 (①の続き・②の区切り・③ を全部足す)。"""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))

    def add(self, reply: ModelReply) -> ModelReply:
        self.tokens_in += reply.tokens_in
        self.tokens_out += reply.tokens_out
        self.cost_usd += reply.cost_usd
        return reply


@dataclass
class _Draft:
    """③ の結果 (席 or Gemini)。"""

    summary: dict[str, Any]
    summary_text: str
    provider: str
    model: str | None
    prompt_version: str
    fallback_kind: str | None = None


async def run_transcribe_job(recording_id: uuid.UUID) -> None:
    """録音 1 件を ① 文字起こし → ② 補正 → ③ まとめ して ``visit_recordings`` に書き戻す.

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
    duration = float(row.duration_sec or 0) or None

    row.status = RECORDING_STATUS_TRANSCRIBING
    row.error_message = None
    row.error_kind = None
    await db.commit()

    usage = _Usage()

    async def ask_text(prompt: str) -> ModelReply:
        return usage.add(await _call_with_retry(lambda: client.generate_text(prompt), recording_id))

    try:
        try:
            audio_bytes = await asyncio.to_thread(audio_path.read_bytes)
        except OSError as exc:
            logger.warning("voice: 音声ファイルを読めません (%s): %s", audio_path, exc)
            await _mark_failed(db, row, AUDIO_MISSING_MESSAGE, kind=ERROR_KIND_AUDIO_MISSING)
            return
        # 長い録音は区切る (P1b・設計 §8)。区切れないとき (短い・ffmpeg が無い・読めない) は 1 本で送る。
        try:
            pieces = await audio_split.split_audio(audio_path)
        except audio_split.AudioSplitError as exc:
            logger.warning(
                "voice: 録音を区切れないため 1 本で送ります (recording_id=%s): %s",
                recording_id,
                exc,
            )
            pieces = None
        if pieces is None:
            if len(audio_bytes) > MAX_INLINE_AUDIO_BYTES:
                logger.warning(
                    "voice: audio is %d bytes (inline limit %d・recording_id=%s)",
                    len(audio_bytes),
                    MAX_INLINE_AUDIO_BYTES,
                    recording_id,
                )
                await _mark_failed(db, row, TOO_LARGE_MESSAGE, kind=ERROR_KIND_TOO_LARGE)
                return
            pieces = [
                audio_split.Piece(start=0.0, end=duration or 0.0, data=audio_bytes, mime=mime)
            ]
            whole = True
        else:
            whole = False

        # ① 文字起こし (区切りごとに読み、区切りの始まりの分だけ時刻をずらしてつなぐ)
        async def transcribe_piece(
            piece: audio_split.Piece, carry: Sequence[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            piece_duration = duration if whole else piece.duration

            async def ask_audio(prompt: str) -> ModelReply:
                return usage.add(
                    await _call_with_retry(
                        lambda: client.transcribe_lines(piece.data, piece.mime, prompt),
                        recording_id,
                    )
                )

            def build_prompt(from_sec: float | None, before: Sequence[dict[str, Any]]) -> str:
                return prompts.build_transcribe_prompt(
                    patient_name,
                    staff_name,
                    visit_date,
                    duration_sec=piece_duration,
                    from_sec=from_sec,
                    before=before,
                    carry=carry,
                )

            lines = await transcribe.transcribe(
                ask_audio, build_prompt, duration_sec=piece_duration
            )
            return [
                {
                    **line,
                    "start": round(line["start"] + piece.start, 1),
                    "end": round(line["end"] + piece.start, 1),
                }
                for line in lines
            ]

        try:
            raw_lines: list[dict[str, Any]] = []
            for piece in pieces:
                raw_lines.extend(await transcribe_piece(piece, raw_lines))
                if not whole:
                    await _touch(db, row)
        except VoiceAiError as exc:
            # 生メッセージは log にだけ残す (画面は固定文言・種別は error_kind)。
            logger.warning(
                "voice: 文字起こしが失敗 kind=%s (recording_id=%s): %s",
                exc.kind,
                recording_id,
                exc,
            )
            await _mark_failed(db, row, AI_FAILED_MESSAGE, kind=exc.kind)
            return
        if not raw_lines:
            await _mark_failed(db, row, EMPTY_TRANSCRIPT_MESSAGE, kind=ERROR_KIND_EMPTY_TRANSCRIPT)
            return
        await _touch(db, row)

        # ② 補正 (失敗しても止めない)
        try:
            fixed_lines = await correction.correct_lines(
                raw_lines, ask_text, recording_id=recording_id
            )
        except Exception:  # noqa: BLE001 — 補正で止めない (①の行で進む)
            logger.exception("voice: 補正で想定外の失敗 (recording_id=%s)", recording_id)
            fixed_lines = None
        lines_for_summary = fixed_lines or raw_lines
        await _touch(db, row)

        # ③ まとめ (席 → だめなら Gemini の v2)
        try:
            draft = await _make_draft(
                db,
                row,
                lines_for_summary,
                ask_text,
                settings,
                context=(patient_name, staff_name, visit_date),
                model=client.model,
            )
        except VoiceAiError as exc:
            logger.warning(
                "voice: まとめが失敗 kind=%s (recording_id=%s): %s",
                exc.kind,
                recording_id,
                exc,
            )
            await _mark_failed(db, row, AI_FAILED_MESSAGE, kind=SUMMARY_ERROR_PREFIX + exc.kind)
            return
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — 後始末の失敗で結果を捨てない
            logger.exception("voice: クライアントの close に失敗 (recording_id=%s)", recording_id)

    await _save_result(
        db,
        row,
        raw_lines=raw_lines,
        fixed_lines=fixed_lines,
        draft=draft,
        usage=usage,
        transcribe_model=client.model,
    )
    await _notify_summary_ready(db, row, patient_name=patient_name)


async def _touch(db: AsyncSession, row: VisitRecording) -> None:
    """段の区切りで ``updated_at`` を進める (3 段で長くなっても stale 掃除に倒されないように)。"""
    row.updated_at = datetime.now(UTC)
    await db.commit()


async def _make_draft(
    db: AsyncSession,
    row: VisitRecording,
    lines: Sequence[dict[str, Any]],
    ask_text: Callable[[str], Awaitable[ModelReply]],
    settings,
    *,
    context: tuple[str | None, str | None, str | None],
    model: str,
) -> _Draft:
    """③ 拠点の旗 ``karte_via_seat`` が ON なら席で書き、だめなら (OFF も) Gemini の v2 で書く.

    席の失敗は ``fallback_kind = seat_<kind>`` に残して Gemini へ進む (カルテを止めない)。
    Gemini の失敗は ``VoiceAiError`` のまま投げる (呼び出し側が ``summary_<kind>`` で failed)。
    """
    fallback_kind: str | None = None
    try:
        if await karte_seat.flag_enabled(db, row.office_id):
            seat_context = await karte_seat.build_context(db, row)
            seat = await karte_seat.request_draft(
                settings,
                recording_id=row.id,
                transcript=prompts.timed_lines_text(lines),
                context=seat_context,
                presser_user_id=row.created_by_user_id,
            )
            return _Draft(
                summary=seat.summary,
                summary_text=seat.summary_text,
                provider=seat.provider,
                model=seat.model or model,
                prompt_version=seat.prompt_version or prompts.PROMPT_VERSION,
            )
    except karte_seat.SeatDraftError as exc:
        logger.info(
            "voice: 席での下書きをやめて Gemini で書きます kind=%s (recording_id=%s)",
            exc.kind,
            row.id,
        )
        fallback_kind = exc.error_kind
    except Exception:  # noqa: BLE001 — 想定外でも Gemini で続ける
        logger.exception("voice: 席での下書きで想定外の失敗 (recording_id=%s)", row.id)
        fallback_kind = "seat_error"

    patient_name, staff_name, visit_date = context
    reply = await ask_text(
        prompts.build_summary_prompt(lines, patient_name, staff_name, visit_date)
    )
    drafted = summary.read_reply(reply.text)
    return _Draft(
        summary=drafted,
        summary_text=drafted["body"],
        provider="vertex",
        model=model,
        prompt_version=prompts.PROMPT_VERSION,
        fallback_kind=fallback_kind,
    )


async def _call_with_retry(
    call: Callable[[], Awaitable[ModelReply]],
    recording_id: uuid.UUID,
) -> ModelReply:
    """``timeout`` / ``http`` だけ 1 回やり直す (合計 2 回)。他は即 raise。

    **再実行の唯一の場所**。``vertex_client._post`` は 1 回しか投げないので、
    1 回の呼び出しの最悪の所要時間は ``VOICE_AI_TIMEOUT_SECONDS x 2 + 待ち`` で読める。
    v2 では①の続き・②の区切り・③ がそれぞれこれを通る (``VOICE_JOB_STALE_MINUTES`` に
    当たらないよう、段の区切りで ``_touch`` が ``updated_at`` を進める)。

    429 (レートリミット) だけは即やり直しても弾かれるので
    ``RATE_LIMIT_BACKOFF_SECONDS`` 待ってから 1 回だけ投げ直す。
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await call()
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


async def _save_result(
    db: AsyncSession,
    row: VisitRecording,
    *,
    raw_lines: list[dict[str, Any]],
    fixed_lines: list[dict[str, Any]] | None,
    draft: _Draft,
    usage: _Usage,
    transcribe_model: str,
) -> None:
    """成功した結果を書き戻す (患者未紐付けなら ``unlinked`` のまま要約だけ残す)。

    * ``transcript_json`` = 直す前の行・``transcript_fixed_json`` = 直した行 (直せなければ NULL)。
      ``transcript`` (文字列) は直した行から組み立てる (直せなければ元の行から)。

    人手修正 (``summary_edited_at`` が立っている行) はここで踏み潰されるので、
    上書きする直前に ``summary['previous_manual']`` へ退避する (設計 §11-2)。

    * 退避と ``summary_edited_*`` のクリアは **この関数でだけ** 行う。再処理を
      頼んだ時点 (``POST /{id}/retry``) でクリアすると、ジョブが失敗したときに
      人が直した文だけが残って出所が AI に見える行が出来る。失敗経路
      (``_mark_failed``) は要約に触らないので、その場合は人手修正が痕跡ごと残る。
    * 残すのは **直近 1 件のみ**。世代を溜める場所ではない (履歴が要るなら
      audit_logs 側の仕事)。2 度目の書き直しは 1 度目の ``previous_manual`` を
      その時点の ``summary_text`` で置き換える。
    * マージ順は ``{**result, **kept}``。AI が ``previous_manual`` というキーを
      返してきても人手修正の退避が勝つ (構造的に守る)。
    """
    # 既に退避済みのぶん (前回の書き直しで入った previous_manual) も引き継ぐ。
    kept: dict = {k: v for k, v in (row.summary or {}).items() if k == "previous_manual"}
    if row.summary_edited_at is not None:
        edited_at = row.summary_edited_at
        if edited_at.tzinfo is None:
            edited_at = edited_at.replace(tzinfo=UTC)
        kept["previous_manual"] = {
            "summary_text": row.summary_text,
            "edited_by": str(row.summary_edited_by) if row.summary_edited_by else None,
            "edited_at": edited_at.isoformat(),
            "replaced_at": datetime.now(UTC).isoformat(),
        }
        row.summary_edited_by = None
        row.summary_edited_at = None
    # AI が本文を書き直した以上、以前の「確認済み」は別の文に対する承認なので外す
    # (PATCH summary_text と同じ原則。note_append=現場の追記 は承認を維持する)。
    row.reviewed_by = None
    row.reviewed_at = None

    row.transcript = transcribe.transcript_text(fixed_lines or raw_lines) or None
    row.transcript_json = raw_lines
    row.transcript_fixed_json = fixed_lines
    row.summary = {**(draft.summary or {}), **kept}
    row.summary_text = draft.summary_text or None
    row.provider = draft.provider
    row.model = draft.model or transcribe_model
    row.prompt_version = draft.prompt_version
    row.tokens_in = usage.tokens_in
    row.tokens_out = usage.tokens_out
    row.cost_usd = usage.cost_usd
    row.error_message = None
    # 成功でも、席での下書きをやめて Gemini で書いたときは理由 (seat_<kind>) を残す。
    row.error_kind = draft.fallback_kind[:32] if draft.fallback_kind else None
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
