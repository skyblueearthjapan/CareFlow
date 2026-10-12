"""ブラウザーの鍵（設計 §1-7・§4-5）。らく助のログインの access JWT を Bearer で受け、backend の
GET /api/v1/auth/me で確かめる（JWT_SECRET は中継に置かない）。結果はトークンの sha256 をキーに 60 秒だけ覚える。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

import httpx

ADMIN_ROLES = {"admin", "manager"}  # 旧 manager も管理者（frontend lib/rbac.ts の isAdminRole と同じ）


@dataclass
class User:
    id: str
    role: str
    username: str = ""
    token_exp: float | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES


class AuthChecker(Protocol):
    async def __call__(self, token: str) -> User | None: ...


class BackendAuthChecker:
    def __init__(self, backend_url: str, ttl: float = 60.0, client: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.url = backend_url.rstrip("/") + "/auth/me"
        self.ttl = ttl
        self.client = client or httpx.AsyncClient(timeout=10.0)
        self.clock = clock
        self._cache: dict[str, tuple[float, User | None]] = {}

    async def __call__(self, token: str) -> User | None:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = self.clock()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        try:
            r = await self.client.get(self.url, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            return None  # backend に届かないときは通さない（覚えない）
        user = None
        if r.status_code == 200:
            d = r.json()
            from rk.auth import jwt_exp
            user = User(id=str(d.get("id")), role=str(d.get("role") or ""), username=str(d.get("username") or ""),
                        token_exp=jwt_exp(token))
        self._cache[key] = (now + self.ttl, user)
        if len(self._cache) > 1000:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        return user


#: だれが何のためにエージェントを使えるか（目的 → 役割）。いまは「相談」＝管理者だけ（PO 2026-10-13）。
#: のちに職員へ限られた目的（例: カルテの下書き "karte"）を開くときは、ここに目的と役割を足し、その目的の口だけ
#: can_use(user, "karte") で確かめる（相談の口・AI のつなぎ先は "consult" のまま）。画面はまだ作らない。
AGENT_USES: dict[str, frozenset[str]] = {"consult": frozenset(ADMIN_ROLES)}


def can_use(user: User, purpose: str = "consult") -> bool:
    return user.role in AGENT_USES.get(purpose, frozenset())


FeatureGate = Callable[[User], Awaitable[bool]]


async def always_on(_user: User) -> bool:
    return True


def office_flag_gate(run_sql: Callable[[str], list]) -> FeatureGate:
    """機能の旗 rakusuke_agent（office_feature_flags・どれかの拠点で有効なら）を agent_ro で読む。"""
    import asyncio

    sql = ("select count(*) as n from office_feature_flags "
           "where feature_key = 'rakusuke_agent' and enabled_at is not null")

    async def gate(_user: User) -> bool:
        try:
            rows = await asyncio.to_thread(run_sql, sql)
            return bool(rows) and int(rows[0].get("n") or 0) > 0
        except Exception:
            return False

    return gate
