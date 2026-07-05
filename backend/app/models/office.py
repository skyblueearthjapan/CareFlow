"""Office (拠点) and Office<->City many-to-many."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Office(Base, TimestampMixin):
    __tablename__ = "offices"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    # K-1b: カイポケ18列CSV「事業所名」列の正式名。name は短縮名 (稲毛/都賀) のため
    # 別途保持する (例: 訪問看護ステーションよりより / (ST)…都賀支店)。NULL時は name で代替。
    kaipoke_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prefecture: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lat: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    lng: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase G-45: 拠点稼働曜日 (0=月..6=日 の int 配列).
    # default [0..5] (月-土). 休業曜日は V2Visit 生成を skip + staff 応援判定で
    # secondary_office にフォールバック.
    # PG では JSONB, SQLite (テスト) では JSON にフォールバック.
    operating_weekdays: Mapped[list[int]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        server_default=text("'[0,1,2,3,4,5]'"),
        default=lambda: [0, 1, 2, 3, 4, 5],
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cities: Mapped[list[OfficeCity]] = relationship(
        "OfficeCity", back_populates="office", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_offices_name", "name"),
        Index(
            "ix_offices_active",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class OfficeCity(Base):
    __tablename__ = "office_cities"

    office_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    city_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    office: Mapped[Office] = relationship("Office", back_populates="cities")
    city: Mapped[City] = relationship("City", back_populates="offices")  # noqa: F821
