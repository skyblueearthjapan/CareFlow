"""Google ドライブの接続 (§7-3) の入出力。トークンは返さない (Picker 用の短い寿命のものだけ)。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GoogleDriveStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: .env にクライアント ID・秘密・リダイレクト URI がそろっているか。
    configured: bool
    #: Picker の API キーとアプリ ID がそろっているか。
    picker_configured: bool
    status: Literal["not_connected", "connected", "needs_reconnect"]
    folder_id: str | None = None
    folder_name: str | None = None
    #: 保存先フォルダがドライブに見つからない (消された・権限が外れた)。
    folder_missing: bool = False
    shared_drive_id: str | None = None
    connected_by_name: str | None = None
    #: つないだ Google アカウント (Drive の about・読めなければ null)。
    account_email: str | None = None
    account_name: str | None = None
    last_error: str | None = None
    updated_at: datetime | None = None
    #: 切断のとき Google 側の許可を取り消せなかった (切断の応答だけ)。
    revoke_failed: bool = False


class GoogleDriveConnectStart(BaseModel):
    authorization_url: str


class GoogleDriveFolderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class GoogleDrivePickerToken(BaseModel):
    access_token: str
    expires_in: int
    api_key: str
    app_id: str
