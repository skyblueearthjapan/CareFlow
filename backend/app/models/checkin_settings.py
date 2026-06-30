"""CheckinSettings ORM model (QR 訪問チェックイン Phase 1).

距離 / 精度 / 時間しきい値の **全社一律** 設定 (DB 単位の単一行).
``SchedulingSettings`` を雛形とする:

* シングルトン: ``is_singleton = true`` の行を 1 行のみ持つ. 部分 UNIQUE
  制約 (両 dialect 変種) で重複 true 行を構造的に防ぐ.
* 各設定列は **nullable** (NULL = コード既定値 fallback). 既定値は
  ``app.services.checkin.judge.DEFAULT_THRESHOLDS``.
* CHECK 制約で範囲を担保. 交差 CHECK ``review_gte_match`` で
  ``review_m >= match_m`` を強制 (両方非 NULL 時).

Phase 1 では judge がこのテーブル (無ければ既定値) を直接読む. GET/PUT API は
Phase 4 で追加する.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# シングルトン部分 UNIQUE の述語 (dialect ごとの真偽リテラル差を吸収).
_IS_SINGLETON_TRUE_PG = text("is_singleton = true")
_IS_SINGLETON_TRUE_SQLITE = text("is_singleton = 1")


class CheckinSettings(Base):
    """チェックイン判定しきい値 (checkin_settings テーブル, シングルトン 1 行)."""

    __tablename__ = "checkin_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # シングルトン制御フラグ. 常に true の行を 1 行のみ持つ.
    # Python 側 default=True で ORM insert 時に明示的に値を入れる (SQLite は
    # ``func.true()`` の DB default を評価できないため; migration 側は dialect 別に
    # true / 1 を server_default で設定).
    is_singleton: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=func.true(),
    )

    # 一致しきい値 (m). NULL = 既定 100. 範囲 10..1000.
    match_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 要確認しきい値 (m). NULL = 既定 300. 範囲 10..2000.
    review_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # GPS 精度許容 (m). NULL = 既定 50. 範囲 5..500.
    accuracy_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 未訪問の猶予 (分). NULL = 既定 20. 範囲 0..240.
    no_show_grace_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 遅延しきい値 (分). NULL = 既定 15. 範囲 0..240.
    late_min: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
            "match_m IS NULL OR (match_m >= 10 AND match_m <= 1000)",
            name="ck_checkin_settings_match_m_range",
        ),
        CheckConstraint(
            "review_m IS NULL OR (review_m >= 10 AND review_m <= 2000)",
            name="ck_checkin_settings_review_m_range",
        ),
        CheckConstraint(
            "accuracy_m IS NULL OR (accuracy_m >= 5 AND accuracy_m <= 500)",
            name="ck_checkin_settings_accuracy_m_range",
        ),
        CheckConstraint(
            "no_show_grace_min IS NULL OR (no_show_grace_min >= 0 AND no_show_grace_min <= 240)",
            name="ck_checkin_settings_no_show_grace_range",
        ),
        CheckConstraint(
            "late_min IS NULL OR (late_min >= 0 AND late_min <= 240)",
            name="ck_checkin_settings_late_min_range",
        ),
        # 交差 CHECK: review_m >= match_m (両方非 NULL 時のみ強制).
        CheckConstraint(
            "review_m IS NULL OR match_m IS NULL OR review_m >= match_m",
            name="ck_checkin_settings_review_gte_match",
        ),
        # シングルトン: is_singleton = true の行は 1 行のみ.
        Index(
            "uq_checkin_settings_singleton",
            "is_singleton",
            unique=True,
            postgresql_where=_IS_SINGLETON_TRUE_PG,
            sqlite_where=_IS_SINGLETON_TRUE_SQLITE,
        ),
    )
