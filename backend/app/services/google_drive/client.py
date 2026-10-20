"""Google の REST を呼ぶ httpx の口と、例外の種類.

``google-api-python-client`` は重いので足さず、Drive・Sheets・OAuth の REST を httpx で
直接呼ぶ。試験では ``set_test_transport`` に ``httpx.MockTransport`` を渡す。
"""

from __future__ import annotations

import httpx

#: 1 回の呼び出しの打ち切り (秒)。
HTTP_TIMEOUT_SECONDS = 30.0

_test_transport: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """試験用: Google への要求をモックの transport へ向ける (None で元に戻す)。"""
    global _test_transport
    _test_transport = transport


def http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=_test_transport)


class GoogleDriveError(Exception):
    """Google ドライブの操作ができなかった (画面に出せる日本語の文を持つ)。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class GoogleDriveNotConfiguredError(GoogleDriveError):
    """.env にクライアント ID などが無い (Google Cloud の準備がまだ)。"""

    def __init__(self) -> None:
        super().__init__("Google ドライブの接続はまだ設定されていません（管理の準備が要ります）")


class GoogleDriveNotConnectedError(GoogleDriveError):
    """まだ接続していない。"""

    def __init__(self) -> None:
        super().__init__("Google ドライブに接続していません")


class GoogleDriveReconnectRequiredError(GoogleDriveError):
    """トークンが使えない (取り消し・6 か月未使用など)。status は needs_reconnect にしてある。"""

    def __init__(self) -> None:
        super().__init__("Google ドライブにもう一度接続してください")


class GoogleDriveNotFoundError(GoogleDriveError):
    """ファイル・フォルダがドライブに見つからない (消された・権限が外れた)。"""

    def __init__(self) -> None:
        super().__init__("ドライブに見つかりません")


class GoogleDriveNotAFolderError(GoogleDriveError):
    """選ばれたものがフォルダではない。"""

    def __init__(self) -> None:
        super().__init__("選んだものはフォルダではありません")
