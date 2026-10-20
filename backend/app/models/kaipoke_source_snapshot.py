"""KaipokeSourceSnapshot — 休みと受け入れ枠が読むカイポケの材料 (migration 0098).

正典: ``docs/plans/shared-foundation-leave-acceptance-2026-10-20.md`` §0-2。
``kaipoke_csv_snapshots`` (●未送信・予実比較・送信後の drop_snapshots) とは別の表。

* ``(kind, period_start)`` ごとに 1 行。取り直したら置き換える (履歴は持たない)。
* 失敗 (``failed``) は前の ``ok`` の行を消さない (``services/kaipoke/sources.py``)。
* ``office_id`` は常に NULL (拠点で行を絞らない)。
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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.patient import JSONBish

SOURCE_KINDS = ("plan_csv", "actual_csv", "tasks")
SOURCE_STATUSES = ("ok", "empty", "failed")


class KaipokeSourceSnapshot(Base):
    """kaipoke_source_snapshots テーブル。"""

    __tablename__ = "kaipoke_source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # plan_csv / actual_csv / tasks
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # CSV は月の 1 日、tasks は週の月曜。
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("offices.id", ondelete="SET NULL"), nullable=True
    )
    # CSV は 1 行 1 オブジェクト (ヘッダ名で)、tasks は RPA の個別業務の配列そのまま。
    rows: Mapped[list] = mapped_column(JSONBish, nullable=False, default=list)
    # tasks だけ: RPA が表示できた日 (``week_dates`` の ISO 日付)。NULL = RPA が返さなかった。
    # 月をまたぐ週で表示されなかった日を「未確認」にするのに使う。
    covered_dates: Mapped[list | None] = mapped_column(JSONBish, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # ok / empty (該当なし) / failed
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("kaipoke_jobs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('plan_csv','actual_csv','tasks')", name="ck_kaipoke_source_snapshots_kind"
        ),
        CheckConstraint(
            "status IN ('ok','empty','failed')", name="ck_kaipoke_source_snapshots_status"
        ),
        UniqueConstraint("kind", "period_start", name="uq_kaipoke_source_snapshots_kind_period"),
        Index("ix_kaipoke_source_snapshots_job", "job_id"),
    )
