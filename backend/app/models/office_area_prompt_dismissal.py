"""OfficeAreaPromptDismissal ORM model (W-7 地域ルールの学習).

未カバー地域の「担当エリア登録呼びかけ」を、組織として「今回だけ」で断った City を
記憶する. 同じ City については全ユーザー・全画面で二度と呼びかけない.

設計書 `docs/plans/region-rule-learning-design.md` §2-3.

## カラム

| column     | 意味 |
|------------|------|
| id         | UUID PK |
| city_id    | 却下対象の City (cities.id, UNIQUE = 組織全体で City ごとに 1 行) |
| created_at | 却下日時 (timestamptz, server_default now()) |

## 削除挙動

* city_id は ``ON DELETE CASCADE`` (City の物理削除で却下記憶も消える).
* 担当エリア追加 (POST /offices/{id}/area-cities) 時に該当行をアプリ側で DELETE し、
  登録済み地域に「聞かない」記憶が残らないようにする (設計書 §2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OfficeAreaPromptDismissal(Base):
    """担当エリア登録呼びかけの却下記憶 (office_area_prompt_dismissals テーブル)."""

    __tablename__ = "office_area_prompt_dismissals"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # 組織全体で City ごとに 1 行 (UNIQUE). 命名規約により
    # uq_office_area_prompt_dismissals_city_id が付与される.
    city_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cities.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
