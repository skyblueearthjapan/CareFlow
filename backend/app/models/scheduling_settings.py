"""SchedulingSettings ORM model (Phase G-88 Step2).

自動最適化ルールの事業所別設定 (事業所 = DB 単位の単一行).

* シングルトン: ``is_singleton = true`` の行を 1 行のみ持つ運用. 部分 UNIQUE
  制約 (PG) で重複 true 行を構造的に防ぐ (詳細は migration を参照).
* 各設定列は **nullable** (NULL = コード既定値 fallback). 既定値の出所は
  ``scheduling.constants`` の ``DEFAULT_*`` で、合成は
  ``services.scheduling.config.build_scheduling_config`` が行う.
* CHECK 制約で範囲を担保: buffer 0..30 / speed 10..40 / lunch 30..90 /
  capacity 1..10 / lunch_window_start < lunch_window_end /
  business_start < business_end.

⚠️ この Step では最適化挙動は変えない (Step3 で ``SchedulingConfig`` を注入).
本テーブルは設定値を保存・読み出す箱にすぎない.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# シングルトン部分 UNIQUE の述語 (dialect ごとの真偽リテラル差を吸収).
_IS_SINGLETON_TRUE_PG = text("is_singleton = true")
_IS_SINGLETON_TRUE_SQLITE = text("is_singleton = 1")


class SchedulingSettings(Base):
    """自動最適化設定 (scheduling_settings テーブル, シングルトン 1 行)."""

    __tablename__ = "scheduling_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # シングルトン制御フラグ. 常に true の行を 1 行のみ持つ.
    # 部分 UNIQUE 制約 (uq_scheduling_settings_singleton) で重複 true 行を防止.
    is_singleton: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=func.true(),
    )

    # ① 訪問間バッファー (分). NULL = 既定 8. 範囲 0..30.
    visit_buffer_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ② 移動速度 (km/h). NULL = 既定 20. 範囲 10..40.
    # 小数点以下 1 桁まで許容 (例: 22.5). config 側で float に正規化.
    travel_speed_kmh: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1),
        nullable=True,
    )

    # ③a 昼休み・標準の長さ (分). NULL = 既定 60. 範囲 30..90.
    lunch_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ③b 昼休み・取得時間帯 (開始 / 終了). NULL = 既定 11:30 / 13:30.
    lunch_window_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    lunch_window_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    # ④ 営業時間 (開始 / 終了). NULL = 既定 09:30 / 18:00.
    business_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    business_end: Mapped[time | None] = mapped_column(Time, nullable=True)

    # ⑤ 1 コース最大人数. NULL = 既定 6. 範囲 1..10.
    max_patients_per_course: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
        # 範囲 CHECK (NULL は CHECK をすり抜ける = 既定 fallback と整合).
        CheckConstraint(
            "visit_buffer_min IS NULL OR (visit_buffer_min >= 0 AND visit_buffer_min <= 30)",
            name="buffer_range",
        ),
        CheckConstraint(
            "travel_speed_kmh IS NULL OR (travel_speed_kmh >= 10 AND travel_speed_kmh <= 40)",
            name="speed_range",
        ),
        CheckConstraint(
            "lunch_duration_min IS NULL OR (lunch_duration_min >= 30 AND lunch_duration_min <= 90)",
            name="lunch_duration_range",
        ),
        CheckConstraint(
            "max_patients_per_course IS NULL "
            "OR (max_patients_per_course >= 1 AND max_patients_per_course <= 10)",
            name="capacity_range",
        ),
        # 時刻の前後関係 (両方非 NULL の場合のみ強制).
        CheckConstraint(
            "lunch_window_start IS NULL OR lunch_window_end IS NULL "
            "OR lunch_window_start < lunch_window_end",
            name="lunch_window_order",
        ),
        CheckConstraint(
            "business_start IS NULL OR business_end IS NULL OR business_start < business_end",
            name="business_hours_order",
        ),
        # シングルトン: is_singleton = true の行は 1 行のみ.
        Index(
            "uq_scheduling_settings_singleton",
            "is_singleton",
            unique=True,
            postgresql_where=_IS_SINGLETON_TRUE_PG,
            sqlite_where=_IS_SINGLETON_TRUE_SQLITE,
        ),
    )
