"""特別訪問週間 (special visit week) モデル — 設計 §1.

`docs/plans/special-visit-week-design.md`:
患者ごとに期間 (例: 3週間) と目標 (週N回以上・既定5) を設定し、基本の固定訪問は
**そのまま生かしたまま**、カレンダーの曜日セルに ○ を付けて「追加の訪問枠」を週ごとに
プールへ積み、毎週その週だけ配置する「上乗せ型」機能。

設計原則: 恒久パターン (``patient_fixed_visits``) には **一切書き込まない**。
期間が終われば自然に元へ戻る。既存の置換型 ``special_weekly_pattern`` /
``special_week_active`` / PFV mode='special' とは別物で、それらには触れない。
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.patient import JSONBish

# ---- status / kind の定数 (文字列比較の単一ソース) --------------------------
PERIOD_STATUS_ACTIVE: str = "active"
PERIOD_STATUS_ENDED: str = "ended"
PERIOD_STATUS_CANCELLED: str = "cancelled"

MARK_KIND_EXTRA: str = "extra"
MARK_KIND_DISPLACED: str = "displaced"

MARK_STATUS_POOL: str = "pool"
MARK_STATUS_PLACED: str = "placed"
MARK_STATUS_CANCELLED: str = "cancelled"

# 週目標の既定値 (「週N回以上」・期間で一律・週別調整なし)。
DEFAULT_WEEKLY_TARGET: int = 5


class SpecialVisitPeriod(Base, TimestampMixin):
    """特別訪問週間の期間 (患者ごと).

    同一患者で ``status='active'`` の期間は同時に 1 本のみ。DB 制約ではなく
    アプリ層 (API) で担保し、重複作成は 422 を返す (設計 §1)。
    """

    __tablename__ = "special_visit_periods"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 任意起点 (今日から等)。週判定は ISO 週単位で行う。
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 含む (end_date 当日まで対象)。
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 「週N回以上」の目標回数。
    weekly_target: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_WEEKLY_TARGET, server_default="5"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=PERIOD_STATUS_ACTIVE, server_default="active"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'ended', 'cancelled')",
            name="ck_svp_status",
        ),
        CheckConstraint("weekly_target >= 1 AND weekly_target <= 7", name="ck_svp_target"),
        Index("ix_svp_patient_status", "patient_id", "status"),
    )


class SpecialVisitMark(Base, TimestampMixin):
    """週 × 曜日のセル単位マーク (設計 §1 / §2).

    - ``kind='extra'``     … ○ 追加枠。1 セル 1 個 (部分ユニーク索引)。
    - ``kind='displaced'`` … 固定訪問の日単位退避。``displaced_snapshot`` に復元用の
      訪問情報を保持する。未生成週は ``{"pfv": true}`` (復元 = 何もしない・生成が正)。

    「配置済みだが訪問が消えた」自己回復: ``status='placed'`` かつ
    (``placed_visit_id IS NULL`` または訪問が soft-delete 済み) はプール一覧で
    'pool' 扱いに読み替える (書き戻し不要)。
    """

    __tablename__ = "special_visit_marks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    period_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("special_visit_periods.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 非正規化 (プール一覧の join 削減)。period.patient_id と常に一致する。
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    iso_year: Mapped[int] = mapped_column(Integer, nullable=False)
    iso_week: Mapped[int] = mapped_column(Integer, nullable=False)
    # 0=Mon..5=Sat (日曜は対象外)。
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MARK_STATUS_POOL, server_default="pool"
    )
    placed_visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="SET NULL"),
        nullable=True,
    )
    # kind='displaced' のみ。復元用スナップショット:
    #   {"visits": [{"visit_id","start_time","end_time","course_id","course_label",
    #                "primary_staff_id"}]}
    #   未生成週は {"pfv": true}。
    displaced_snapshot: Mapped[dict | None] = mapped_column(JSONBish, nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('extra', 'displaced')", name="ck_svm_kind"),
        CheckConstraint(
            "status IN ('pool', 'placed', 'cancelled')",
            name="ck_svm_status",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 5", name="ck_svm_weekday"),
        # ○ は 1 セル 1 個 (取消済みは除外). 述語は両 dialect 同一 (0062 と同方針)。
        Index(
            "uq_svm_extra_cell",
            "period_id",
            "iso_year",
            "iso_week",
            "weekday",
            "kind",
            unique=True,
            postgresql_where=text("status != 'cancelled' AND kind = 'extra'"),
            sqlite_where=text("status != 'cancelled' AND kind = 'extra'"),
        ),
        Index("ix_svm_week_status", "iso_year", "iso_week", "status"),
        Index("ix_svm_patient", "patient_id"),
    )
