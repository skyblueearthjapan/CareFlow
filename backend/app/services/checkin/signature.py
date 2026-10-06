"""サインで記録 — サインの画像の検証と保存 (signature-checkin-design-2026-10-06 §4).

画像は DB に入れず ``VISIT_SIGNATURES_DIR/{yyyy}/{mm}/{signature_id}.{png|jpg}`` に置く
(音声記録と同じ作法)。年月は **受け取った日 (JST)** — 端末の時計に保存先を
左右させない。

受け付けるのは PNG と JPEG だけ。形式は端末が名乗る ``Content-Type`` ではなく
ファイル先頭のバイト (magic bytes) で決める。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.services.checkin.judge import JST

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

#: 画像の形式 → (MIME, 拡張子)。
_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("image/png", ".png"),
    "jpeg": ("image/jpeg", ".jpg"),
}

DETAIL_EMPTY = "サインの画像がありません"
DETAIL_NOT_IMAGE = "サインの画像は PNG か JPEG で送ってください"


def detail_too_large(max_bytes: int) -> str:
    return f"サインの画像が大きすぎます（{max_bytes // 1024} KB まで）"


def signatures_root() -> Path:
    """設定を **呼び出しの都度** 読む (テストが tmp_path を差し込めるように)。"""
    return Path(get_settings().visit_signatures_dir)


def sniff_format(data: bytes) -> str | None:
    """先頭のバイトから 'png' / 'jpeg' を返す。どちらでもなければ None。"""
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data.startswith(_JPEG_MAGIC):
        return "jpeg"
    return None


@dataclass
class SignatureImage:
    """検証済みのサインの画像 (まだディスクには書いていない)。"""

    data: bytes
    mime: str
    ext: str
    sha256: str

    @property
    def size(self) -> int:
        return len(self.data)


async def read_signature_image(upload: UploadFile) -> SignatureImage:
    """アップロードを読み、大きさと形式を検証する (413 / 422)。

    1 枚は数十 KB なので丸ごと読む (上限 + 1 バイトで打ち切る)。
    """
    max_bytes = get_settings().visit_signature_max_bytes
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=detail_too_large(max_bytes),
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=DETAIL_EMPTY)
    fmt = sniff_format(data)
    if fmt is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=DETAIL_NOT_IMAGE
        )
    mime, ext = _FORMATS[fmt]
    return SignatureImage(data=data, mime=mime, ext=ext, sha256=hashlib.sha256(data).hexdigest())


def write_signature_file(image: SignatureImage, signature_id: UUID, received_at: datetime) -> Path:
    """画像をディスクに書く (``.part`` に書いてから置き換える)。書いたパスを返す。

    失敗したら ``.part`` を消して 500。DB の commit に失敗したときは、呼び出し側が
    :func:`discard_signature_file` で消す (DB とファイルの食い違いを残さない)。
    """
    day = received_at.astimezone(JST)
    target_dir = signatures_root() / f"{day:%Y}" / f"{day:%m}"
    target = target_dir / f"{signature_id}{image.ext}"
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(image.data)
        os.replace(tmp, target)
    except OSError as exc:
        discard_signature_file(tmp)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="サインの画像を保存できませんでした",
        ) from exc
    return target


def discard_signature_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - best effort
        pass
