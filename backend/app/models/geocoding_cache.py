"""Address -> lat/lng geocoding cache (Phase 5-1, P-06).

The address itself is hashed (sha256) and stored as the unique key, so we
never look up the same string twice and avoid storing the raw address as the
primary key (PII friendliness + bounded index size).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class GeocodingCache(Base, TimestampMixin):
    __tablename__ = "geocoding_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    address_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    lat: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    lng: Mapped[float] = mapped_column(Numeric(10, 7), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="google")
    looked_up_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
