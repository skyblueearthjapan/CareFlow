"""Generic paginated response wrapper — Wave 2-B revise (M7).

Used by integrations list endpoints (Kaipoke jobs / geocoding cache / AI
logs) so clients can render `total` + drive offset-based paging without
relying on naked array length. New endpoints in later waves should adopt
the same shape.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):  # noqa: UP046
    model_config = ConfigDict(extra="forbid")

    items: list[T] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int
