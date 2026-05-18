"""PatientFixedVisit ORM model (W9-BE1 / W37 Phase 1).

週間訪問パターン (固定枠) を表す行単位テーブル。
スケジュール確定後に book-back され、翌週以降は固定枠から visits を自動生成する。

設計上の制約:
  - mode: 'normal' (通常週) / 'special' (特別週)
  - 同一 (patient_id, mode, weekday, slot_index) は 1 行のみ (UNIQUE 制約)
  - W37 Phase 1: 複数スタッフ対応患者は同曜日・同時刻に 2 コース固定するため、
    slot_index 0/1 の 2 スロットを持てるよう拡張 (既存行は slot_index=0 として扱う)。
  - 1 日複数訪問なし (1 patient × 1 weekday × 1 mode で max 2 行 = slot 0/1)
  - 指名スタッフなし (ローテ前提)
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.office import Office


class PatientFixedVisit(Base):
    """患者固定訪問パターン (1 行 = 1 曜日 × 1 mode)."""

    __tablename__ = "patient_fixed_visits"

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
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # 'normal' or 'special'
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=Mon … 6=Sun
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    # W37 Phase 1: 複数スタッフ対応患者向けに同曜日・同時刻 2 コース固定を許容するための
    # スロットインデックス. 0 or 1 (CHECK で範囲拘束).
    # 既存行は migration 0027 で default 0 に正規化される (案 P).
    slot_index: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    # W22 Phase A: 固定枠が属するコーステンプレート ID.
    #   - NULL の場合は Layer 1 の office フォールバックで解決する (後方互換).
    #   - course_template が削除された場合は SET NULL (固定枠は残るが course は外れる).
    course_template_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("course_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Phase E-5 (項目 ⑥B): サブ拠点機能.
    #   - 主担当拠点 (patient.primary_office_id) とは別に、フォロー時に
    #     固定枠を配置できる拠点を指定する (任意, NULL 可).
    #   - 自動算出本体は patient.primary_office_id のみを見るため
    #     ``sub_office_id`` は基本ロジックに影響しない.
    #   - diff_add の pool 展開でのみ「sub_office にも候補化」する fallback として使う.
    #   - course_template_id と整合: API 層で「``sub_office_id`` が指定された場合、
    #     ``course_template_id`` も sub_office の template を指す」よう検証.
    sub_office_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id"),
        nullable=True,
    )
    sub_office: Mapped[Office | None] = relationship(
        "Office",
        foreign_keys=[sub_office_id],
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # W37 Phase 1: 旧 UNIQUE (patient_id, mode, weekday) を slot_index 込みに拡張.
        UniqueConstraint(
            "patient_id",
            "mode",
            "weekday",
            "slot_index",
            name="uq_pfv_patient_mode_weekday_slot",
        ),
        CheckConstraint(
            "mode IN ('normal','special')",
            name="ck_pfv_mode",
        ),
        CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_pfv_weekday",
        ),
        CheckConstraint(
            "duration_min > 0 AND duration_min <= 480",
            name="ck_pfv_duration",
        ),
        CheckConstraint(
            "slot_index >= 0 AND slot_index <= 1",
            name="ck_pfv_slot_index",
        ),
    )
