"""API の窓口（rk.opsbase.Api の実装）と、つなぎ方（pc=ssh 転送・vps=直接）の切り替え。

    from rk.session import open_api
    with open_api(cfg) as api:
        api.get("/auth/me")
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator

from rk.auth import TokenManager
from rk.config import Config
from rk.opsbase import ApiError
from rk.transport import Http, SshTunnel


class RkApi:
    def __init__(self, http: Http, tokens: TokenManager):
        self.http = http
        self.tokens = tokens

    def _do(self, method: str, path: str, body: Any = None, params: dict | None = None) -> Any:
        try:
            return self.http.request(method, path, body, params, token=self.tokens.access_token())
        except ApiError as e:
            if e.status != 401:
                raise
            # 期限ずれなど → 1 回だけ取り直して再送（ログインの繰り返しにはならない: access_token が refresh を先に使う）
            self.tokens.invalidate_access()
            return self.http.request(method, path, body, params, token=self.tokens.access_token())

    def get(self, path: str, params: dict | None = None) -> Any:
        return self._do("GET", path, None, params)

    def call(self, method: str, path: str, body: Any = None) -> Any:
        return self._do(method.upper(), path, body)


@contextlib.contextmanager
def open_api(cfg: Config, tunnel_factory=SshTunnel) -> Iterator[RkApi]:
    if cfg.mode == "vps":
        http = Http(cfg.api_base_remote)
        yield RkApi(http, TokenManager(cfg, http))
        return
    with tunnel_factory(cfg) as port:
        http = Http(f"http://127.0.0.1:{port}/api/v1")
        yield RkApi(http, TokenManager(cfg, http))
