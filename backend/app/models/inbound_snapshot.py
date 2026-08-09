"""InboundSnapshot — カイポケ取り込み直前の週の盤面の自動バックアップ.

PO 決定 (2026-08-09): 未来週開放とセットで「間違えて取り込んでも、取り込む前に
戻せる」状態を作る。実適用 (dry_run=false) の直前に、対象週の
visits + スタッフ割当 + コース担当 + 同行リンク(訪問単位) を JSONB へ丸ごと保存し、
「取り込み前に戻す」でワンクリック復元できるようにする。

- 保存は取り込みと同一トランザクション (取り込みが失敗すればスナップショットも残らない)
- 週ごとに直近 5 世代のみ保持 (それ以前は保存時に自動削除)
- 復元は打刻ガード付き (打刻の付いた週は復元不可 = 実績の紐付け先を消さない)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InboundSnapshot(Base):
    """inbound_snapshots テーブル (migration 0068)。"""

    __tablename__ = "inbound_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    # どの取り込み経路の直前か: 'diff' | 'replace' | 'smart'
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # {"visits": [...], "assignments": [...], "accompaniments": [...], "courses": [...]}
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # 一覧表示用 (payload を開かずに規模が分かる)
    visits_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_inbound_snapshots_week_start", "week_start"),)
