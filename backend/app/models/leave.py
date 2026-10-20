"""休み申請と「休み・勤務表」の表 (migration 0099).

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §2-2・§2-5・§7。

* ``OfficeClosedDay`` — 事業所の休み (年末年始など)。必要休日数に数えるだけで、
  それだけでは誰も休みにならない (§2-2)。
* ``StaffLeaveKaipoke`` — 職員×日付で 1 行。カイポケへ最後に送った／カイポケにあると
  確かめた休みの種類 (``sent_type``) と「照合前」(``baseline_pending``)。休みの送信の状態は
  列に持たず、らく助の休み・``sent_type``・``baseline_pending`` からその場で決める (§2-5)。
* ``KaipokeLeaveJudgement`` — 件名で種類が分からないカイポケの予定に管理者が選んだ種類
  (``content_hash`` ごと・§7-4-3)。
* ``LeaveCompareRun`` — 照合の差分の結果だけ (材料は ``kaipoke_source_snapshots``)。
* ``LeaveMonth`` — 月ごとに 1 行 (勤務すべき時間数・最後に休みを変えた時刻・照合・実績の記録)。
* ``KinmuSheet`` — 作った勤務表 (Google スプレッドシート) の記録。**行を消す API を作らない**
  (5 年間はらく助から消さない・§7-2)。
* ``GoogleDriveConnection`` — Google ドライブの接続 (1 行だけ・§7-3)。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.patient import JSONBish

#: 照合でカイポケから取り込んだ・送った休みの種類 (``app/core/leave.py`` の ``LEAVE_TYPES``)。
_LEAVE_TYPES_SQL = "'off', 'am_off', 'pm_off', 'paid_off'"


def _user_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="SET NULL")


class OfficeClosedDay(Base):
    """事業所の休み (office_closed_days)."""

    __tablename__ = "office_closed_days"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    # 例「年末年始」。
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StaffLeaveKaipoke(Base):
    """休みのカイポケへの送信の記録 (staff_leave_kaipoke)・職員×日付で 1 行."""

    __tablename__ = "staff_leave_kaipoke"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    leave_date: Mapped[date] = mapped_column(Date, nullable=False)
    # カイポケへ最後に送った／カイポケにあると確かめた種類。NULL = 無し。
    sent_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 照合前 (0099 より前からある休み)。その月の照合で決めるまで送らない。
    baseline_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    kaipoke_external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            f"sent_type IS NULL OR sent_type IN ({_LEAVE_TYPES_SQL})", name="sent_type"
        ),
        UniqueConstraint("staff_id", "leave_date"),
    )


class KaipokeLeaveJudgement(Base):
    """件名で種類が分からないカイポケの予定に管理者が選んだ種類 (kaipoke_leave_judgements)."""

    __tablename__ = "kaipoke_leave_judgements"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    # sha256(職員 id ‖ 日付 ‖ 件名 ‖ 開始 HH:MM ‖ 終了 HH:MM) の 16 進 (§7-4-3)。
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    starts: Mapped[time | None] = mapped_column(Time, nullable=True)
    ends: Mapped[time | None] = mapped_column(Time, nullable=True)
    # not_leave / off / am_off / pm_off / paid_off
    chosen_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "chosen_kind IN ('not_leave', " + _LEAVE_TYPES_SQL + ")", name="chosen_kind"
        ),
        UniqueConstraint("content_hash"),
    )


class LeaveCompareRun(Base):
    """照合の差分の結果 (leave_compare_runs)."""

    __tablename__ = "leave_compare_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 月の 1 日。
    month: Mapped[date] = mapped_column(Date, nullable=False)
    kaipoke_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"), nullable=True
    )
    # 使った材料の fetched_at (週ごとの個別業務・実績 CSV)。
    fetched_at: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)
    # 差分の一覧。
    diffs: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    diff_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    planned_send_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    unconfirmed_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_leave_compare_runs_month_created_at", "month", "created_at"),)


class LeaveMonth(Base):
    """月ごとの休みの記録 (leave_months)."""

    __tablename__ = "leave_months"

    # 月の 1 日。
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    # 常勤の従業者が当該月勤務すべき時間数 (勤務表の右上)。
    required_hours: Mapped[float | None] = mapped_column(
        Numeric(5, 1, asdecimal=False), nullable=True
    )
    # その月の override の追加・変更・削除で更新 (set_staff_override の中・§2-1)。
    last_leave_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_compared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_compare_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("leave_compare_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    actual_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )


class KinmuSheet(Base):
    """作った勤務表のファイル (kinmu_sheets)。行を消す API は作らない."""

    __tablename__ = "kinmu_sheets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 月の 1 日。
    month: Mapped[date] = mapped_column(Date, nullable=False)
    # plan (予定版) / final (確定版)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # 第 N 版 (作り直すたびに 1 つ増やす。前の版の行とファイルは残す)。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    drive_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('plan','final')", name="kind"),
        CheckConstraint("version >= 1", name="version"),
        UniqueConstraint("month", "kind", "version"),
    )


class GoogleDriveConnection(Base):
    """Google ドライブの接続 (google_drive_connections・1 行だけ)."""

    __tablename__ = "google_drive_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    is_singleton: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=func.true()
    )
    # Fernet で暗号化したリフレッシュトークン (API では返さない)。
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 保存先フォルダ (Google Picker で選ぶ)。
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    shared_drive_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), _user_fk(), nullable=True
    )
    # connected / needs_reconnect
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="connected", server_default="connected"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('connected','needs_reconnect')", name="status"),
        Index(
            "uq_google_drive_connections_singleton",
            "is_singleton",
            unique=True,
            postgresql_where=text("is_singleton = true"),
            sqlite_where=text("is_singleton = 1"),
        ),
    )
