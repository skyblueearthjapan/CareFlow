"""Staff (スタッフ) + secondary offices + shifts + weekly overrides + events + companion assignments.

W1-BE2 (v2 整理): can_double_team / home_address / home_lat / home_lng /
areas / max_per_day / skill_level / assignment_volume の 8 カラムを削除済み
(設計書 §4.2). 物理 DROP は migration 0010 で実施。

W10-BE1: mentor_id / MentorAssignment 廃止。is_trainee 追加。
Phase 2 (新人同行 v1.1 §3): 旧 staff_companion_assignments 機構を撤去。
同行は accompaniments/accompaniment_defaults (mig 0060 → 0072 で汎化) が唯一の正典。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kana: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # 業務ロール (staff=スタッフ / manager=マネージャー)。**スケジュールエンジン専用**:
    # manager は自動割当の対象外 (埋まらないコースの救済にのみ入る)・人数カウント外。
    # ログインのアカウント権限 (users.role: admin/staff) とは無関係 (二軸分離・2026-08-09)。
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="staff")
    # K-1b: カイポケ18列CSV「職種」列の値 (看護師/准看護師/理学療法士…)。
    # 業務ロールとも独立。カイポケ転記専用。
    qualification: Mapped[str | None] = mapped_column(String(16), nullable=True)

    primary_office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_trainee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    secondary_offices: Mapped[list[StaffSecondaryOffice]] = relationship(
        "StaffSecondaryOffice",
        back_populates="staff",
        cascade="all, delete-orphan",
    )
    shifts: Mapped[list[StaffShift]] = relationship(
        "StaffShift",
        back_populates="staff",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_staff_status_office", "status", "primary_office_id"),
        # W41 後続 cross-review HIGH#3: concurrent import で staff.code 重複が
        # silent に通る race を防ぐ partial UNIQUE INDEX. PostgreSQL では
        # ``deleted_at IS NULL AND code IS NOT NULL`` のみを対象にする
        # (soft-delete 済み / code NULL は除外). SQLite (テスト) でも
        # 同条件の partial UNIQUE が貼られる (SQLite 3.8+ サポート).
        # migration 0032_staff_code_unique_partial が production 側を担保する.
        Index(
            "ix_staff_code_unique_alive",
            "code",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND code IS NOT NULL"),
            sqlite_where=text("deleted_at IS NULL AND code IS NOT NULL"),
        ),
    )


class StaffSecondaryOffice(Base):
    __tablename__ = "staff_secondary_offices"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        primary_key=True,
    )

    staff: Mapped[Staff] = relationship("Staff", back_populates="secondary_offices")


class StaffShift(Base):
    """Fixed weekly shift (7 rows per staff, weekday 0=Mon ... 6=Sun)."""

    __tablename__ = "staff_shifts"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    is_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    staff: Mapped[Staff] = relationship("Staff", back_populates="shifts")


class StaffWeeklyOverride(Base, TimestampMixin):
    """Per-week override (休み or 時間変更) for a single weekday."""

    __tablename__ = "staff_weekly_overrides"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    iso_year: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    override_type: Mapped[str] = mapped_column(String(16), nullable=False)  # off / custom_time
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "staff_id", "iso_year", "iso_week", "weekday", name="uq_staff_week_override"
        ),
        Index("ix_staff_overrides_lookup", "iso_year", "iso_week", "staff_id"),
    )


class StaffEvent(Base, TimestampMixin):
    __tablename__ = "staff_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # イベント取り込み (kaipoke-event-inbound-design.md §2・mig 0062):
    # source='kaipoke' はカイポケ個別業務取り込みが管理する行 (手動='manual' には触れない)。
    # external_id = "{個別業務ID}:{職員内部ID}:{YYYY-MM-DD}" (複合・冪等 upsert キー)。
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="manual", default="manual"
    )
    external_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # 🔒絶対に潰せないイベント (mig 0063): 提案エンジンの2段階フォールバック
    # (パスB=ソフトイベント無視) でも占有として扱い、衝突提案を出さない。
    # 既定 false。カイポケ再取り込みの update はこの列に触れない (保持される)。
    blocking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    __table_args__ = (
        Index("ix_staff_events_when", "staff_id", "starts_at"),
        Index(
            "uq_staff_events_source_external",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
    )


class StaffEventDefault(Base, TimestampMixin):
    """毎週の固定イベント既定 (朝会など・kaipoke-event-two-way-design.md §3-②).

    スタッフ×曜日×時間帯×名称の「型」。週生成系 3 地点
    (週生成 / 割付のみ / 固定枠戻し) から `expand_staff_event_defaults` が
    当該週の staff_events (source='fixed') へ冪等展開する。
    曜日は 0=月〜5=土 (カイポケ個別業務の運用範囲に合わせ日曜は定義不可)。
    """

    __tablename__ = "staff_event_defaults"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=月〜5=土
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 🔒絶対に潰せないイベントとして展開するか (staff_events.blocking へ引き継ぐ)
    blocking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_staff_event_defaults_staff", "staff_id", "weekday"),)


class StaffShiftConfirmation(Base, TimestampMixin):
    """月次の出勤カレンダー確定記録 (staff-shift-confirmation-design.md §1).

    「このスタッフのこの月の出勤/休みを確定として本人へ通知した」1 行。
    再確定 = 同一行の confirmed_at/confirmed_by 更新 + 再通知 (履歴は持たない)。
    """

    __tablename__ = "staff_shift_confirmations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 月初日 (YYYY-MM-01)。API 層で day==1 を検証する (DB CHECK は貼らない)。
    month: Mapped[date] = mapped_column(Date, nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("staff_id", "month", name="uq_staff_shift_confirmation_month"),
    )
