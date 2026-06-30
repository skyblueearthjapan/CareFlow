"""VisitCheckin (訪問チェックイン記録) — append-only 監査ログ.

QR 訪問チェックイン Phase 1 (migration 0041). 1 訪問 (``visits`` 行) に対し
到着 / 退出 / 未訪問の打刻を **追記専用** で紐付ける。再スキャンは新しい行を
追加し、読み取りは kind ごとに ``ORDER BY scanned_at DESC LIMIT 1`` で最新を
採用する (= 行を更新しない)。

監査証跡を守るため ``visit_id`` / ``patient_id`` は **ON DELETE RESTRICT**
(設計 R2)。打刻者 (``staff_id``) と確認者 (``reviewed_by``) は SET NULL で
人事異動時にも記録自体は残す。

判定 (``match_status`` / ``distance_m``) は ``app/services/checkin/judge.py``
がサーバ側で確定し、その時点のしきい値を ``threshold_snapshot`` に保存する
(遡及変更しない)。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# Cross-dialect JSONB: PostgreSQL gets native JSONB, SQLite (tests) gets JSON.
JSONBish = JSONB().with_variant(JSON(), "sqlite")


class VisitCheckin(Base, TimestampMixin):
    """訪問チェックイン (visit_checkins テーブル, append-only)."""

    __tablename__ = "visit_checkins"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # 監査証跡を守るため RESTRICT (visit はソフト削除のみの既存方針と整合).
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # QR が指す (= 検証済みの) 患者. visit.patient_id と一致する.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 打刻者 (User.staff_id). 退職等で staff 行が消えても記録は残す.
    staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 'arrival' | 'departure' | 'no_show'
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    # サーバ受信時刻 (JST 換算で「当日」判定・滞在計算に使用).
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 端末時刻 (既存 ``at`` を写像 / オフライン同期で逆転した滞在計算に使用).
    device_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 1), nullable=True)
    # サーバ計算 (haversine_m). GPS or 患者座標が欠ければ NULL.
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(9, 1), nullable=True)

    # 'match' | 'review' | 'mismatch' | 'no_gps' (位置のみ; 時間判定は集計時).
    match_status: Mapped[str] = mapped_column(String(12), nullable=False)
    # 判定に使ったしきい値スナップショット {"v":1, "match_m":..., ...}.
    threshold_snapshot: Mapped[dict] = mapped_column(JSONBish, nullable=False)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 不一致でも強行記録した旗.
    # server_default は dialect 対応 (PG=false / SQLite=0) を func.false() に委ねる
    # (autogenerate ドリフト防止)。
    is_override: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=func.false()
    )
    # 'qr' | 'manual' (Visit.source と混同しないよう改称).
    checkin_source: Mapped[str] = mapped_column(
        String(12), nullable=False, default="qr", server_default=text("'qr'")
    )

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('arrival','departure','no_show')",
            name="ck_visit_checkins_kind",
        ),
        CheckConstraint(
            "match_status IN ('match','review','mismatch','no_gps')",
            name="ck_visit_checkins_match_status",
        ),
        CheckConstraint(
            "checkin_source IN ('qr','manual')",
            name="ck_visit_checkins_checkin_source",
        ),
        # 最新採用クエリ (kind ごと scanned_at DESC) に整合する複合 index.
        Index(
            "ix_visit_checkins_visit_kind_scanned",
            "visit_id",
            "kind",
            text("scanned_at DESC"),
        ),
        Index("ix_visit_checkins_staff_scanned", "staff_id", "scanned_at"),
    )
