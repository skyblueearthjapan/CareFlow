"""PatientContact ORM model (患者様の連絡先 / migration 0109).

正典: ``docs/plans/contacts-phone-email-design-2026-11-05.md`` §2・§4。

* 患者 1 人に 0〜3 件 (件数の上限はアプリで守る)。
* ``relation='self'`` (ご本人) は患者 1 人に 1 件まで (部分 UNIQUE)。お名前は持たない。
* ``phone`` は数字だけ (digits)。``phone_display`` は表示用、``phone_kind`` は見分けの結果
  (``app/services/contacts.py`` の ``classify_phone``)。
* 管理者だけが読む・書く (API は ``require_role("admin")``)。
* 患者の論理削除では CASCADE されない (行は残るが、削除済みの患者の API は 404)。

``Patient`` に relationship は張らない (利用側が明示クエリで読む・NG スタッフと同じ方針)。
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PatientContact(Base, TimestampMixin):
    """患者様の連絡先 (patient_contacts テーブル)."""

    __tablename__ = "patient_contacts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # self / family / facility / care_manager / other
    relation: Mapped[str] = mapped_column(String(16), nullable=False)
    # self は NULL (画面は「本人（〇〇 様）」と自動で出す)。
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 続柄・施設名。
    relation_note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phone_display: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_notify_target: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # sms / email (選んだ手段が使えなければ使える方へ落とす = effective_channel)。
    notify_channel: Mapped[str] = mapped_column(
        String(8), nullable=False, default="sms", server_default="sms"
    )
    # unconfirmed / confirmed / ng
    consent_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unconfirmed", server_default="unconfirmed"
    )
    # confirmed / ng になった日 (JST・サーバーが入れる)。
    consent_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # manual / excel
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default="manual"
    )

    __table_args__ = (
        # mig 0109 と同じ CHECK (命名規約で ck_patient_contacts_<name> になる)。
        CheckConstraint(
            "relation IN ('self','family','facility','care_manager','other')", name="relation"
        ),
        CheckConstraint("notify_channel IN ('sms','email')", name="notify_channel"),
        CheckConstraint(
            "consent_status IN ('unconfirmed','confirmed','ng')", name="consent_status"
        ),
        CheckConstraint("source IN ('manual','excel')", name="source"),
        # ご本人は患者 1 人に 1 件まで。
        Index(
            "uq_patient_contacts_self",
            "patient_id",
            unique=True,
            postgresql_where=text("relation = 'self'"),
            sqlite_where=text("relation = 'self'"),
        ),
    )
