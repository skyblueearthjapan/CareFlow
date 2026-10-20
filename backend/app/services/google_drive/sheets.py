"""Google スプレッドシートを作って値と簡単な書式を書く.

正典: ``docs/plans/leave-and-kinmu-sheet-design-2026-10-20.md`` §7-1・§7-3。
公式:
  * Drive ``files.create`` — https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create
  * Sheets ``spreadsheets.batchUpdate`` — https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate

流れ: Drive の ``files.create`` (``mimeType=application/vnd.google-apps.spreadsheet``・
``parents=[保存先]``・**``supportsAllDrives=true``**) で空のスプレッドシートを作り、最初の
シートの id を読み、Sheets の ``batchUpdate`` 1 回でシートの名前・大きさ・値・書式・
結合・列幅を書く。書き込みに失敗したら作りかけのファイルを消す (消せなくても例外は元のもの)。

勤務表の中身 (``app/services/kinmu_sheet/``) はこのファイルの ``SheetData`` の形で渡す。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.leave import GoogleDriveConnection
from app.services.google_drive.client import (
    GoogleDriveError,
    GoogleDriveNotAFolderError,
    GoogleDriveNotFoundError,
    GoogleDriveReconnectRequiredError,
    http_client,
)
from app.services.google_drive.connection import get_access_token, mark_needs_reconnect

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
MIME_SPREADSHEET = "application/vnd.google-apps.spreadsheet"
MIME_FOLDER = "application/vnd.google-apps.folder"

#: セルの値。str は「=」で始まれば式、それ以外は文字。None は空のセル。
CellValue = str | int | float | bool | None


@dataclass(frozen=True)
class CellFormat:
    """セルの書式 (None の項目は触らない)。

    * ``background`` — 塗り。``"#RRGGBB"``。
    * ``bold`` / ``font_size`` — 文字。
    * ``horizontal_align`` — ``"LEFT"`` / ``"CENTER"`` / ``"RIGHT"``。
    * ``number_format`` — 数の表示 (例 ``"0.0"``・``"0.00"``)。
    * ``wrap`` — 折り返す。
    """

    background: str | None = None
    bold: bool | None = None
    font_size: int | None = None
    horizontal_align: Literal["LEFT", "CENTER", "RIGHT"] | None = None
    number_format: str | None = None
    wrap: bool | None = None


@dataclass(frozen=True)
class CellRange:
    """0 始まりの範囲。``end_*`` は含まない (Sheets の GridRange と同じ)。"""

    start_row: int
    start_col: int
    end_row: int
    end_col: int


@dataclass(frozen=True)
class FormatRange:
    """範囲に書式をかける。``borders=True`` で外側と内側に細い罫線。"""

    range: CellRange
    format: CellFormat = field(default_factory=CellFormat)
    borders: bool = False


@dataclass(frozen=True)
class SheetData:
    """1 枚のシート (``create_spreadsheet`` の入力)。

    * ``title`` — シート名 (例「勤務表」「集計」)。1 つのファイルの中で重ならないこと。
    * ``rows`` — 行の並び。各行はセルの値の並び (``CellValue``)。A1 から左上詰めで書く。
      行ごとに長さが違ってよい。数は数として書く (合計の式は「=SUM(E5:AI5)」のように文字で)。
    * ``formats`` — 書式をかける範囲 (後に書いたものが上書き)。
    * ``merges`` — 結合するセルの範囲。
    * ``column_widths`` — 列の幅 (列の番号 0 始まり → ピクセル)。
    * ``frozen_rows`` / ``frozen_cols`` — 固定する見出しの行数・列数 (結合の途中で切らないこと)。
    """

    title: str
    rows: list[list[CellValue]]
    formats: list[FormatRange] = field(default_factory=list)
    merges: list[CellRange] = field(default_factory=list)
    column_widths: dict[int, int] = field(default_factory=dict)
    frozen_rows: int = 0
    frozen_cols: int = 0


# ── Google の呼び出し ─────────────────────────────────────────────────


async def _request(
    db: AsyncSession,
    row: GoogleDriveConnection,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    token: str,
    params: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        resp = await client.request(
            method, url, params=params, json=json, headers={"Authorization": f"Bearer {token}"}
        )
    except httpx.RequestError as exc:
        raise GoogleDriveError("Google に接続できませんでした") from exc
    if resp.status_code == 401:
        await mark_needs_reconnect(db, row, "Google がアクセスを受け付けませんでした（401）")
        raise GoogleDriveReconnectRequiredError()
    if resp.status_code == 404:
        raise GoogleDriveNotFoundError()
    if resp.status_code == 403:
        raise GoogleDriveError(
            "Google ドライブの権限が足りません（保存先フォルダをもう一度選んでください）"
        )
    if resp.status_code >= 400:
        raise GoogleDriveError(f"Google ドライブの操作ができませんでした（{resp.status_code}）")
    if not resp.content:
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


async def get_folder(db: AsyncSession, folder_id: str) -> dict[str, Any]:
    """フォルダの名前と共有ドライブの id を読む (Picker で選ばれたものだけ読める)。

    Returns: ``{"id", "name", "driveId" (共有ドライブのときだけ)}``。
    Raises: ``GoogleDriveNotFoundError`` (消された)・``GoogleDriveNotAFolderError``。
    """
    token, _expires, row = await get_access_token(db)
    async with http_client() as client:
        meta = await _request(
            db,
            row,
            client,
            "GET",
            f"{DRIVE_FILES_URL}/{folder_id}",
            token=token,
            params={"supportsAllDrives": "true", "fields": "id,name,mimeType,driveId,trashed"},
        )
    if meta.get("trashed"):
        raise GoogleDriveNotFoundError()
    if meta.get("mimeType") != MIME_FOLDER:
        raise GoogleDriveNotAFolderError()
    return meta


# ── batchUpdate の要求を組み立てる ──────────────────────────────────


def _color(hex_color: str) -> dict[str, float]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"色は #RRGGBB で書いてください: {hex_color!r}")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": r, "green": g, "blue": b}


def _cell(value: CellValue) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, int | float):
        return {"userEnteredValue": {"numberValue": value}}
    if value.startswith("="):
        return {"userEnteredValue": {"formulaValue": value}}
    return {"userEnteredValue": {"stringValue": value}}


def _grid(sheet_id: int, r: CellRange) -> dict[str, int]:
    return {
        "sheetId": sheet_id,
        "startRowIndex": r.start_row,
        "endRowIndex": r.end_row,
        "startColumnIndex": r.start_col,
        "endColumnIndex": r.end_col,
    }


def _format_request(sheet_id: int, fr: FormatRange) -> dict[str, Any] | None:
    f = fr.format
    fmt: dict[str, Any] = {}
    fields: list[str] = []
    text: dict[str, Any] = {}
    if f.background is not None:
        fmt["backgroundColor"] = _color(f.background)
        fields.append("userEnteredFormat.backgroundColor")
    if f.bold is not None:
        text["bold"] = f.bold
        fields.append("userEnteredFormat.textFormat.bold")
    if f.font_size is not None:
        text["fontSize"] = f.font_size
        fields.append("userEnteredFormat.textFormat.fontSize")
    if text:
        fmt["textFormat"] = text
    if f.horizontal_align is not None:
        fmt["horizontalAlignment"] = f.horizontal_align
        fields.append("userEnteredFormat.horizontalAlignment")
    if f.number_format is not None:
        fmt["numberFormat"] = {"type": "NUMBER", "pattern": f.number_format}
        fields.append("userEnteredFormat.numberFormat")
    if f.wrap is not None:
        fmt["wrapStrategy"] = "WRAP" if f.wrap else "OVERFLOW_CELL"
        fields.append("userEnteredFormat.wrapStrategy")
    if not fields:
        return None
    return {
        "repeatCell": {
            "range": _grid(sheet_id, fr.range),
            "cell": {"userEnteredFormat": fmt},
            "fields": ",".join(fields),
        }
    }


def _grid_properties(sheet: SheetData) -> dict[str, int]:
    width = max((len(r) for r in sheet.rows), default=0)
    # 手で書き足せるよう少し余白を持たせる (既定のシートと同じ 1000 行・26 列を下限に)。
    return {
        "rowCount": max(len(sheet.rows), 1000),
        "columnCount": max(width, 26),
        "frozenRowCount": sheet.frozen_rows,
        "frozenColumnCount": sheet.frozen_cols,
    }


_GRID_FIELDS = (
    "gridProperties.rowCount,gridProperties.columnCount,"
    "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
)


def build_batch_requests(first_sheet_id: int, sheets: list[SheetData]) -> list[dict[str, Any]]:
    """``spreadsheets.batchUpdate`` の ``requests`` を作る (要求は書いた順に適用される)。"""
    requests: list[dict[str, Any]] = []
    ids: list[int] = []
    for i, sheet in enumerate(sheets):
        if i == 0:
            sheet_id = first_sheet_id
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": sheet.title,
                            "gridProperties": _grid_properties(sheet),
                        },
                        "fields": f"title,{_GRID_FIELDS}",
                    }
                }
            )
        else:
            sheet_id = max(first_sheet_id, 0) + i
            requests.append(
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": sheet.title,
                            "index": i,
                            "gridProperties": _grid_properties(sheet),
                        }
                    }
                }
            )
        ids.append(sheet_id)

    for sheet_id, sheet in zip(ids, sheets, strict=True):
        if sheet.rows:
            requests.append(
                {
                    "updateCells": {
                        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                        "rows": [{"values": [_cell(v) for v in row]} for row in sheet.rows],
                        "fields": "userEnteredValue",
                    }
                }
            )
        for fr in sheet.formats:
            req = _format_request(sheet_id, fr)
            if req is not None:
                requests.append(req)
            if fr.borders:
                solid = {"style": "SOLID"}
                requests.append(
                    {
                        "updateBorders": {
                            "range": _grid(sheet_id, fr.range),
                            "top": solid,
                            "bottom": solid,
                            "left": solid,
                            "right": solid,
                            "innerHorizontal": solid,
                            "innerVertical": solid,
                        }
                    }
                )
        for m in sheet.merges:
            requests.append({"mergeCells": {"range": _grid(sheet_id, m), "mergeType": "MERGE_ALL"}})
        for col, px in sorted(sheet.column_widths.items()):
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": col,
                            "endIndex": col + 1,
                        },
                        "properties": {"pixelSize": px},
                        "fields": "pixelSize",
                    }
                }
            )
    return requests


# ── 作る ─────────────────────────────────────────────────────────────


async def create_spreadsheet(
    db: AsyncSession, title: str, folder_id: str | None, sheets: list[SheetData]
) -> tuple[str, str]:
    """保存先フォルダにスプレッドシートを新しく作り、(ファイルの id, 開く URL) を返す。

    ``folder_id`` が None なら接続に保存したフォルダを使う。毎回新しいファイルを作る
    (予定版の第 2 版なども別ファイル・前の版は触らない・§7-2)。

    Raises:
        ValueError: シートが無い・シート名が重なる・色の書き方が違う。
        GoogleDriveNotConfiguredError / GoogleDriveNotConnectedError: まだ使えない。
        GoogleDriveReconnectRequiredError: 接続し直しが要る (status と通知は済み・未 commit。
            呼び出し側は commit して勤務表を「未作成」で残す)。
        GoogleDriveNotFoundError: 保存先フォルダがドライブに無い。
        GoogleDriveError: そのほか。
    """
    if not sheets:
        raise ValueError("シートが 1 枚もありません")
    titles = [s.title for s in sheets]
    if len(set(titles)) != len(titles):
        raise ValueError(f"シート名が重なっています: {titles}")

    token, _expires, row = await get_access_token(db)
    target_folder = folder_id or row.folder_id
    if not target_folder:
        raise GoogleDriveError("保存先フォルダが選ばれていません")
    # 先に組み立てて、書式の誤り (ValueError) ではファイルを作らない。
    probe = build_batch_requests(0, sheets)

    async with http_client() as client:
        created = await _request(
            db,
            row,
            client,
            "POST",
            DRIVE_FILES_URL,
            token=token,
            params={"supportsAllDrives": "true", "fields": "id,webViewLink"},
            json={"name": title, "mimeType": MIME_SPREADSHEET, "parents": [target_folder]},
        )
        file_id = str(created["id"])
        url = str(
            created.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
        )
        try:
            meta = await _request(
                db,
                row,
                client,
                "GET",
                f"{SHEETS_URL}/{file_id}",
                token=token,
                params={"fields": "sheets.properties.sheetId"},
            )
            first_id = int(meta["sheets"][0]["properties"]["sheetId"])
            requests = probe if first_id == 0 else build_batch_requests(first_id, sheets)
            await _request(
                db,
                row,
                client,
                "POST",
                f"{SHEETS_URL}/{file_id}:batchUpdate",
                token=token,
                json={"requests": requests},
            )
        except (GoogleDriveError, KeyError, IndexError, ValueError, TypeError) as exc:
            # 作りかけのファイルを残さない (消せなくても元の失敗を伝える)。
            try:
                await client.delete(
                    f"{DRIVE_FILES_URL}/{file_id}",
                    params={"supportsAllDrives": "true"},
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.RequestError:
                pass
            if isinstance(exc, GoogleDriveError):
                raise
            raise GoogleDriveError("Google から想定と違う応答が返りました") from exc
    return file_id, url
