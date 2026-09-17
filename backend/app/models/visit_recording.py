"""訪問の音声記録 (録音 → 文字起こし → 要約) — 1 行 = 1 録音.

正典設計書: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §2-4 / §10-2
(migration ``0086_visit_recordings``)。

音声バイナリ本体は DB に入れず ``VISIT_AUDIO_DIR`` 配下のファイルに置き
(``{yyyy}/{mm}/{id}.{ext}``)、この行は **メタ + 文字起こし + 要約** だけを持つ。
``visit_photos`` と同じ作法だが、保持期間パージで音声だけ先に消える点が違う
(``audio_path`` を NULL にし ``audio_deleted_at`` を立てる。文字起こし・要約は
訪問と同じ寿命で残す)。

``visit_id`` / ``patient_id`` は **NULL 可**。QR 無しの導線や、録音してから
患者を選ぶ運用のために「紐付け待ち」の状態 (``status='unlinked'``) を許す。

処理の進行は専用のジョブテーブルを作らず ``status`` + ``updated_at`` で表す
(設計 §2-4)。``transcribing`` のまま ``VOICE_JOB_STALE_MINUTES`` を過ぎた行は
``app.services.voice.jobs.reap_stale_jobs`` が ``failed`` にする。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.patient import JSONBish

# ``status`` の値 (設計 §10-2)。``unlinked`` は patient_id が NULL の間の表示用で、
# 処理の進み方は ``uploaded`` と同じ。
RECORDING_STATUS_UPLOADED = "uploaded"
RECORDING_STATUS_TRANSCRIBING = "transcribing"
RECORDING_STATUS_SUMMARIZED = "summarized"
RECORDING_STATUS_FAILED = "failed"
RECORDING_STATUS_UNLINKED = "unlinked"

RECORDING_STATUSES: frozenset[str] = frozenset(
    {
        RECORDING_STATUS_UPLOADED,
        RECORDING_STATUS_TRANSCRIBING,
        RECORDING_STATUS_SUMMARIZED,
        RECORDING_STATUS_FAILED,
        RECORDING_STATUS_UNLINKED,
    }
)


class VisitRecording(Base, TimestampMixin):
    __tablename__ = "visit_recordings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 紐付け待ちは NULL (訪問が消えても録音は残す = SET NULL)。
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 録音者。誰が録ったかは監査の要なので消さない (RESTRICT)。
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 表示のスコープ (患者の主担当拠点 → 無ければ録音者の主担当拠点)。
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 端末時刻 (サーバー受領時刻は created_at が持つ)。
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 音声ファイル。パージ後は audio_path / audio_bytes を NULL にし
    # audio_deleted_at を立てる (audio_mime は「何だったか」の記録として残す)。
    audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 受領時に端末が名乗った MIME (audio_mime は正規化後)。
    device_mime: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RECORDING_STATUS_UPLOADED
    )

    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    # セグメント (開始秒・話者・本文) の配列。
    transcript_json: Mapped[list | None] = mapped_column(JSONBish, nullable=True)
    # 構造化要約 (主訴・様子 / バイタル / 処置・ケア / 申し送り / 次回 / free)。
    summary: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 要約の人手修正の痕跡 (migration 0087)。``summary_text`` は AI が書いた文でも
    # 人が直した文でもあるので、「誰がいつ直したか」を併せて持つ。再処理で AI が
    # 書き直したらこの 2 本はクリアし、消える人手修正は
    # ``summary['previous_manual']`` へ退避する (設計 §11-2)。
    summary_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary_edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 監査・再実行のための出所 (どのモデル・どのプロンプトで作ったか)。
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 1 件 ≈ $0.0016。pricing の丸め桁 (0.000001) をそのまま格納する。
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)

    # 画面に出す日本語の固定文言 (人が読む用)。
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 失敗の種別 (機械が読む用)。config / auth / timeout / http / parse /
    # too_large / audio_missing / stale。文言を日本語に固定した代わりに、
    # 「何で落ちたか」はこの列で追う。
    error_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # 受領の冪等キー (端末が 1 録音につき 1 個発行する UUID)。同じ値の 2 回目の
    # POST は新しい行を作らず既存行を返す (staff_id と組で partial unique)。
    client_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # 録音同意チェック (画面で必ず取る。false の受領は API 層で 422)。
    consent_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 一覧の既定順 (自分の録音を新しい順)。
        Index("ix_visit_recordings_staff_recorded", "staff_id", text("recorded_at DESC")),
        # 患者詳細カード (その患者の録音を新しい順)。
        Index("ix_visit_recordings_patient_recorded", "patient_id", text("recorded_at DESC")),
        # stale 掃除 / 失敗一覧。
        Index("ix_visit_recordings_status", "status"),
        # 訪問詳細カード。生きている行しか引かないので partial。
        Index(
            "ix_visit_recordings_visit",
            "visit_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # 受領の冪等キー。client_id が NULL の行 (手 seed / 旧 FE) と削除済みの行は
        # 制約の外 — 削除した録音と同じ client_id の再送は新しい行として受け取る
        # (墓石と衝突して受領が 500 で落ちるのを避ける)。
        Index(
            "uq_visit_recordings_staff_client",
            "staff_id",
            "client_id",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL AND deleted_at IS NULL"),
            sqlite_where=text("client_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
