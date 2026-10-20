"""Google ドライブの接続 (管理者のみ・§7-3・段階 L #12).

GET    /google-drive/status        接続の状態 (未設定・未接続・接続中・接続し直し)
POST   /google-drive/connect       許可の画面の URL を返し、合言葉の cookie を置く
GET    /google-drive/callback      Google からの戻り (ブラウザーが来る・Bearer は無い)
PUT    /google-drive/folder        Picker で選んだ保存先フォルダを保存する
POST   /google-drive/picker-token  Picker 用の短い寿命のアクセストークン・API キー・アプリ ID
DELETE /google-drive/connection    切断 (Google 側の許可を取り消してから行を消す)

戻り (callback) は Bearer を持たないので、state の中の管理者の id と、接続を始めた
ブラウザーの cookie の合言葉で確かめる。戻りのあとは ``/settings/google-drive`` へ戻す。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.deps import DbDep, require_role
from app.models.audit_log import AuditLog
from app.models.leave import GoogleDriveConnection
from app.models.user import User, normalize_user_role
from app.schemas.google_drive import (
    GoogleDriveConnectStart,
    GoogleDriveFolderUpdate,
    GoogleDrivePickerToken,
    GoogleDriveStatusRead,
)
from app.services.google_drive.client import (
    GoogleDriveError,
    GoogleDriveNotAFolderError,
    GoogleDriveNotConfiguredError,
    GoogleDriveNotConnectedError,
    GoogleDriveNotFoundError,
    GoogleDriveReconnectRequiredError,
)
from app.services.google_drive.connection import (
    clear_token_cache,
    delete_connection,
    get_access_token,
    load_connection,
    notify_connection_changed,
    save_connection,
)
from app.services.google_drive.oauth import (
    STATE_TTL_SECONDS,
    DriveAccount,
    InvalidOAuthStateError,
    build_authorization,
    decrypt_token,
    exchange_code,
    get_account,
    is_configured,
    is_picker_configured,
    refresh_access_token,
    revoke_token,
    verify_state,
)
from app.services.google_drive.sheets import get_folder

router = APIRouter()

AdminUser = Annotated[User, Depends(require_role("admin"))]

#: 接続を始めたブラウザーに置く合言葉の cookie。
OAUTH_COOKIE = "rakusuke_gdrive_oauth"
#: 戻りのあとに開く画面。
SETTINGS_PAGE = "/settings/google-drive"


def _cookie_path() -> str:
    return f"{get_settings().api_v1_prefix}/google-drive"


def _account_label(account: DriveAccount | None) -> str:
    if account is None:
        return "（アカウントを読めませんでした）"
    if account.name and account.email:
        return f"{account.name}（{account.email}）"
    return account.email or account.name or "（アカウントを読めませんでした）"


def _user_label(user: User | None) -> str | None:
    if user is None:
        return None
    return user.display_name or user.username or user.email


async def _build_status(db: DbDep, row: GoogleDriveConnection | None) -> GoogleDriveStatusRead:
    settings = get_settings()
    base = GoogleDriveStatusRead(
        configured=is_configured(settings),
        picker_configured=is_picker_configured(settings),
        status="not_connected",
    )
    if row is None:
        return base
    base.status = "connected" if row.status == "connected" else "needs_reconnect"
    base.folder_id = row.folder_id
    base.shared_drive_id = row.shared_drive_id
    base.last_error = row.last_error
    base.updated_at = row.updated_at
    if row.connected_by is not None:
        base.connected_by_name = _user_label(await db.get(User, row.connected_by))
    return base


def _http_error(exc: GoogleDriveError) -> HTTPException:
    if isinstance(
        exc,
        GoogleDriveNotConfiguredError
        | GoogleDriveNotConnectedError
        | GoogleDriveReconnectRequiredError,
    ):
        return HTTPException(status.HTTP_409_CONFLICT, detail=exc.message)
    if isinstance(exc, GoogleDriveNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, GoogleDriveNotAFolderError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message)
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=exc.message)


@router.get("/status", response_model=GoogleDriveStatusRead, summary="Google ドライブの接続の状態")
async def get_status(db: DbDep, _actor: AdminUser) -> GoogleDriveStatusRead:
    row = await load_connection(db)
    folder_name: str | None = None
    folder_missing = False
    account: DriveAccount | None = None
    if row is not None and row.status == "connected" and is_configured():
        try:
            token, _expires, _row = await get_access_token(db)
            account = await get_account(token)
            if row.folder_id:
                folder_name = str((await get_folder(db, row.folder_id)).get("name") or "") or None
        except GoogleDriveNotFoundError:
            folder_missing = True
        except GoogleDriveReconnectRequiredError:
            # status を needs_reconnect にし、通知も入れた。残す。
            await db.commit()
            await db.refresh(row)
        except GoogleDriveError:
            # 一時的な失敗ではフォルダ名を出さないだけにする。
            folder_name = None
    result = await _build_status(db, row)
    result.folder_name = folder_name
    result.folder_missing = folder_missing
    if account is not None:
        result.account_email = account.email
        result.account_name = account.name
    return result


@router.post(
    "/connect", response_model=GoogleDriveConnectStart, summary="Google ドライブへの接続を始める"
)
async def start_connect(response: Response, actor: AdminUser) -> GoogleDriveConnectStart:
    try:
        started = build_authorization(actor.id)
    except GoogleDriveNotConfiguredError as exc:
        raise _http_error(exc) from exc
    response.set_cookie(
        OAUTH_COOKIE,
        started.nonce,
        max_age=STATE_TTL_SECONDS,
        path=_cookie_path(),
        httponly=True,
        secure=get_settings().is_production,
        # Google からの戻りはトップレベルの GET なので Lax で届く。
        samesite="lax",
    )
    return GoogleDriveConnectStart(authorization_url=started.url)


def _back(result: str, reason: str | None = None) -> RedirectResponse:
    query = {"result": result} | ({"reason": reason} if reason else {})
    resp = RedirectResponse(f"{SETTINGS_PAGE}?{urlencode(query)}", status_code=303)
    resp.delete_cookie(OAUTH_COOKIE, path=_cookie_path())
    return resp


@router.get("/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    db: DbDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if error:
        return _back("error", "denied" if error == "access_denied" else "google")
    if not code or not state:
        return _back("error", "invalid")
    try:
        user_id, verifier = verify_state(state, request.cookies.get(OAUTH_COOKIE))
    except InvalidOAuthStateError:
        return _back("error", "state")
    user = await db.get(User, user_id)
    if user is None or user.deleted_at is not None or normalize_user_role(user.role) != "admin":
        return _back("error", "forbidden")

    # つなぎ直し: 前の接続の持ち主と保存先を監査に残し、前のアカウントを確かめてから
    # 前のトークンを取り消す (L-2)。取り消したトークンは行から外しておく。
    previous = await load_connection(db)
    before: dict[str, object] | None = None
    previous_account: DriveAccount | None = None
    if previous is not None:
        before = {
            "status": previous.status,
            "connected_by": str(previous.connected_by) if previous.connected_by else None,
            "folder_id": previous.folder_id,
        }
        old_token = (
            decrypt_token(previous.refresh_token_encrypted)
            if previous.refresh_token_encrypted
            else None
        )
        if old_token is not None:
            try:
                old_access, _ = await refresh_access_token(old_token)
                previous_account = await get_account(old_access)
            except GoogleDriveError:
                previous_account = None
            await revoke_token(old_token)
        previous.refresh_token_encrypted = None
        clear_token_cache()
        await db.flush()

    try:
        tokens = await exchange_code(code, verifier)
    except GoogleDriveError:
        await db.commit()
        return _back("error", "exchange")
    account = await get_account(tokens.access_token)
    row = await save_connection(db, refresh_token=tokens.refresh_token, user_id=user.id)
    db.add(
        AuditLog(
            actor_user_id=user.id,
            action="google_drive_connect",
            target_table="google_drive_connections",
            target_id=str(row.id),
            before=before,
            after={
                "status": row.status,
                "connected_by": str(user.id),
                "folder_id": row.folder_id,
                "account": account.email if account else None,
            },
        )
    )
    new_email = account.email if account else None
    old_email = previous_account.email if previous_account else None
    if previous is not None and new_email != old_email:
        await notify_connection_changed(
            db, account=_account_label(account), by=_user_label(user) or "管理者"
        )
    await db.commit()
    return _back("connected")


@router.put("/folder", response_model=GoogleDriveStatusRead, summary="保存先フォルダを保存する")
async def save_folder(
    payload: GoogleDriveFolderUpdate, db: DbDep, actor: AdminUser
) -> GoogleDriveStatusRead:
    try:
        meta = await get_folder(db, payload.folder_id)
    except GoogleDriveReconnectRequiredError as exc:
        await db.commit()
        raise _http_error(exc) from exc
    except GoogleDriveNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="選んだフォルダがドライブに見つかりません"
        ) from exc
    except GoogleDriveError as exc:
        raise _http_error(exc) from exc
    row = await load_connection(db)
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=GoogleDriveNotConnectedError().message)
    before = {"folder_id": row.folder_id, "shared_drive_id": row.shared_drive_id}
    row.folder_id = str(meta["id"])
    row.shared_drive_id = str(meta["driveId"]) if meta.get("driveId") else None
    db.add(
        AuditLog(
            actor_user_id=actor.id,
            action="google_drive_folder",
            target_table="google_drive_connections",
            target_id=str(row.id),
            before=before,
            after={"folder_id": row.folder_id, "shared_drive_id": row.shared_drive_id},
        )
    )
    await db.commit()
    await db.refresh(row)
    result = await _build_status(db, row)
    result.folder_name = str(meta.get("name") or "") or None
    return result


@router.post(
    "/picker-token",
    response_model=GoogleDrivePickerToken,
    summary="Google Picker 用の短い寿命のアクセストークン",
)
async def picker_token(response: Response, db: DbDep, _actor: AdminUser) -> GoogleDrivePickerToken:
    # アクセストークンを途中の層・ブラウザーに残さない (L-4)。
    response.headers["Cache-Control"] = "no-store"
    settings = get_settings()
    if not is_picker_configured(settings):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="フォルダを選ぶ画面（Picker）の API キーとアプリ ID がまだ設定されていません",
        )
    try:
        token, expires_in, _row = await get_access_token(db)
    except GoogleDriveReconnectRequiredError as exc:
        await db.commit()
        raise _http_error(exc) from exc
    except GoogleDriveError as exc:
        raise _http_error(exc) from exc
    return GoogleDrivePickerToken(
        access_token=token,
        expires_in=expires_in,
        api_key=settings.google_picker_api_key.strip(),
        app_id=settings.google_picker_app_id.strip(),
    )


@router.delete(
    "/connection", response_model=GoogleDriveStatusRead, summary="Google ドライブを切断する"
)
async def disconnect(db: DbDep, actor: AdminUser) -> GoogleDriveStatusRead:
    row = await load_connection(db)
    revoke_failed = False
    if row is not None:
        token = decrypt_token(row.refresh_token_encrypted) if row.refresh_token_encrypted else None
        # 取り消せなかったときも切断は続け、応答で知らせる (L-3)。
        revoke_failed = token is None or not await revoke_token(token)
        row_id = str(row.id)
        before = {
            "status": row.status,
            "connected_by": str(row.connected_by) if row.connected_by else None,
            "folder_id": row.folder_id,
        }
        await delete_connection(db)
        db.add(
            AuditLog(
                actor_user_id=actor.id,
                action="google_drive_disconnect",
                target_table="google_drive_connections",
                target_id=row_id,
                before=before,
                after={"revoke_failed": revoke_failed},
            )
        )
        await db.commit()
    result = await _build_status(db, None)
    result.revoke_failed = revoke_failed
    return result
