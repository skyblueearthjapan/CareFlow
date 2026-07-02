"""SuggestionDismissal ORM model (改善提案 MVP P2-A).

改善提案の「却下記憶」を表す. スタッフ/管理者が改善提案を見送った (dismiss) 際に、
同一提案の蒸し返しを防ぐために記録する.

## 同一提案の指紋 = (patient_id, kind, target_weekday)

設計書 docs/plans/p2-improvement-mvp-design.md §1.2:
  - 時刻まで含めると僅差の蒸し返しを防げず、kind だけでは粗すぎるため、
    (patient_id, kind, target_weekday) を指紋とする.
  - 同一指紋の再 dismiss は既存行の upsert (reason 更新) で扱う (upsert 実装は P2-B).

## カラム

| column         | 意味 |
|----------------|------|
| kind           | 'time_change' / 'day_change' (提案種別) |
| target_weekday | 0=月 … 6=日 (対象曜日) |
| reason         | 'day_immovable' / 'time_immovable' / 'staff_relation' / 'other' |
| reason_note    | 自由記述 (任意) |
| dismissed_by   | 却下したユーザー (users, ON DELETE SET NULL = 監査用) |
| dismissed_at   | 却下日時 |
| expires_at     | 失効日時 (NULL = 無期限) |

## 削除挙動

* patient_id は ``ON DELETE CASCADE`` (患者の物理削除で却下記憶も消える).
* dismissed_by は ``ON DELETE SET NULL`` (ユーザー削除で NULL 化 = 記録は残す).

SQLite 互換: ``patient_same_address_link.py`` と同じ dialect 分岐方針
(UUID → String(36) フォールバック, CHECK 制約).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SuggestionDismissalKind = Literal["time_change", "day_change"]
SuggestionDismissalReason = Literal[
    "day_immovable", "time_immovable", "staff_relation", "other"
]


class SuggestionDismissal(Base):
    """改善提案の却下記憶 (suggestion_dismissals テーブル)."""

    __tablename__ = "suggestion_dismissals"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 'time_change' / 'day_change'. 値域は CHECK ck_sd_kind で拘束.
    kind: Mapped[SuggestionDismissalKind] = mapped_column(String(16), nullable=False)
    # 対象曜日 0=月 … 6=日. CHECK ck_sd_weekday で拘束.
    target_weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # 'day_immovable' / 'time_immovable' / 'staff_relation' / 'other'. CHECK ck_sd_reason.
    reason: Mapped[SuggestionDismissalReason] = mapped_column(String(24), nullable=False)
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 却下したユーザー. 監査用に ON DELETE SET NULL (削除で NULL 化, 記録は残す).
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    dismissed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # NULL = 無期限.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('time_change','day_change')",
            name="ck_sd_kind",
        ),
        CheckConstraint(
            "reason IN ('day_immovable','time_immovable','staff_relation','other')",
            name="ck_sd_reason",
        ),
        CheckConstraint(
            "target_weekday BETWEEN 0 AND 6",
            name="ck_sd_weekday",
        ),
        # 指紋 (patient_id, kind, target_weekday) の UNIQUE 制約.
        # UNIQUE index を兼ねるため別途 Index 不要.
        # dismiss upsert の TOCTOU 競合を DB 側で防ぐ.
        UniqueConstraint(
            "patient_id",
            "kind",
            "target_weekday",
            name="uq_sd_fingerprint",
        ),
    )
