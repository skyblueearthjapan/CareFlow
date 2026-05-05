"""Pydantic schemas for visit photo metadata.

Upload happens through ``multipart/form-data`` (FastAPI ``UploadFile``)
so there is no JSON-shaped Create schema; only the response and an
optional caption-update schema.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class VisitPhotoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    visit_id: UUID
    mime_type: str
    size_bytes: int
    caption: str | None = None
    uploaded_by: UUID | None = None
    uploaded_at: datetime
    # Convenience URL for the binary download — populated by the route.
    download_url: str


class VisitPhotoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str | None = Field(default=None, max_length=500)
