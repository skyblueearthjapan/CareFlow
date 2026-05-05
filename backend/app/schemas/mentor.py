"""Schemas for mentor assignment (`Staff.mentor_id` + `mentor_assignments`).

The current "who is my mentor?" view is driven by the simple
`Staff.mentor_id` self-referential FK, which the frontend
`staff/[id]/page.tsx` メンター割付セクション uses. The richer
`mentor_assignments` table (history / dated pairs) is preserved untouched
and may back a separate endpoint later.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MentorRead(BaseModel):
    """Current mentor pointer for a staff member."""

    model_config = ConfigDict(extra="forbid")

    staff_id: UUID
    mentor_staff_id: UUID | None = None
    mentor_staff_name: str | None = None


class MentorAssign(BaseModel):
    """Body of PUT /staff/{staff_id}/mentor."""

    model_config = ConfigDict(extra="forbid")

    mentor_staff_id: UUID | None = None
