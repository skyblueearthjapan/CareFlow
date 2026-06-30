"""VisitReview (訪問「確認済み」マーク) — QR チェックイン Phase 5-3.

訪問モニターの要対応 (未訪問 / 場所違い / 要確認) を管理者が **visit 単位** で
「確認済み」にして要対応トレイから外すための表。未訪問 (missing) は紐づく
visit_checkins が無いため checkin 単位ではクリアできない → visit 単位の review が
必須 (設計 §5-3 critic C1/C2)。

* ``visit_id`` は監査証跡として **ON DELETE RESTRICT** かつ **unique**
  (1 visit につき高々 1 行。再確認はこの行の upsert で表現する)。
* ``reviewed_by`` は確認者 (User)。人事異動で User 行が消えても記録は残すため
  SET NULL。
* ``reviewed_at`` は確認時刻、``comment`` は確認理由 (任意)。
* undo (取消) はこの行の物理削除で表現する (append-only ではない)。

SQLite 互換: ``revoked_qr_token.py`` と同じ dialect 分岐方針 (model は PG_UUID を
使い、SQLite では String にマップされる)。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VisitReview(Base):
    """訪問「確認済み」マーク (visit_reviews テーブル)."""

    __tablename__ = "visit_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # 1 visit につき高々 1 行 (unique)。監査証跡として RESTRICT。
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "visits.id",
            ondelete="RESTRICT",
            name="fk_visit_reviews_visit_id_visits",
        ),
        nullable=False,
        unique=True,
    )
    # 確認者 (User)。退職等で User 行が消えても記録は残す。
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_visit_reviews_reviewed_by_users",
        ),
        nullable=True,
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
