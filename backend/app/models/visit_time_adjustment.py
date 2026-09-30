"""VisitTimeAdjustment (実績の時刻の調整) — 追記専用.

正典設計書: ``docs/plans/actual-time-adjust-design-2026-09-30.md`` §4 (migration 0088)。

QR は家に入ってから読むので、記録上の到着が実際より遅くなることがある。スタッフ
または管理者が実績の時刻を実際に合わせた記録を 1 行ずつ **追記** する
(``visit_checkins`` と同じく行を更新しない)。

* どの調整が効くか: (visit, kind) ごとに ``created_at DESC, id DESC`` の先頭 1 行。
  その行の ``adjusted_at`` が NULL でなく、同じ kind の最新の打刻より後に作られて
  いれば有効。同じ読み取りの再送 (最新の打刻と ``base_checkin_id`` の打刻の
  ``device_time`` が一致) は打刻し直しに数えない
  (``services/checkin/actuals.py`` がこの規則の唯一の実装)。
* ``adjusted_at`` が NULL の行 = 「読取時刻に戻す」操作の記録。
* ``base_checkin_id`` が NULL の退出 = 読み取りの無い退出を手で入れたもの。
  その行には、手で入れる前の ``visits.status`` (と予定外訪問の ``end_time``) を
  ``prev_visit_status`` / ``prev_visit_end_time`` に控える (読取時刻に戻すときに使う)。

予定 (``visits.start_time`` / ``end_time``) はこの表とは無関係で、調整では動かない。
"""

from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, Time, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ADJUST_KINDS: tuple[str, ...] = ("arrival", "departure")

#: 理由コード → 画面に出す名前 (設計 §4)。
ADJUST_REASON_LABELS: dict[str, str] = {
    "intercom_wait": "インターホン待ち",
    "read_later": "読み取りが後になった",
    "no_read": "読み取りなし",
    "other": "その他",
}

ADJUST_SOURCES: tuple[str, ...] = ("mobile", "pc", "checkin")


class VisitTimeAdjustment(Base, TimestampMixin):
    """実績の時刻の調整 (visit_time_adjustments テーブル, append-only)."""

    __tablename__ = "visit_time_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # 監査証跡を守るため RESTRICT (visit_checkins と同じ方針)。
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 'arrival' | 'departure'
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    # 合わせた時刻 (分単位・秒 0)。NULL = 読取時刻に戻す。
    adjusted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 調整の元になった打刻。読み取りの無い退出を手で入れた場合は NULL。
    base_checkin_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visit_checkins.id", ondelete="SET NULL"),
        nullable=True,
    )
    # 'intercom_wait' | 'read_later' | 'no_read' | 'other'
    reason_code: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'mobile' | 'pc' | 'checkin' (打刻リクエストに同梱)
    source: Mapped[str] = mapped_column(String(12), nullable=False)
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
    # 読み取りの無い退出を手で入れた行だけが持つ: 手で入れる前の ``visits.status`` と、
    # 予定外訪問の ``visits.end_time`` (通常の訪問は NULL)。読取時刻に戻すときに、
    # 元から completed だった訪問を in_progress にしない・予定外訪問の ``end_time`` を
    # 元へ戻すために使う。手で入れた退出を続けて合わせ直した行は、最初の値を引き継ぐ。
    prev_visit_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prev_visit_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    __table_args__ = (
        # 名前は ``visit_checkins`` (0041) と同じ流儀で、migration 0088 と同じ文字列を
        # 書く。命名規約 (``ck_%(table_name)s_%(constraint_name)s``) がモデルにも
        # migration (``op.create_table`` は ``target_metadata`` の規約を引き継ぐ) にも
        # 同じ接頭辞を付けるので、PostgreSQL での実名はどちらも
        # ``ck_visit_time_adjustments_ck_visit_time_adjustments_kind`` になる
        # (SQLite のテスト経路では migration 側は素の名前のまま)。
        CheckConstraint(
            "kind IN ('arrival','departure')",
            name="ck_visit_time_adjustments_kind",
        ),
        # 「効いている調整」の取得 (visit, kind ごと created_at DESC) に整合する index。
        Index(
            "ix_visit_time_adjustments_visit_kind_created",
            "visit_id",
            "kind",
            text("created_at DESC"),
        ),
    )
