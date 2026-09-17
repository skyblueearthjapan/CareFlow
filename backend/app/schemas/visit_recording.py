"""訪問の音声記録 (visit_recordings) の Pydantic スキーマ.

契約の正典: ``docs/plans/visit-voice-record-design-2026-09-17.md`` §10-3。

受領 (``POST``) は ``multipart/form-data`` なので Create スキーマは無い
(``visit_photos`` と同じ作法)。一覧では ``transcript`` / ``transcript_json`` を
**省略する** (全文は詳細でだけ返す — 一覧のペイロードが膨らむのを防ぐ)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VisitRecordingRead(BaseModel):
    # ``model`` (= 生成に使った AI モデル名) は pydantic の保護名前空間
    # ``model_`` と衝突するため、警告を止める意味で明示的に空にする。
    model_config = ConfigDict(from_attributes=True, extra="ignore", protected_namespaces=())

    id: UUID
    visit_id: UUID | None = None
    patient_id: UUID | None = None
    patient_name: str | None = None
    staff_id: UUID
    staff_name: str | None = None
    office_id: UUID | None = None
    office_name: str | None = None

    recorded_at: datetime
    ended_at: datetime | None = None
    duration_sec: int

    status: str
    has_audio: bool
    audio_mime: str | None = None
    audio_bytes: int | None = None

    # 一覧では省略 (詳細のみ)。
    transcript: str | None = None
    transcript_json: list[Any] | None = None

    summary: dict[str, Any] | None = None
    summary_text: str | None = None
    # 要約の人手修正の痕跡 (0087)。NULL = AI のまま (画面の「修正済み」表示用)。
    summary_edited_by: UUID | None = None
    summary_edited_at: datetime | None = None

    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    # 画面に出す日本語の固定文言 / 機械が読む種別 (§10-3)。
    error_message: str | None = None
    error_kind: str | None = None
    # 受領の冪等キー (FE が再送の同一判定に使う)。
    client_id: UUID | None = None

    consent_confirmed: bool
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None

    created_at: datetime
    updated_at: datetime


class VisitRecordingList(BaseModel):
    """一覧レスポンス (BE ページング)。``total`` は絞り込み後の総件数。"""

    model_config = ConfigDict(extra="forbid")

    items: list[VisitRecordingRead]
    total: int


class VisitRecordingUpdate(BaseModel):
    """紐付け・確認済み・要約の追記 / 人手修正 (設計 §10-3 PATCH ＋ §11-2)。

    ``patient_id`` / ``visit_id`` は **明示的な null** で紐付けを外せるよう
    ``model_fields_set`` で「送られたか」を見分ける (既定値 None と区別する)。

    ``note_append`` (末尾に足す) と ``summary_text`` (丸ごと差し替える) は別物。
    前者は現場の追記、後者は PC の詳細ダイアログでの人手修正で、後者は
    ``summary_edited_by`` / ``summary_edited_at`` を立てる。同じ列を奪い合うので
    **同時指定は 422** (API 層で弾く)。

    ``summary_text`` を変えると「確認済み」(``reviewed_*``) は失効する — 承認は
    その内容に対するものなので、別の文が承認済みのまま残る状態を作らない。同じ
    PATCH に ``reviewed: true`` を添えれば、直した本人がその場で承認できる。
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: UUID | None = None
    visit_id: UUID | None = None
    reviewed: bool | None = None
    note_append: str | None = Field(default=None, max_length=2000)
    # 要約の人手修正 (admin または録音した本人)。空文字 / null は「要約を消す」。
    summary_text: str | None = Field(default=None, max_length=20000)
