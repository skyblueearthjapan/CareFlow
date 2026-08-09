"""Visit photo endpoints (Wave 4-D).

Routes (all prefixed by ``/api/v1`` and registered under the ``visits``
router include path so the URL is ``/visits/{visit_id}/photos``):

  * ``POST   /visits/{visit_id}/photos``                  upload (multipart)
  * ``GET    /visits/{visit_id}/photos``                  list
  * ``DELETE /visits/{visit_id}/photos/{photo_id}``       delete (admin/manager)
  * ``GET    /visits/{visit_id}/photos/{photo_id}/download``  binary

RBAC mirrors ``visits.py``:
  * staff role can upload/list only on visits they are assigned to.
  * admin/manager can upload/list/delete anywhere.

Storage layout (host bind-mount, see ``.env.example``):
  ``${VISIT_PHOTOS_DIR}/{visit_id}/{photo_uuid}.{ext}``
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.deps import CurrentActiveUser, DbDep, require_role
from app.models.user import User, normalize_user_role
from app.models.visit import Visit
from app.models.visit_photo import VisitPhoto
from app.schemas.visit_photo import VisitPhotoRead

router = APIRouter()

# Mirror existing settings idiom (KAIPOKE_EXPORT_DIR is read straight off
# `os.environ`-style settings) — VISIT_PHOTOS_DIR has the same shape so we
# read it directly to avoid touching `core/config.py` for a single bool.
_DEFAULT_PHOTOS_DIR = "/opt/carelink/data/visit_photos"
_ALLOWED_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


def _photos_root() -> Path:
    return Path(os.environ.get("VISIT_PHOTOS_DIR", _DEFAULT_PHOTOS_DIR))


def _download_url(visit_id: uuid.UUID, photo_id: uuid.UUID) -> str:
    return f"/api/v1/visits/{visit_id}/photos/{photo_id}/download"


def _serialize(photo: VisitPhoto) -> dict:
    return {
        "id": photo.id,
        "visit_id": photo.visit_id,
        "mime_type": photo.mime_type,
        "size_bytes": photo.size_bytes,
        "caption": photo.caption,
        "uploaded_by": photo.uploaded_by,
        "uploaded_at": photo.uploaded_at,
        "download_url": _download_url(photo.visit_id, photo.id),
    }


async def _load_visit_or_404(db, visit_id: uuid.UUID) -> Visit:
    visit = await db.scalar(select(Visit).where(Visit.id == visit_id, Visit.deleted_at.is_(None)))
    if visit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return visit


def _check_visit_access(user: User, visit: Visit) -> None:
    """Enforce same staff-visibility as ``visits.get_visit``."""
    if normalize_user_role(user.role) not in {"admin", "staff"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    if user.role == "staff":
        if user.staff_id is None or user.staff_id not in {
            visit.primary_staff_id,
            visit.secondary_staff_id,
            visit.mentor_staff_id,
        }:
            # Hide existence to mirror visits.py behaviour.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get(
    "/{visit_id}/photos",
    response_model=list[VisitPhotoRead],
    summary="List photos for a visit",
)
async def list_visit_photos(
    visit_id: uuid.UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> list[dict]:
    visit = await _load_visit_or_404(db, visit_id)
    _check_visit_access(user, visit)

    rows = (
        await db.scalars(
            select(VisitPhoto)
            .where(VisitPhoto.visit_id == visit_id)
            .order_by(VisitPhoto.uploaded_at.desc())
        )
    ).all()
    return [_serialize(p) for p in rows]


@router.post(
    "/{visit_id}/photos",
    response_model=VisitPhotoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a photo for a visit (multipart/form-data)",
)
async def upload_visit_photo(
    visit_id: uuid.UUID,
    db: DbDep,
    user: CurrentActiveUser,
    file: Annotated[UploadFile, File(...)],
    caption: Annotated[str | None, Form()] = None,
) -> dict:
    visit = await _load_visit_or_404(db, visit_id)
    _check_visit_access(user, visit)

    mime = (file.content_type or "").lower()
    if mime not in _ALLOWED_MIME:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG / PNG / WebP images are accepted",
        )

    # Stream-read with a hard cap so a hostile client cannot exhaust RAM.
    contents = await file.read(_MAX_BYTES + 1)
    if len(contents) > _MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {_MAX_BYTES // 1024 // 1024} MiB limit",
        )
    if len(contents) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty",
        )
    if caption is not None and len(caption) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="caption must be ≤500 characters",
        )

    photo_id = uuid.uuid4()
    ext = _ALLOWED_MIME[mime]
    target_dir = _photos_root() / str(visit_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{photo_id}{ext}"

    # Write to a temp file then rename to keep partial writes off the
    # listing path even if the process is killed mid-write.
    tmp_path = target_path.with_suffix(target_path.suffix + ".part")
    try:
        with tmp_path.open("wb") as fh:
            fh.write(contents)
        os.replace(tmp_path, target_path)
    except OSError as exc:
        # Best-effort cleanup; surface 500 so the client can retry.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist file: {exc}",
        ) from exc

    row = VisitPhoto(
        id=photo_id,
        visit_id=visit_id,
        file_path=str(target_path),
        mime_type=mime,
        size_bytes=len(contents),
        caption=caption,
        uploaded_by=user.id,
        uploaded_at=datetime.now(UTC),
    )
    db.add(row)
    try:
        await db.commit()
    except Exception:
        # Roll back the disk write to keep DB and FS in sync.
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    await db.refresh(row)
    return _serialize(row)


@router.delete(
    "/{visit_id}/photos/{photo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a photo (admin/manager)",
)
async def delete_visit_photo(
    visit_id: uuid.UUID,
    photo_id: uuid.UUID,
    db: DbDep,
    _user: Annotated[User, Depends(require_role("admin"))],
) -> None:
    row = await db.scalar(
        select(VisitPhoto).where(VisitPhoto.id == photo_id, VisitPhoto.visit_id == visit_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    file_path = Path(row.file_path)
    await db.delete(row)
    await db.commit()
    # Best-effort: leave the DB row gone even if the file cleanup fails
    # (e.g. external NFS hiccup). A nightly sweeper can collect orphans.
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


@router.get(
    "/{visit_id}/photos/{photo_id}/download",
    response_class=FileResponse,
    summary="Download the photo binary",
)
async def download_visit_photo(
    visit_id: uuid.UUID,
    photo_id: uuid.UUID,
    db: DbDep,
    user: CurrentActiveUser,
) -> FileResponse:
    visit = await _load_visit_or_404(db, visit_id)
    _check_visit_access(user, visit)

    row = await db.scalar(
        select(VisitPhoto).where(VisitPhoto.id == photo_id, VisitPhoto.visit_id == visit_id)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    file_path = Path(row.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Photo file is missing on disk",
        )
    return FileResponse(
        path=str(file_path),
        media_type=row.mime_type,
        filename=file_path.name,
    )
