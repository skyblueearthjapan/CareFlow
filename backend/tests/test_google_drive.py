"""Google ドライブの接続と勤務表のスプレッドシート (段階 L #12・設計 §7-3).

Google への HTTP はすべて ``httpx.MockTransport`` で受ける (本物へは出ない)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.models import User
from app.models.leave import GoogleDriveConnection
from app.models.notification import Notification
from app.services.google_drive import (
    CellFormat,
    CellRange,
    FormatRange,
    Formula,
    GoogleDriveReconnectRequiredError,
    SheetData,
    create_spreadsheet,
)
from app.services.google_drive import client as gd_client
from app.services.google_drive.connection import (
    NOTIFY_GOOGLE_DRIVE_RECONNECT,
    get_access_token,
    load_connection,
    save_connection,
)
from app.services.google_drive.oauth import (
    InvalidOAuthStateError,
    build_authorization,
    decrypt_token,
    encrypt_token,
    verify_state,
)

# 試験のための作り物の値 (本物の秘密ではない)。
FAKE_ENV = {
    "GOOGLE_DRIVE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_DRIVE_CLIENT_SECRET": "test-client-secret",
    "GOOGLE_DRIVE_REDIRECT_URI": "https://example.test/api/v1/google-drive/callback",
    "GOOGLE_PICKER_API_KEY": "test-picker-key",
    "GOOGLE_PICKER_APP_ID": "123456789",
}
SCOPE = "https://www.googleapis.com/auth/drive.file"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k, v in FAKE_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in FAKE_ENV:
        monkeypatch.setenv(k, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeGoogle:
    """Google の口を真似る。``handlers`` は (メソッド, URL の頭) → 応答を作る関数。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handlers: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []

    def on(self, method: str, prefix: str, fn: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handlers.insert(0, (method, prefix, fn))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for method, prefix, fn in self.handlers:
            if request.method == method and url.startswith(prefix):
                return fn(request)
        return httpx.Response(500, json={"error": f"unexpected {request.method} {url}"})


@pytest.fixture
def google() -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    fake.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        lambda r: httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3599}),
    )
    gd_client.set_test_transport(httpx.MockTransport(fake))
    try:
        yield fake
    finally:
        gd_client.set_test_transport(None)


async def _make_user(db, email: str, role: str) -> User:
    user = User(email=email, password_hash=hash_password("not-used-here"), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(subject=user.id, role=user.role, staff_id=user.staff_id)
    return {"Authorization": f"Bearer {token}"}


async def _connect_row(db, user: User, *, folder_id: str | None = "folder-1") -> None:
    row = await save_connection(db, refresh_token="1//refresh-test", user_id=user.id)
    row.folder_id = folder_id
    await db.commit()


# ── 暗号化・state ────────────────────────────────────────────────────


def test_token_encrypt_roundtrip(configured) -> None:
    enc = encrypt_token("1//refresh-test")
    assert "1//refresh-test" not in enc
    assert decrypt_token(enc) == "1//refresh-test"
    assert decrypt_token("not-a-fernet-token") is None


def test_authorization_url_has_pkce_and_state(configured) -> None:
    user_id = uuid.uuid4()
    started = build_authorization(user_id)
    parsed = urlparse(started.url)
    assert parsed.netloc == "accounts.google.com"
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["scope"] == SCOPE
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == FAKE_ENV["GOOGLE_DRIVE_REDIRECT_URI"]
    # 戻りで state と cookie の合言葉がそろえば、管理者と verifier が取り出せる。
    got_user, verifier = verify_state(q["state"], started.nonce)
    assert got_user == user_id
    import base64
    import hashlib

    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert q["code_challenge"] == challenge
    # verifier は URL に平文で出ない。
    assert verifier not in started.url


def test_verify_state_rejects_wrong_cookie_and_tampering(configured) -> None:
    started = build_authorization(uuid.uuid4())
    state = parse_qs(urlparse(started.url).query)["state"][0]
    with pytest.raises(InvalidOAuthStateError):
        verify_state(state, "other-nonce")
    with pytest.raises(InvalidOAuthStateError):
        verify_state(state, None)
    with pytest.raises(InvalidOAuthStateError):
        verify_state(state[:-4] + "AAAA", started.nonce)


def test_verify_state_expires(configured, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    started = build_authorization(uuid.uuid4())
    state = parse_qs(urlparse(started.url).query)["state"][0]
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real + 601)
    with pytest.raises(InvalidOAuthStateError):
        verify_state(state, started.nonce)


# ── 未設定・権限 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_unconfigured(client, db, unconfigured) -> None:
    admin = await _make_user(db, "gd-admin@example.com", "admin")
    res = await client.get("/api/v1/google-drive/status", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is False
    assert body["picker_configured"] is False
    assert body["status"] == "not_connected"
    res = await client.post("/api/v1/google-drive/connect", headers=_bearer(admin))
    assert res.status_code == 409
    assert "設定されていません" in res.json()["detail"]


@pytest.mark.asyncio
async def test_non_admin_forbidden(client, db, configured) -> None:
    staff = await _make_user(db, "gd-staff@example.com", "staff")
    h = _bearer(staff)
    assert (await client.get("/api/v1/google-drive/status", headers=h)).status_code == 403
    assert (await client.post("/api/v1/google-drive/connect", headers=h)).status_code == 403
    assert (
        await client.put("/api/v1/google-drive/folder", headers=h, json={"folder_id": "abc"})
    ).status_code == 403
    assert (await client.post("/api/v1/google-drive/picker-token", headers=h)).status_code == 403
    assert (await client.delete("/api/v1/google-drive/connection", headers=h)).status_code == 403


# ── 接続の流れ ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_and_callback_saves_encrypted_token(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin2@example.com", "admin")
    admin_id = admin.id
    google.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        lambda r: httpx.Response(
            200,
            json={
                "access_token": "ya29.first",
                "expires_in": 3599,
                "refresh_token": "1//refresh-new",
                "scope": SCOPE,
                "token_type": "Bearer",
            },
        ),
    )
    res = await client.post("/api/v1/google-drive/connect", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    url = res.json()["authorization_url"]
    nonce = res.cookies.get("rakusuke_gdrive_oauth")
    assert nonce
    state = parse_qs(urlparse(url).query)["state"][0]

    # cookie が無い (別のブラウザー) と受け付けない。
    client.cookies.clear()
    res = await client.get(
        "/api/v1/google-drive/callback", params={"code": "4/abc", "state": state}
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/settings/google-drive?result=error&reason=state"
    assert await load_connection(db) is None

    client.cookies.set("rakusuke_gdrive_oauth", nonce, path="/api/v1/google-drive")
    res = await client.get(
        "/api/v1/google-drive/callback", params={"code": "4/abc", "state": state}
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/settings/google-drive?result=connected"

    # 要求: code・verifier・client_secret を送っている。
    token_req = google.requests[-1]
    form = parse_qs(token_req.content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["4/abc"]
    assert form["code_verifier"][0]

    db.expire_all()
    row = await load_connection(db)
    assert row is not None
    assert row.status == "connected"
    assert row.connected_by == admin_id
    assert "1//refresh-new" not in (row.refresh_token_encrypted or "")
    assert decrypt_token(row.refresh_token_encrypted or "") == "1//refresh-new"


@pytest.mark.asyncio
async def test_callback_without_drive_scope_is_error(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin3@example.com", "admin")
    google.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        lambda r: httpx.Response(
            200, json={"access_token": "x", "refresh_token": "1//r", "scope": "openid"}
        ),
    )
    res = await client.post("/api/v1/google-drive/connect", headers=_bearer(admin))
    state = parse_qs(urlparse(res.json()["authorization_url"]).query)["state"][0]
    res = await client.get("/api/v1/google-drive/callback", params={"code": "c", "state": state})
    assert res.headers["location"] == "/settings/google-drive?result=error&reason=exchange"
    assert await load_connection(db) is None


@pytest.mark.asyncio
async def test_callback_denied(client, configured) -> None:
    res = await client.get("/api/v1/google-drive/callback", params={"error": "access_denied"})
    assert res.headers["location"] == "/settings/google-drive?result=error&reason=denied"


@pytest.mark.asyncio
async def test_singleton_guard(db, configured) -> None:
    admin = await _make_user(db, "gd-admin4@example.com", "admin")
    await save_connection(db, refresh_token="1//a", user_id=admin.id)
    await save_connection(db, refresh_token="1//b", user_id=admin.id)
    await db.commit()
    rows = (await db.scalars(select(GoogleDriveConnection))).all()
    assert len(rows) == 1
    assert decrypt_token(rows[0].refresh_token_encrypted or "") == "1//b"
    # 2 行目を直接入れても部分 UNIQUE が弾く。
    db.add(GoogleDriveConnection(is_singleton=True, status="connected"))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# ── トークン切れ ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_updates_access_token(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin5@example.com", "admin")
    await _connect_row(db, admin)
    token, expires_in, _row = await get_access_token(db)
    assert token == "ya29.test"
    assert expires_in == 3599
    form = parse_qs(google.requests[-1].content.decode())
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["1//refresh-test"]


@pytest.mark.asyncio
async def test_invalid_grant_marks_needs_reconnect_and_notifies(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin6@example.com", "admin")
    other = await _make_user(db, "gd-admin7@example.com", "admin")
    await _make_user(db, "gd-staff2@example.com", "staff")
    await _connect_row(db, admin)
    google.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    with pytest.raises(GoogleDriveReconnectRequiredError):
        await get_access_token(db)
    await db.commit()
    row = await load_connection(db)
    assert row is not None and row.status == "needs_reconnect"
    assert row.last_error
    notes = (
        await db.scalars(
            select(Notification).where(Notification.reference_type == NOTIFY_GOOGLE_DRIVE_RECONNECT)
        )
    ).all()
    assert {n.user_id for n in notes} == {admin.id, other.id}

    # 2 回目は Google を呼ばず、通知も増やさない。
    before = len(google.requests)
    with pytest.raises(GoogleDriveReconnectRequiredError):
        await get_access_token(db)
    assert len(google.requests) == before
    count = len(
        (
            await db.scalars(
                select(Notification).where(
                    Notification.reference_type == NOTIFY_GOOGLE_DRIVE_RECONNECT
                )
            )
        ).all()
    )
    assert count == 2


@pytest.mark.asyncio
async def test_api_401_marks_needs_reconnect(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin8@example.com", "admin")
    await _connect_row(db, admin)
    google.on(
        "POST",
        "https://www.googleapis.com/drive/v3/files",
        lambda r: httpx.Response(401, json={"error": {"code": 401}}),
    )
    with pytest.raises(GoogleDriveReconnectRequiredError):
        await create_spreadsheet(db, "勤務表", None, [SheetData(title="勤務表", rows=[["a"]])])
    await db.commit()
    row = await load_connection(db)
    assert row is not None and row.status == "needs_reconnect"
    notes = (await db.scalars(select(Notification).where(Notification.user_id == admin.id))).all()
    assert any(n.type == NOTIFY_GOOGLE_DRIVE_RECONNECT for n in notes)


@pytest.mark.asyncio
async def test_status_detects_invalid_grant(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin17@example.com", "admin")
    await _connect_row(db, admin, folder_id="fold_A")
    google.on(
        "POST",
        "https://oauth2.googleapis.com/token",
        lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    res = await client.get("/api/v1/google-drive/status", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "needs_reconnect"
    db.expire_all()
    row = await load_connection(db)
    assert row is not None and row.status == "needs_reconnect"


@pytest.mark.asyncio
async def test_status_shows_needs_reconnect(client, db, configured) -> None:
    admin = await _make_user(db, "gd-admin9@example.com", "admin")
    await _connect_row(db, admin, folder_id=None)
    row = await load_connection(db)
    assert row is not None
    row.status = "needs_reconnect"
    row.last_error = "invalid_grant"
    await db.commit()
    res = await client.get("/api/v1/google-drive/status", headers=_bearer(admin))
    body = res.json()
    assert body["configured"] is True
    assert body["status"] == "needs_reconnect"
    res = await client.post("/api/v1/google-drive/picker-token", headers=_bearer(admin))
    assert res.status_code == 409


# ── フォルダ・Picker・切断 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_folder_reads_shared_drive(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin10@example.com", "admin")
    await _connect_row(db, admin, folder_id=None)
    google.on(
        "GET",
        "https://www.googleapis.com/drive/v3/files/fold_A",
        lambda r: httpx.Response(
            200,
            json={
                "id": "fold_A",
                "name": "勤務表",
                "mimeType": "application/vnd.google-apps.folder",
                "driveId": "drive_X",
                "trashed": False,
            },
        ),
    )
    res = await client.put(
        "/api/v1/google-drive/folder", headers=_bearer(admin), json={"folder_id": "fold_A"}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["folder_id"] == "fold_A"
    assert body["folder_name"] == "勤務表"
    assert body["shared_drive_id"] == "drive_X"
    get_req = google.requests[-1]
    assert get_req.url.params["supportsAllDrives"] == "true"
    assert get_req.headers["Authorization"] == "Bearer ya29.test"

    # 状態にもフォルダ名が出る。
    res = await client.get("/api/v1/google-drive/status", headers=_bearer(admin))
    assert res.json()["folder_name"] == "勤務表"


@pytest.mark.asyncio
async def test_save_folder_rejects_file(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin11@example.com", "admin")
    await _connect_row(db, admin, folder_id=None)
    google.on(
        "GET",
        "https://www.googleapis.com/drive/v3/files/doc_1",
        lambda r: httpx.Response(
            200, json={"id": "doc_1", "name": "x", "mimeType": "application/pdf"}
        ),
    )
    res = await client.put(
        "/api/v1/google-drive/folder", headers=_bearer(admin), json={"folder_id": "doc_1"}
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_picker_token(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin12@example.com", "admin")
    await _connect_row(db, admin)
    res = await client.post("/api/v1/google-drive/picker-token", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {
        "access_token": "ya29.test",
        "expires_in": 3599,
        "api_key": FAKE_ENV["GOOGLE_PICKER_API_KEY"],
        "app_id": FAKE_ENV["GOOGLE_PICKER_APP_ID"],
    }


@pytest.mark.asyncio
async def test_disconnect_revokes_and_deletes(client, db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin13@example.com", "admin")
    await _connect_row(db, admin)
    google.on("POST", "https://oauth2.googleapis.com/revoke", lambda r: httpx.Response(200))
    res = await client.delete("/api/v1/google-drive/connection", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["status"] == "not_connected"
    revoke = google.requests[-1]
    assert parse_qs(revoke.content.decode())["token"] == ["1//refresh-test"]
    db.expire_all()
    assert await load_connection(db) is None


# ── スプレッドシートを作る ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_spreadsheet_requests(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin14@example.com", "admin")
    await _connect_row(db, admin, folder_id="fold_A")
    google.on(
        "POST",
        "https://www.googleapis.com/drive/v3/files",
        lambda r: httpx.Response(
            200, json={"id": "sheet_1", "webViewLink": "https://docs.google.com/x/edit"}
        ),
    )
    google.on(
        "GET",
        "https://sheets.googleapis.com/v4/spreadsheets/sheet_1",
        lambda r: httpx.Response(200, json={"sheets": [{"properties": {"sheetId": 0}}]}),
    )
    google.on(
        "POST",
        "https://sheets.googleapis.com/v4/spreadsheets/sheet_1:batchUpdate",
        lambda r: httpx.Response(200, json={"spreadsheetId": "sheet_1", "replies": []}),
    )
    wide = ["x"] * 40
    sheets = [
        SheetData(
            title="勤務表",
            rows=[
                ["　10月", None, 160, "時間"],
                wide,
                [1, 2.5, Formula("=SUM(A3:B3)"), True, '=HYPERLINK("x")'],
            ],
            formats=[
                FormatRange(
                    CellRange(0, 0, 1, 4),
                    CellFormat(bold=True, background="#FFF2CC", horizontal_align="CENTER"),
                    borders=True,
                ),
                FormatRange(CellRange(2, 0, 3, 3), CellFormat(number_format="0.0")),
            ],
            merges=[CellRange(0, 0, 1, 2)],
            column_widths={0: 120},
            frozen_rows=2,
        ),
        SheetData(title="集計", rows=[["氏名", "1月"]]),
    ]
    file_id, url = await create_spreadsheet(db, "勤務形態一覧表_2026年10月_予定", None, sheets)
    assert (file_id, url) == ("sheet_1", "https://docs.google.com/x/edit")

    create_req = next(r for r in google.requests if r.url.path == "/drive/v3/files")
    assert create_req.method == "POST"
    assert create_req.url.params["supportsAllDrives"] == "true"
    assert create_req.headers["Authorization"] == "Bearer ya29.test"
    assert json.loads(create_req.content) == {
        "name": "勤務形態一覧表_2026年10月_予定",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": ["fold_A"],
    }

    batch = json.loads(google.requests[-1].content)["requests"]
    first = batch[0]["updateSheetProperties"]
    assert first["properties"]["title"] == "勤務表"
    assert first["properties"]["gridProperties"]["columnCount"] == 40
    assert first["properties"]["gridProperties"]["frozenRowCount"] == 2
    assert batch[1]["addSheet"]["properties"]["title"] == "集計"
    assert batch[1]["addSheet"]["properties"]["sheetId"] == 1
    cells = batch[2]["updateCells"]
    assert cells["fields"] == "userEnteredValue"
    row0 = cells["rows"][0]["values"]
    assert row0[0] == {"userEnteredValue": {"stringValue": "　10月"}}
    assert row0[1] == {}
    assert row0[2] == {"userEnteredValue": {"numberValue": 160}}
    row2 = cells["rows"][2]["values"]
    assert row2[2] == {"userEnteredValue": {"formulaValue": "=SUM(A3:B3)"}}
    assert row2[3] == {"userEnteredValue": {"boolValue": True}}
    # 「=」で始まるふつうの文字は式にならない (数式の注入を防ぐ)。
    assert row2[4] == {"userEnteredValue": {"stringValue": '=HYPERLINK("x")'}}
    kinds = [next(iter(r)) for r in batch]
    assert kinds.count("repeatCell") == 2
    assert "updateBorders" in kinds and "mergeCells" in kinds
    assert "updateDimensionProperties" in kinds
    rc = next(r["repeatCell"] for r in batch if "repeatCell" in r)
    assert rc["cell"]["userEnteredFormat"]["backgroundColor"]["red"] == 1.0
    assert "userEnteredFormat.textFormat.bold" in rc["fields"].split(",")


@pytest.mark.asyncio
async def test_create_spreadsheet_deletes_half_made_file(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin15@example.com", "admin")
    await _connect_row(db, admin, folder_id="fold_A")
    google.on(
        "POST",
        "https://www.googleapis.com/drive/v3/files",
        lambda r: httpx.Response(200, json={"id": "sheet_2"}),
    )
    google.on(
        "GET",
        "https://sheets.googleapis.com/v4/spreadsheets/sheet_2",
        lambda r: httpx.Response(200, json={"sheets": [{"properties": {"sheetId": 0}}]}),
    )
    google.on(
        "POST",
        "https://sheets.googleapis.com/v4/spreadsheets/sheet_2:batchUpdate",
        lambda r: httpx.Response(400, json={"error": {"code": 400}}),
    )
    google.on(
        "DELETE",
        "https://www.googleapis.com/drive/v3/files/sheet_2",
        lambda r: httpx.Response(204),
    )
    from app.services.google_drive import GoogleDriveError

    with pytest.raises(GoogleDriveError):
        await create_spreadsheet(db, "t", None, [SheetData(title="勤務表", rows=[["a"]])])
    deleted = google.requests[-1]
    assert deleted.method == "DELETE"
    assert deleted.url.params["supportsAllDrives"] == "true"


@pytest.mark.asyncio
async def test_create_spreadsheet_rejects_bad_input_before_calling(db, configured, google) -> None:
    admin = await _make_user(db, "gd-admin16@example.com", "admin")
    await _connect_row(db, admin)
    with pytest.raises(ValueError):
        await create_spreadsheet(db, "t", None, [])
    with pytest.raises(ValueError):
        await create_spreadsheet(
            db, "t", None, [SheetData(title="a", rows=[]), SheetData(title="a", rows=[])]
        )
    with pytest.raises(ValueError):
        await create_spreadsheet(
            db,
            "t",
            None,
            [
                SheetData(
                    title="a",
                    rows=[["x"]],
                    formats=[FormatRange(CellRange(0, 0, 1, 1), CellFormat(background="red"))],
                )
            ],
        )
    assert not any("drive/v3/files" in str(r.url) for r in google.requests)


def test_cell_formula_only_from_formula_type() -> None:
    from app.services.google_drive.sheets import _cell

    assert _cell("=1+1") == {"userEnteredValue": {"stringValue": "=1+1"}}
    assert _cell("+1") == {"userEnteredValue": {"stringValue": "+1"}}
    assert _cell(Formula("=1+1")) == {"userEnteredValue": {"formulaValue": "=1+1"}}
