"""サインで記録 — 退出のときにもらったサインの画像 (1 行 = 1 枚).

正典設計書: ``docs/plans/signature-checkin-design-2026-10-06.md`` §4・§5-1
(migration ``0090_visit_signatures``)。

QR を置けない利用者さん (または QR を忘れたとき) は、退出のときに利用者さんの
サインをもらい、その時刻と GPS で退出を記録する。退出の打刻そのものは
``visit_checkins`` の 1 行 (``checkin_source='signature'``) で、この表はその打刻に
付いた **サインの画像** を指す (打刻 1 行につき画像 1 枚 = ``checkin_id`` は UNIQUE)。

* 画像本体は DB に入れず ``VISIT_SIGNATURES_DIR`` 配下のファイルに置く
  (``{yyyy}/{mm}/{id}.png``・音声と同じ作法)。
* 署名した人 (ご本人 / ご家族) は記録しない (PO 決定 Q4)。
* 保存は 5 年 (PO 決定 Q5)。過ぎたら画像だけ消し (``image_path=NULL`` +
  ``image_deleted_at``)、行は残す。
* ``sha256`` は受け取った画像のハッシュ (改ざんの確認用)。
* ``client_id`` は端末が 1 回の記録につき 1 個発行する UUID。圏外で退避した記録の
  再送が重なっても 1 件に畳む (NULL は制約の外)。
* 打刻と訪問は監査証跡なので ``visit_checkins`` と同じく **RESTRICT**。作成者は
  人事異動でも記録を残すため SET NULL。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class VisitSignature(Base, TimestampMixin):
    """サインの画像 (visit_signatures テーブル)."""

    __tablename__ = "visit_signatures"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # サインで記録した退出の打刻 (1 打刻 = 1 枚)。
    checkin_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visit_checkins.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    # 画像ファイル。保持期間を過ぎたら NULL にし ``image_deleted_at`` を立てる
    # (``image_mime`` / ``sha256`` は「何だったか」の記録として残す)。
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_mime: Mapped[str] = mapped_column(String(32), nullable=False)
    image_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # 端末の時刻 (サインして押した瞬間)。受け取った時刻は ``created_at``。
    device_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 再送の冪等キー (端末が発行する UUID)。
    client_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    image_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_visit_signatures_visit", "visit_id"),
        Index(
            "uq_visit_signatures_client_id",
            "client_id",
            unique=True,
            postgresql_where=text("client_id IS NOT NULL"),
            sqlite_where=text("client_id IS NOT NULL"),
        ),
    )
