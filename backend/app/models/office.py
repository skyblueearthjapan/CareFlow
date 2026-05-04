"""Office (拠点) and Office<->City many-to-many."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Office(Base, TimestampMixin):
    __tablename__ = "offices"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lat: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    lng: Mapped[float | None] = mapped_column(Numeric(10, 7), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cities: Mapped[list["OfficeCity"]] = relationship(
        "OfficeCity", back_populates="office", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_offices_name", "name"),
        Index(
            "ix_offices_active",
            "id",
            postgresql_where=func.coalesce(deleted_at, None).is_(None),
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

    office: Mapped["Office"] = relationship("Office", back_populates="cities")
    city: Mapped["City"] = relationship("City", back_populates="offices")  # noqa: F821
