"""予定のお知らせ (SMS) の ORM モデル (migration 0110).

正典: ``docs/plans/sms-notify-design-2026-11-16.md`` §5-4・§5-5・§6。

* ``sms_batches``: 1 回の「送る」(週のお知らせ / 変更のお知らせ)。
* ``sms_messages``: 1 通。二重送信を防ぐ鍵 ``dedupe_key`` は UNIQUE。
* ``sms_status_events``: 1 通の状態の経過 (KDDI の値はそのまま ``raw_status`` に残す)。
* ``patient_week_notices``: 患者 × 週の「お伝えした内容」(1 患者 1 週 1 行・版を持つ)。
* ``patient_notice_events``: 「お伝えした内容」の版ごとの記録 (手段・理由・だれが)。

本文と番号は個人の情報。読めるのは管理者だけ (API は ``require_role("admin")``)。
監査ログには本文と番号を書かない。

``contact_id`` に外部キーを張らない: 連絡先の保存は「消して入れ直す」
(``replace_patient_contacts``) ので、外部キーの SET NULL / CASCADE だと保存のたびに
送信の記録から連絡先が外れてしまう。送った時点の続柄と番号は行に写して持つ。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.pending_request import JSONBish

#: 送信の種類 (週のお知らせ / 変更のお知らせ)。
SMS_KINDS: tuple[str, ...] = ("week", "change")
#: 1 通の状態。KDDI の値そのものは ``status_raw`` に残す。
SMS_STATUSES: tuple[str, ...] = (
    "queued",
    "sending",
    "accepted",
    "delivered",
    "failed",
    "unknown",
    "skipped",
)
#: お伝えした手段。
NOTICE_CHANNELS: tuple[str, ...] = ("sms", "phone", "none")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class SmsBatch(Base):
    """1 回の「送る」(sms_batches テーブル)."""

    __tablename__ = "sms_batches"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    patient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segments_est_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(SMS_KINDS)})", name="kind"),
        Index("ix_sms_batches_week_start", "week_start"),
    )


class SmsMessage(Base, TimestampMixin):
    """1 通 (sms_messages テーブル)."""

    __tablename__ = "sms_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sms_batches.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )
    # 外部キーなし (モジュールの説明を参照)。
    contact_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # 送った時点の続柄 (self / family / facility / care_manager / other)。
    relation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 数字だけ (09000000001)。保存の期間を過ぎたら消す (設計 §6)。
    phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segments_est: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    # この版のお知らせ (patient_week_notices.version)。
    notice_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    # KDDI の状態の値そのまま (pending / sent / delivered / failed …)。
    status_raw: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 試験の守り・送れる時間の外などで送らなかった理由 (短い英語の印)。
    skip_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 二重送信を防ぐ鍵: 種類 + 患者 + 連絡先 + 週 + 版。
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # これより前には送らない (同じ番号へ続けて送るときに 600 秒あける)。
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider_msg_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    # 受け付けられなかったときの印と文 (HTTP の番号・KDDI の MGW の番号など)。
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 照会で分かること。
    carrier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    split_num: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    fail_reason_raw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poll_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(SMS_KINDS)})", name="kind"),
        CheckConstraint(f"status IN ({_in(SMS_STATUSES)})", name="status"),
        Index("ix_sms_messages_batch_id", "batch_id"),
        Index("ix_sms_messages_patient_id", "patient_id"),
        Index("ix_sms_messages_status_not_before", "status", "not_before"),
        Index("ix_sms_messages_created_at", "created_at"),
    )


class SmsStatusEvent(Base):
    """1 通の状態の経過 (sms_status_events テーブル)."""

    __tablename__ = "sms_status_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sms_messages.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 短い補足 (エラーの印・照会の結果の要約)。本文と番号は入れない。
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (Index("ix_sms_status_events_message_id", "message_id"),)


class PatientWeekNotice(Base, TimestampMixin):
    """患者 × 週の「お伝えした内容」(patient_week_notices テーブル)."""

    __tablename__ = "patient_week_notices"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    # [{"date": "2026-12-01", "start": "10:00"}, ...] (日・時刻の順)。
    visits: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_channel: Mapped[str] = mapped_column(String(8), nullable=False, default="sms")
    told_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    told_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("patient_id", "week_start", name="uq_patient_week_notices_patient_week"),
        CheckConstraint(f"last_channel IN ({_in(NOTICE_CHANNELS)})", name="last_channel"),
    )


class PatientNoticeEvent(Base):
    """「お伝えした内容」の版ごとの記録 (patient_notice_events テーブル)."""

    __tablename__ = "patient_notice_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    notice_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patient_week_notices.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    # 送らずに済ませたときの理由 (電話でお伝えした・ご本人からのお申し出 など)。
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visits: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    # 変更に気づいた時刻 (変更のお知らせ・段階 2)。週のお知らせは NULL。
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    told_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    told_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("sms_batches.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(SMS_KINDS)})", name="kind"),
        CheckConstraint(f"channel IN ({_in(NOTICE_CHANNELS)})", name="channel"),
        Index("ix_patient_notice_events_patient_week", "patient_id", "week_start"),
    )
