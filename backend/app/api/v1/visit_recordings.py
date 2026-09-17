"""訪問の音声記録 API (``/api/v1/visit-recordings``) — Phase 1.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-5 / §10-3。

  * ``POST   /visit-recordings``            受領 (multipart・ストリーミング書き込み)
  * ``GET    /visit-recordings``            一覧 (BE 絞り込み + ページング)
  * ``GET    /visit-recordings/{id}``       詳細 (文字起こし・要約を含む)
  * ``GET    /visit-recordings/{id}/audio`` 音声本体 (Bearer・Range 可)
  * ``PATCH  /visit-recordings/{id}``       紐付け / 確認済み / 要約の追記・人手修正
  * ``POST   /visit-recordings/{id}/retry`` 再処理 (admin)
  * ``DELETE /visit-recordings/{id}``       soft delete + 音声削除 (admin)

可視性 (設計 §2-6): **staff は自分が録音した行だけ** 見える (担当外は 404 で
存在ごと秘匿)。admin は全件。``visit_id`` を付けて受領するときだけ、その訪問が
自分に見えるか (``visits.py`` の ``_staff_visibility_filter`` = コース担当
フォールバック込み) を確かめる — 一覧と詳細で帰属規則を二重に書かないため
同じ述語を再利用する。

保存レイアウト (ホスト bind-mount・``.env.example``):
``${VISIT_AUDIO_DIR}/{yyyy}/{mm}/{recording_id}.{ext}``

受領時のメモリとディスク (実態):

* Starlette の ``UploadFile`` は 1 MiB を超えた時点で自前の spool ファイル
  (``SpooledTemporaryFile``) へ落とす。したがって **RAM は抑えられている** が、
  音声はまず spool に、次に ``VISIT_AUDIO_DIR`` にと **ディスクへ 2 回** 書かれる。
* ここで ``UploadFile`` を 1 MiB ずつ読んで ``.part`` へ流すのは RAM のためでは
  なく、``await audio.read()`` の全量 read (= 20 MiB の bytes を 1 個作る) を
  避けるためと、**累積が上限を超えた時点で打ち切る** ため。
* 受領そのものを弾けるときは弾く: ``Content-Length`` が上限 + 余白を超えていれば
  1 バイトも読まずに 413 を返す。
* 監査ミドルウェア側の body バッファも ``app/middleware/audit.py`` でバイパス
  してある (multipart のパス規則 + Content-Length の大きさ規則)。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.visits import _staff_visibility_filter
from app.core.config import get_settings
from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.office import Office
from app.models.patient import Patient
from app.models.staff import Staff
from app.models.user import User, normalize_user_role
from app.models.visit import VISIT_STATUS_CANCELLED, Visit
from app.models.visit_recording import (
    RECORDING_STATUS_UNLINKED,
    RECORDING_STATUS_UPLOADED,
    VisitRecording,
)
from app.schemas.visit_recording import (
    VisitRecordingList,
    VisitRecordingRead,
    VisitRecordingReportRead,
    VisitRecordingUpdate,
)
from app.services.checkin.judge import JST
from app.services.voice.jobs import reap_stale_jobs_standalone, run_transcribe_job
from app.services.voice.record_report_html import render_visit_record_html

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/visit-recordings", tags=["visit-recordings"])

# 受領の読み取り単位 (= Starlette の spool しきい値と同じ 1 MiB)。
_CHUNK_BYTES = 1024 * 1024

# MIME → 拡張子。ここに無い ``audio/*`` はサブタイプから素直に導く
# (MediaRecorder の実装差で未知のサブタイプが来ても受領は落とさない)。
_EXT_BY_MIME: dict[str, str] = {
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
}

# 紐付け変更を録音者本人に許す猶予 (設計 §10-3)。これを過ぎたら admin だけ。
_RELINK_GRACE = timedelta(hours=24)

# 同意が取れていないときの 422 文言 (FE はこれをそのまま出す)。
CONSENT_REQUIRED_DETAIL = "録音の同意が確認できていないため保存できません"

# ``duration_sec`` の上限 (6 時間)。端末が ms を秒として送る類の事故を弾く
# — 1 訪問が 6 時間を超えることは無い。
_MAX_DURATION_SEC = 6 * 60 * 60

# ``recorded_at`` に許す幅。過去は「圏外だった端末がまとめて送る」ぶんだけ
# 認め (7 日)、未来は端末時計のずれぶんだけ (10 分)。ここを開けておくと、
# 端末時計が狂った 1 台が保存先ディレクトリ (yyyy/mm) とパージ基準を
# 巻き込んで壊す。**パージ基準は created_at (サーバー受領時刻) の方を使う**。
_RECORDED_AT_PAST_LIMIT = timedelta(days=7)
_RECORDED_AT_FUTURE_LIMIT = timedelta(minutes=10)

# ``Content-Length`` で即断するときの余白。multipart の境界・他フィールド・
# ヘッダの分だけ本体より大きくなるので、この余白を足した上で比較する。
_CONTENT_LENGTH_SLACK = 64 * 1024

# 413 の文言に出す「約 N 分」の換算レート (FE の録音設定と同じ 32kbps)。
_AUDIO_BITRATE_BPS = 32_000

# 音声本体と印刷レポートは個人情報そのもの。中間キャッシュにも端末にも残さない。
_NO_STORE: dict[str, str] = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------------------
# ヘルパ


def _audio_root() -> Path:
    """設定を **呼び出しの都度** 読む (テストが tmp_path を差し込めるように)。"""
    return Path(get_settings().visit_audio_dir)


def _is_admin(user: User) -> bool:
    return normalize_user_role(user.role) == "admin"


def _parse_uuid_form(value: str | None, field: str) -> uuid.UUID | None:
    """multipart の任意 UUID フィールドを読む (空文字 / 'null' は未指定扱い)。"""
    if value is None:
        return None
    raw = value.strip()
    if raw in {"", "null", "undefined"}:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} is not a valid UUID",
        ) from exc


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ext_for_mime(mime: str) -> str:
    known = _EXT_BY_MIME.get(mime)
    if known:
        return known
    subtype = re.sub(r"[^a-z0-9]", "", mime.split("/", 1)[-1])[:8]
    return f".{subtype}" if subtype else ".bin"


def _normalize_mime(raw: str | None) -> str:
    """``audio/webm;codecs=opus`` → ``audio/webm`` (小文字・パラメータ除去)。"""
    return (raw or "").split(";", 1)[0].strip().lower()


def _jst_day_start_utc(day: date) -> datetime:
    """JST の 0:00 を UTC の aware datetime にする (一覧の日付境界用)。"""
    return datetime.combine(day, time.min, tzinfo=JST).astimezone(UTC)


def _max_audio_minutes(max_bytes: int) -> int:
    """上限バイト数を「約 N 分」に直す (413 の文言用・32kbps 換算)。"""
    return max(1, int(max_bytes * 8 / _AUDIO_BITRATE_BPS / 60))


def _too_large_detail(max_bytes: int) -> str:
    return f"録音が大きすぎます（約 {_max_audio_minutes(max_bytes)} 分まで。32kbps で計算）"


async def _names_for(
    db: AsyncSession, rows: list[VisitRecording]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """患者名 / スタッフ名 / 拠点名を 3 クエリでまとめて解決する (N+1 回避)。

    一覧は 1 ページ 50 行まで返すので、行ごとに名前を引くと 150 クエリになる。
    id を集めてから ``IN`` で 1 回ずつ引く (件数に依らずクエリ数は 3 本)。
    """
    patient_ids = {r.patient_id for r in rows if r.patient_id is not None}
    staff_ids = {r.staff_id for r in rows if r.staff_id is not None}
    office_ids = {r.office_id for r in rows if r.office_id is not None}
    patients: dict[uuid.UUID, str] = {}
    staff: dict[uuid.UUID, str] = {}
    offices: dict[uuid.UUID, str] = {}
    if patient_ids:
        for pid, name in (
            await db.execute(select(Patient.id, Patient.name).where(Patient.id.in_(patient_ids)))
        ).all():
            patients[pid] = name
    if staff_ids:
        for sid, name in (
            await db.execute(select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids)))
        ).all():
            staff[sid] = name
    if office_ids:
        for oid, name in (
            await db.execute(select(Office.id, Office.name).where(Office.id.in_(office_ids)))
        ).all():
            offices[oid] = name
    return patients, staff, offices


def _serialize(
    row: VisitRecording,
    *,
    patient_name: str | None = None,
    staff_name: str | None = None,
    office_name: str | None = None,
    include_transcript: bool = True,
) -> dict[str, Any]:
    """``VisitRecordingRead`` の中身 (手書き dict — 列を足したらここにも足す)。"""
    return {
        "id": row.id,
        "visit_id": row.visit_id,
        "patient_id": row.patient_id,
        "patient_name": patient_name,
        "staff_id": row.staff_id,
        "staff_name": staff_name,
        "office_id": row.office_id,
        "office_name": office_name,
        "recorded_at": row.recorded_at,
        "ended_at": row.ended_at,
        "duration_sec": row.duration_sec,
        "status": row.status,
        # 「音声が今も取れるか」= パージ済み / 削除済みは false。
        "has_audio": bool(row.audio_path) and row.audio_deleted_at is None,
        "audio_mime": row.audio_mime,
        "audio_bytes": row.audio_bytes,
        "transcript": row.transcript if include_transcript else None,
        "transcript_json": row.transcript_json if include_transcript else None,
        "summary": row.summary,
        "summary_text": row.summary_text,
        "summary_edited_by": row.summary_edited_by,
        "summary_edited_at": row.summary_edited_at,
        "provider": row.provider,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "cost_usd": float(row.cost_usd) if row.cost_usd is not None else None,
        "error_message": row.error_message,
        "error_kind": row.error_kind,
        "client_id": row.client_id,
        "consent_confirmed": row.consent_confirmed,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def _serialize_one(
    db: AsyncSession, row: VisitRecording, *, include_transcript: bool = True
) -> dict[str, Any]:
    patients, staff, offices = await _names_for(db, [row])
    return _serialize(
        row,
        patient_name=patients.get(row.patient_id) if row.patient_id else None,
        staff_name=staff.get(row.staff_id),
        office_name=offices.get(row.office_id) if row.office_id else None,
        include_transcript=include_transcript,
    )


async def _load_recording_or_404(
    db: AsyncSession, recording_id: uuid.UUID, user: User
) -> VisitRecording:
    """生きている行を可視性込みで引く (staff は自分の録音のみ・他は 404)。"""
    row = await db.scalar(
        select(VisitRecording).where(
            VisitRecording.id == recording_id,
            VisitRecording.deleted_at.is_(None),
        )
    )
    # detail は Starlette 既定の "Not Found" と区別できる固有文言にする (FE の再送キューは
    # 「ルート不在の 404」だけを保持し、確定回答の 404 は失敗一覧へ落とす)。
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="録音が見つかりません")
    if not _is_admin(user) and row.staff_id != user.staff_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="録音が見つかりません")
    return row


async def _load_visit_for_user(db: AsyncSession, visit_id: uuid.UUID, user: User) -> Visit:
    """録音を紐付けてよい訪問か (担当外は 404 = 存在ごと秘匿)。"""
    stmt = select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None))
    if not _is_admin(user):
        if user.staff_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="担当外または存在しない訪問です"
            )
        # 一覧と同じ述語 (primary/secondary/mentor/assignments/同行/コース担当)。
        stmt = stmt.where(_staff_visibility_filter(user.staff_id))
    visit = await db.scalar(stmt)
    if visit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="担当外または存在しない訪問です"
        )
    return visit


async def _find_by_client_id(
    db: AsyncSession, *, staff_id: uuid.UUID, client_id: uuid.UUID
) -> VisitRecording | None:
    """受領の冪等キーで **生きている** 行を引く (事前チェックと復帰で共用)。

    述語は partial unique ``(staff_id, client_id) WHERE client_id IS NOT NULL
    AND deleted_at IS NULL`` (migration 0086) と同じにする。片方だけ
    ``deleted_at`` を見ると、admin が消した録音と同じ client_id の再送が
    「既存は無い」→「unique に弾かれる」で 500 になる。
    """
    return await db.scalar(
        select(VisitRecording).where(
            VisitRecording.staff_id == staff_id,
            VisitRecording.client_id == client_id,
            VisitRecording.deleted_at.is_(None),
        )
    )


async def _resolve_office_id(
    db: AsyncSession, *, patient_id: uuid.UUID | None, staff_id: uuid.UUID
) -> uuid.UUID | None:
    """表示スコープの拠点: 患者の主担当拠点 → 無ければ録音者の主担当拠点。"""
    if patient_id is not None:
        office_id = await db.scalar(
            select(Patient.primary_office_id).where(Patient.id == patient_id)
        )
        if office_id is not None:
            return office_id
    return await db.scalar(select(Staff.primary_office_id).where(Staff.id == staff_id))


# ---------------------------------------------------------------------------
# 受領 (multipart)


@router.post(
    "",
    response_model=VisitRecordingRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="音声記録の受領 (multipart/form-data・ストリーミング書き込み)",
)
async def create_visit_recording(
    db: DbDep,
    user: CurrentActiveUser,
    background: BackgroundTasks,
    request: Request,
    response: Response,
    audio: Annotated[UploadFile, File(...)],
    recorded_at: Annotated[datetime, Form()],
    duration_sec: Annotated[int, Form()],
    consent: Annotated[str, Form()],
    visit_id: Annotated[str | None, Form()] = None,
    patient_id: Annotated[str | None, Form()] = None,
    device_mime: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    if user.staff_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="スタッフに紐付いていないアカウントでは録音を保存できません",
        )
    # 録音者 / 作成者を **ここで値として控える**。commit が unique で転んだ後の
    # 復帰路 (``except IntegrityError``) は ``db.rollback()`` を挟むので、その
    # 先で ``user.staff_id`` に触ると期限切れ属性の遅延ロードが 1 本走る
    # (エラー処理の中で新しいクエリを撃つのは、失敗が失敗を呼ぶ形)。
    staff_id = user.staff_id
    created_by_user_id = user.id
    # 同意は「取れている」ことが保存の前提 (設計 §2-6)。曖昧な値は拒否する。
    if consent.strip().lower() not in {"true", "1"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=CONSENT_REQUIRED_DETAIL
        )
    if not 0 <= duration_sec <= _MAX_DURATION_SEC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"duration_sec must be between 0 and {_MAX_DURATION_SEC}",
        )

    settings = get_settings()
    max_bytes = settings.visit_audio_max_bytes

    # ---- 読む前に弾けるものは弾く ----
    # ``Content-Length`` は端末の自己申告なので信用はしないが、申告の時点で
    # 上限を超えているなら 1 バイトも読む必要がない (下の累積チェックが本番)。
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:  # pragma: no cover - 壊れたヘッダは無視して読みに行く
        declared = 0
    if declared > max_bytes + _CONTENT_LENGTH_SLACK:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=_too_large_detail(max_bytes),
        )

    mime = _normalize_mime(audio.content_type)
    if not mime.startswith("audio/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="音声ファイル (audio/*) のみ受け付けます",
        )

    visit_uuid = _parse_uuid_form(visit_id, "visit_id")
    patient_uuid = _parse_uuid_form(patient_id, "patient_id")
    client_uuid = _parse_uuid_form(client_id, "client_id")

    recorded_at_utc = _as_utc(recorded_at)
    now = datetime.now(UTC)
    # 端末時計を鵜呑みにしない。保存先 (yyyy/mm) と一覧の日付境界がここで決まる。
    if not (now - _RECORDED_AT_PAST_LIMIT <= recorded_at_utc <= now + _RECORDED_AT_FUTURE_LIMIT):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="録音日時が受け付けられる範囲外です（端末の時計を確認してください）",
        )

    # ---- 冪等キー: 同じ録音の二度目の POST は既存行を返す (新しい行を作らない) ----
    # 圏外・再送・タブの二重送信で「同じ録音が 2 件」になると、AI 呼び出しも
    # 課金も二重になる。端末が 1 録音につき 1 個発行する client_id で弾く。
    if client_uuid is not None:
        existing = await _find_by_client_id(db, staff_id=staff_id, client_id=client_uuid)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return await _serialize_one(db, existing)

    recorded_at_jst = recorded_at_utc.astimezone(JST)

    # 訪問を指定したときだけ可視性を確かめる (担当外は 404)。患者だけ /
    # どちらも無しは「紐付け待ち」として許す。
    if visit_uuid is not None:
        visit = await _load_visit_for_user(db, visit_uuid, user)
        patient_uuid = patient_uuid or visit.patient_id

    recording_id = uuid.uuid4()
    ext = _ext_for_mime(mime)
    target_dir = _audio_root() / f"{recorded_at_jst:%Y}" / f"{recorded_at_jst:%m}"
    target_path = target_dir / f"{recording_id}{ext}"
    tmp_path = target_path.with_suffix(target_path.suffix + ".part")

    def _cleanup_tmp() -> None:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass

    # ---- ストリーミング受領 (1 MiB ずつ・累積上限で打ち切り) ----
    # ``except BaseException`` にするのが要点: クライアント切断
    # (``asyncio.CancelledError``) は ``Exception`` を継承しないので、
    # ``except OSError`` や ``except Exception`` では ``.part`` が消えずに
    # 残り続ける。回線の悪い現場から上がってくる録音ほど途中で切れるため、
    # 掃除を取りこぼすとディスクが ``.part`` で埋まる。握りはせず再送出する。
    total = 0
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("wb") as fh:
            while True:
                chunk = await audio.read(_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=_too_large_detail(max_bytes),
                    )
                fh.write(chunk)
    except HTTPException:
        _cleanup_tmp()
        raise
    except OSError as exc:
        _cleanup_tmp()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist audio: {exc}",
        ) from exc
    except BaseException:
        # 切断・タイムアウト・シャットダウン。掃除だけして素通しする。
        _cleanup_tmp()
        raise

    if total == 0:
        _cleanup_tmp()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty"
        )

    try:
        os.replace(tmp_path, target_path)
    except OSError as exc:  # pragma: no cover - defensive
        _cleanup_tmp()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist audio: {exc}",
        ) from exc

    row = VisitRecording(
        id=recording_id,
        visit_id=visit_uuid,
        patient_id=patient_uuid,
        staff_id=staff_id,
        office_id=await _resolve_office_id(db, patient_id=patient_uuid, staff_id=staff_id),
        recorded_at=recorded_at_utc,
        ended_at=recorded_at_utc + timedelta(seconds=duration_sec),
        duration_sec=duration_sec,
        audio_path=str(target_path),
        audio_mime=mime,
        audio_bytes=total,
        device_mime=(device_mime or audio.content_type or "")[:64] or None,
        # 患者が決まるまでは表示上 `unlinked` (処理の進み方は uploaded と同じ)。
        status=RECORDING_STATUS_UPLOADED if patient_uuid else RECORDING_STATUS_UNLINKED,
        consent_confirmed=True,
        created_by_user_id=created_by_user_id,
        client_id=client_uuid,
    )
    db.add(row)

    def _discard_audio() -> None:
        # DB とファイルの食い違いを残さない (写真と同じ後始末)。
        try:
            target_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass

    try:
        await db.commit()
    except IntegrityError:
        # 同じ client_id の受領が並走した (上の事前チェックをすり抜けた)。
        # partial unique が受け止めるので、こちらは既存行を返して引き下がる。
        await db.rollback()
        _discard_audio()
        if client_uuid is None:
            raise
        existing = await _find_by_client_id(db, staff_id=staff_id, client_id=client_uuid)
        if existing is None:  # pragma: no cover - unique 以外の IntegrityError
            raise
        response.status_code = status.HTTP_200_OK
        return await _serialize_one(db, existing)
    except Exception:
        _discard_audio()
        raise
    await db.refresh(row)

    # 受領のついでに残骸を掃除する (専用 cron を増やさない・設計 §10-4)。
    # **リクエストのセッションでは回さない**: 掃除は自前で commit するので、
    # 同じセッションで呼ぶと受領のトランザクションを巻き込む (掃除が転べば
    # rollback が受領ごと消す)。応答を返した後に別セッションで走らせる。
    background.add_task(reap_stale_jobs_standalone)
    background.add_task(run_transcribe_job, row.id)
    return await _serialize_one(db, row)


# ---------------------------------------------------------------------------
# 一覧 / 詳細


@router.get(
    "",
    response_model=VisitRecordingList,
    summary="音声記録の一覧 (staff は自分の録音のみ・BE 絞り込み + ページング)",
)
async def list_visit_recordings(
    db: DbDep,
    user: CurrentActiveUser,
    patient_id: Annotated[uuid.UUID | None, Query()] = None,
    staff_id: Annotated[uuid.UUID | None, Query()] = None,
    visit_id: Annotated[uuid.UUID | None, Query()] = None,
    office_id: Annotated[uuid.UUID | None, Query()] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    reviewed: Annotated[bool | None, Query()] = None,
    # 1 文字の検索は文字起こし全文にほぼ必ず当たり、絞り込みとして無意味な上に
    # 全件 ilike のスキャンだけ走る。2 文字から受ける。
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    order: Annotated[Literal["recorded_at_desc", "recorded_at_asc"], Query()] = "recorded_at_desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    conditions = [VisitRecording.deleted_at.is_(None)]
    if _is_admin(user):
        if staff_id is not None:
            conditions.append(VisitRecording.staff_id == staff_id)
    else:
        # staff は指定に関わらず自分の録音だけ (staff_id は無視ではなく強制上書き)。
        if user.staff_id is None:
            return {"items": [], "total": 0}
        conditions.append(VisitRecording.staff_id == user.staff_id)
    if patient_id is not None:
        conditions.append(VisitRecording.patient_id == patient_id)
    if visit_id is not None:
        conditions.append(VisitRecording.visit_id == visit_id)
    if office_id is not None:
        # 拠点は 2 通りで当たる: 行に載っている表示スコープ (患者の主担当拠点 →
        # 無ければ録音者の主担当拠点) と、**録音者の今の所属**。前者だけで絞ると、
        # 他拠点の患者を応援で回った録音が担当拠点の一覧から消え、後者だけだと
        # 「その拠点の利用者の記録」が録音者の異動で見えなくなる。
        #
        # 第 2 レグは ``staff.primary_office_id`` = **現在の**所属を見るので、
        # 録音者が異動すると **過去の録音まで新しい拠点の一覧へ移る** (行の
        # office_id は動かないので、患者が付いた録音は旧拠点でも引ける)。
        # 「異動前の記録は旧拠点に残す」のが正なら、録音時の所属を行に持つか
        # 第 2 レグを落とす必要がある — PO 判断待ち (設計 §8-11)。
        conditions.append(
            or_(
                VisitRecording.office_id == office_id,
                VisitRecording.staff_id.in_(
                    select(Staff.id).where(Staff.primary_office_id == office_id)
                ),
            )
        )
    if status_filter:
        conditions.append(VisitRecording.status == status_filter)
    if reviewed is not None:
        # 「確認済み」の正は reviewed_at (reviewed_by は利用者削除で SET NULL)。
        conditions.append(
            VisitRecording.reviewed_at.is_not(None)
            if reviewed
            else VisitRecording.reviewed_at.is_(None)
        )
    # 日付の境界は JST で切り、**必ず UTC へ直してから** 比較する。
    # SQLite は timestamptz のオフセットを落として保存するため、JST のまま
    # 渡すと 9 時間ずれた範囲を引く (PG では正しいので気付きにくい)。
    if from_ is not None:
        conditions.append(VisitRecording.recorded_at >= _jst_day_start_utc(from_))
    if to is not None:
        conditions.append(VisitRecording.recorded_at < _jst_day_start_utc(to + timedelta(days=1)))
    if q:
        # 画面の検索窓は 1 本しか無いので、人が打ちそうな 4 つを同時に見る:
        # 患者名・スタッフ名・要約・文字起こし全文。名前は別テーブルなので
        # join ではなく ``IN (subquery)`` にする — join を足すと ``total`` の
        # count で行が増える (1 録音が複数行に化ける) 事故が起きる。
        pattern = f"%{q}%"
        conditions.append(
            or_(
                VisitRecording.summary_text.ilike(pattern),
                VisitRecording.transcript.ilike(pattern),
                VisitRecording.patient_id.in_(
                    select(Patient.id).where(Patient.name.ilike(pattern))
                ),
                VisitRecording.staff_id.in_(select(Staff.id).where(Staff.name.ilike(pattern))),
            )
        )

    total = int(
        await db.scalar(select(func.count()).select_from(VisitRecording).where(*conditions)) or 0
    )
    # 同じ秒の録音が複数あってもページングがぶれないよう id で決着を付ける
    # (tie-break が無いと offset をまたいだ行が重複 / 欠落する)。並び替えても
    # tie-break の向きを本体に合わせる。
    order_by = (
        (VisitRecording.recorded_at.asc(), VisitRecording.id.asc())
        if order == "recorded_at_asc"
        else (VisitRecording.recorded_at.desc(), VisitRecording.id.desc())
    )
    rows = (
        await db.scalars(
            select(VisitRecording)
            .where(*conditions)
            .order_by(*order_by)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    patients, staff, offices = await _names_for(db, list(rows))
    return {
        "items": [
            _serialize(
                r,
                patient_name=patients.get(r.patient_id) if r.patient_id else None,
                staff_name=staff.get(r.staff_id),
                office_name=offices.get(r.office_id) if r.office_id else None,
                # 一覧では全文を返さない (設計 §10-3)。
                include_transcript=False,
            )
            for r in rows
        ],
        "total": total,
    }


@router.get(
    "/{recording_id}",
    response_model=VisitRecordingRead,
    summary="音声記録の詳細 (文字起こし・要約を含む)",
)
async def get_visit_recording(
    recording_id: uuid.UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict[str, Any]:
    row = await _load_recording_or_404(db, recording_id, user)
    return await _serialize_one(db, row)


async def _audit_read(
    db: AsyncSession,
    request: Request,
    user: User,
    row: VisitRecording,
    *,
    action: str,
    suffix: str,
) -> None:
    """個人情報そのものを返す GET を ``audit_logs`` に明示記録する (設計 §2-6)。

    監査ミドルウェアは GET を記録しない (読み取りの量が桁違いのため)。音声本体
    (``audio_read``) と印刷レポート (``report_read``) は会話の中身がそのまま
    出ていくので、**この 2 本だけ** 例外として自分で 1 行書く。記録に失敗しても
    **配信は止めない** — 監査の都合で現場の閲覧を落とす方が害が大きい。
    """
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = (
        forwarded.split(",", 1)[0].strip()[:64]
        if forwarded
        else (request.client.host[:64] if request.client else None)
    )
    db.add(
        AuditLog(
            actor_user_id=user.id,
            role=normalize_user_role(user.role),
            action=action,
            target_table="visit_recordings",
            target_id=str(row.id)[:64],
            method="GET",
            path=f"/api/v1/visit-recordings/{row.id}{suffix}"[:255],
            status_code=200,
            ip_address=ip_address,
            user_agent=(request.headers.get("user-agent") or None),
        )
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — 監査の失敗で閲覧を落とさない
        logger.exception("voice: %s audit insert failed (swallowed)", action)
        await db.rollback()


@router.get(
    "/{recording_id}/audio",
    response_class=FileResponse,
    summary="音声本体 (Bearer・Range 可・audit_logs に読み取りを明示記録)",
)
async def get_visit_recording_audio(
    recording_id: uuid.UUID,
    request: Request,
    db: DbDep,
    user: CurrentActiveUser,
) -> FileResponse:
    row = await _load_recording_or_404(db, recording_id, user)
    if not row.audio_path or row.audio_deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="音声は保持期間を過ぎて削除されています"
        )
    file_path = Path(row.audio_path)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="音声ファイルがありません")

    # 配信に要る値は **監査より前に素の値へ退避する**。``_audit_read`` は commit し、
    # 失敗すれば rollback する — rollback すると ORM の属性は expire 済みになり、
    # そこへ触った瞬間に遅延ロードが走って async では MissingGreenlet で 500 に
    # なる。「監査に失敗しても配信は止めない」という約束が、属性アクセス 1 つで
    # 破れないようにしておく。
    media_type = row.audio_mime or "application/octet-stream"
    filename = file_path.name

    await _audit_read(db, request, user, row, action="audio_read", suffix="/audio")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Accept-Ranges": "bytes", **_NO_STORE},
    )


@router.get(
    "/{recording_id}/report",
    response_model=VisitRecordingReportRead,
    responses={200: {"content": {"text/html": {}}}},
    summary="訪問記録の印刷レポート (A4 縦 HTML・audit_logs に読み取りを明示記録)",
)
async def get_visit_recording_report(
    recording_id: uuid.UUID,
    request: Request,
    response: Response,
    db: DbDep,
    user: CurrentActiveUser,
    fmt: Annotated[Literal["json", "html"], Query(alias="format")] = "json",
) -> HTMLResponse | VisitRecordingReportRead:
    """音声記録 1 件を A4 縦の訪問記録にして返す (read-only・設計 §11-3)。

    可視性は詳細と同じ (``_load_recording_or_404`` = admin と録音した本人だけ。
    他人の録音は 404 で存在ごと秘匿)。中身は要約と会話の全文そのものなので、
    ``audit_logs`` に ``action='report_read'`` を 1 行残し (``audio_read`` と同型)、
    応答には ``Cache-Control: no-store`` を付ける。

    ``format=json`` の ``recording`` は **全文 (``transcript`` /
    ``transcript_json``) を省く** — 同じ本文が ``html`` に入っているので、
    1 応答に会話を 2 回載せない (一覧が全文を返さないのと同じ作法)。全文が要る
    画面は ``html`` を使うか、詳細 (``GET /visit-recordings/{id}``) を叩く。
    """
    row = await _load_recording_or_404(db, recording_id, user)
    payload = await _serialize_one(db, row)
    generated_at = datetime.now(UTC)
    html_doc = render_visit_record_html({**payload, "generated_at": generated_at})

    await _audit_read(db, request, user, row, action="report_read", suffix="/report")

    if fmt == "html":
        return HTMLResponse(html_doc, headers=_NO_STORE)

    # ``_serialize_one(..., include_transcript=False)`` と同じ中身。名前解決
    # (患者 / スタッフ / 拠点) を 2 度引かないために dict を複製して落とす。
    recording = {**payload, "transcript": None, "transcript_json": None}
    response.headers["Cache-Control"] = _NO_STORE["Cache-Control"]
    return VisitRecordingReportRead(
        recording=VisitRecordingRead.model_validate(recording),
        html=html_doc,
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# 更新 (紐付け / 確認済み / 追記)


async def _find_reusable_visit(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    staff_id: uuid.UUID,
    day: date,
    at: time,
) -> Visit | None:
    """その日その患者に **既にある** 自分の訪問を探す (無ければ None)。

    **この API は訪問を作らない** (設計変更 2026-09-18)。録音の紐付けはあくまで
    「録音がどの患者・どの訪問のものか」を記録するだけで、予定を生やす行為とは
    分ける。受け皿の訪問を起こすと、モニター・通知・プール・代替提案・実現性
    チェック・Layer1 まで「その訪問を除外する」処理が波及し、どこか 1 箇所の
    漏れが静かな誤表示になる。見つからなければ ``visit_id`` は NULL のままで、
    録音は患者にだけ紐付く。

    帰属は一覧・詳細と同じ ``_staff_visibility_filter`` で見る (主担当だけに
    絞ると、コース担当フォールバックや同行・VSA 経由で回った訪問を取り逃がし、
    「自分の訪問なのに再利用されない」が起きる)。候補が複数あるときは録音開始
    時刻に最も近いものを選ぶ (午前と午後で 2 回入る患者のため)。
    """
    rows = (
        await db.scalars(
            select(Visit).where(
                Visit.patient_id == patient_id,
                Visit.visit_date == day,
                Visit.deleted_at.is_(None),
                Visit.status != VISIT_STATUS_CANCELLED,
                _staff_visibility_filter(staff_id),
            )
        )
    ).all()
    if not rows:
        return None
    ref = datetime.combine(day, at)
    return min(
        rows,
        # 同着は開始時刻 → id で決定的に決める (実行のたびに別の訪問を選ばない)。
        key=lambda v: (abs(datetime.combine(day, v.start_time) - ref), v.start_time, str(v.id)),
    )


async def _reusable_visit_id_for(
    db: AsyncSession, row: VisitRecording, patient_id: uuid.UUID
) -> uuid.UUID | None:
    """録音の ``recorded_at`` (JST 日付) で既存訪問を探し、その id を返す。"""
    moment = _as_utc(row.recorded_at).astimezone(JST)
    visit = await _find_reusable_visit(
        db,
        patient_id=patient_id,
        staff_id=row.staff_id,
        day=moment.date(),
        at=moment.time(),
    )
    return visit.id if visit is not None else None


@router.patch(
    "/{recording_id}",
    response_model=VisitRecordingRead,
    summary="紐付け (患者 / 訪問) ・確認済み・要約の追記 / 人手修正",
)
async def update_visit_recording(
    recording_id: uuid.UUID,
    payload: VisitRecordingUpdate,
    db: DbDep,
    user: CurrentActiveUser,
) -> dict[str, Any]:
    row = await _load_recording_or_404(db, recording_id, user)
    fields = payload.model_fields_set
    changing_link = "patient_id" in fields or "visit_id" in fields

    # 追記 (末尾に足す) と人手修正 (丸ごと差し替える) は同じ列を奪い合う。
    # 両方来たら適用順で結果が変わる = FE が順序を暗黙に当てにすることになるので、
    # 黙ってどちらかを勝たせず断る。
    if payload.note_append is not None and "summary_text" in fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="note_append と summary_text は同時に指定できません",
        )

    # 紐付けの付け替えは「録音した本人が 24h 以内」か admin だけ (設計 §10-3)。
    if changing_link and not _is_admin(user):
        created_at = _as_utc(row.created_at)
        if row.staff_id != user.staff_id or datetime.now(UTC) - created_at > _RELINK_GRACE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="紐付けの変更は録音から 24 時間以内 (以降は管理者のみ) です",
            )

    previous_patient_id = row.patient_id
    unlink_requested = "visit_id" in fields and payload.visit_id is None

    # 紐付けの付け替え・解除で **訪問そのものには一切触らない** (設計変更
    # 2026-09-18)。録音は訪問に貼られた付箋であって、剥がしても訪問は残る。
    if "visit_id" in fields and payload.visit_id is not None:
        # 訪問を明示的に選んだときは、そちらが正 (患者はその訪問のものになる)。
        visit = await _load_visit_for_user(db, payload.visit_id, user)
        if payload.patient_id is not None and visit.patient_id != payload.patient_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="visit_id の訪問と patient_id が一致しません",
            )
        row.visit_id = visit.id
        row.patient_id = visit.patient_id
    else:
        if unlink_requested:
            # 明示的な紐付け解除 (``visit_id: null``) は常に許す。
            row.visit_id = None
        if "patient_id" in fields:
            if payload.patient_id is None:
                row.visit_id = None
                row.patient_id = None
            else:
                # 録音は「予定」ではなく「起きたことの記録」なので、患者が入院中・
                # 休止中でも紐付けを拒まない (訪問した翌日に非稼働化する例が現実にある。
                # 2026-09-18 レビュー決定)。存在しない/削除済みの患者だけ 404。
                patient = await db.scalar(
                    select(Patient).where(
                        Patient.id == payload.patient_id, Patient.deleted_at.is_(None)
                    )
                )
                if patient is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail="患者が見つかりません"
                    )
                # 患者が変わる (= 付け替え) なら、今の紐付けはもう合わない。
                if previous_patient_id != patient.id:
                    row.visit_id = None
                row.patient_id = patient.id
                # ``visit_id: null`` と同時に来たときは「訪問は要らない」が正。
                # そうでなければ既存訪問だけ探す — 見つからなければ NULL のまま。
                if row.visit_id is None and not unlink_requested:
                    row.visit_id = await _reusable_visit_id_for(db, row, patient.id)

    if changing_link:
        row.office_id = await _resolve_office_id(
            db, patient_id=row.patient_id, staff_id=row.staff_id
        )
        # 表示用の `unlinked` は患者が決まった時点で解除する。
        if row.patient_id is not None and row.status == RECORDING_STATUS_UNLINKED:
            row.status = RECORDING_STATUS_UPLOADED
        elif row.patient_id is None and row.status == RECORDING_STATUS_UPLOADED:
            row.status = RECORDING_STATUS_UNLINKED

    # note_append は現場の追記であって書き直しではないので「確認済み」は維持する
    # (AI の書き直し / summary_text の置換は失効させる)。
    if payload.note_append:
        # 「要約の追記」= 画面用の整形済みテキストの末尾に足す (設計 §2-5)。
        note = payload.note_append.strip()
        if note:
            row.summary_text = f"{row.summary_text}\n{note}" if row.summary_text else note

    if "summary_text" in fields:
        # 要約の人手修正 (PC 詳細ダイアログ・設計 §11-2)。可視性は
        # ``_load_recording_or_404`` が既に絞っている (staff は自分の録音だけ・
        # 他人の録音は 404) ので、ここで足すべき条件は無い。追記と違い **丸ごと
        # 差し替える** ので、「誰がいつ直したか」を必ず残す。
        new_text = (payload.summary_text or "").strip() or None
        row.summary_text = new_text
        row.summary_edited_by = user.id
        row.summary_edited_at = datetime.now(UTC)
        # 「確認済み」は **その内容を承認した** という意味なので、本文が変われば
        # 承認は一度失効させる (承認した文とは別の文が残る状態を作らない)。
        # 同じ PATCH で ``reviewed: true`` が来ていれば、下で立て直す = 直した
        # 本人がその場で承認したことになる。
        row.reviewed_by = None
        row.reviewed_at = None

    if payload.reviewed is not None:
        if payload.reviewed:
            row.reviewed_by = user.id
            row.reviewed_at = datetime.now(UTC)
        else:
            row.reviewed_by = None
            row.reviewed_at = None

    await db.commit()
    await db.refresh(row)
    return await _serialize_one(db, row)


# ---------------------------------------------------------------------------
# 再処理 / 削除 (admin)


@router.post(
    "/{recording_id}/retry",
    response_model=VisitRecordingRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="失敗した音声記録の再処理 (admin)",
)
async def retry_visit_recording(
    recording_id: uuid.UUID,
    db: DbDep,
    background: BackgroundTasks,
    admin: Annotated[User, Depends(require_role("admin"))],
) -> dict[str, Any]:
    row = await _load_recording_or_404(db, recording_id, admin)
    if not row.audio_path or row.audio_deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="音声が残っていないため再処理できません",
        )
    # 人手修正の退避 (``summary['previous_manual']``) と ``summary_edited_*`` の
    # クリアは **ここではやらない**。再処理を頼んだだけでは要約はまだ変わらず、
    # ジョブが失敗すれば人手修正の文がそのまま残る — ここでクリアすると
    # 「人が直した文なのに出所が AI に見える」行ができる。実際に上書きする
    # ``jobs._save_result`` の冒頭でだけ動かす (設計 §11-2)。
    row.status = RECORDING_STATUS_UPLOADED
    row.error_message = None
    row.error_kind = None
    await db.commit()
    await db.refresh(row)

    # 受領時と同じく、ついでに残骸を掃除する (設計 §10-4)。自前セッションで
    # 応答後に走らせるので、掃除の成否は再処理に影響しない。
    background.add_task(reap_stale_jobs_standalone)
    background.add_task(run_transcribe_job, row.id)
    return await _serialize_one(db, row)


@router.delete(
    "/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="音声記録の削除 (soft delete + 音声ファイル削除・admin)",
)
async def delete_visit_recording(
    recording_id: uuid.UUID,
    db: DbDep,
    admin: Annotated[User, Depends(require_role("admin"))],
) -> None:
    row = await _load_recording_or_404(db, recording_id, admin)
    file_path = Path(row.audio_path) if row.audio_path else None
    now = datetime.now(UTC)
    row.deleted_at = now
    if row.audio_path:
        row.audio_path = None
        row.audio_deleted_at = now
    await db.commit()
    if file_path is not None:
        # 行は消えたままにする (写真と同じ: 実ファイルの後始末が失敗しても
        # DB を巻き戻さない)。
        try:
            file_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            logger.warning("voice: failed to unlink %s", file_path)
    return None
