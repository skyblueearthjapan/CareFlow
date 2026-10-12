"""stg-backend コンテナの中で動かす（stg-create-agent-user.sh が docker cp して実行）。

試験環境にだけ、エージェント専用の管理者ユーザーを作る:
  1. 試験 DB にいる有効な admin を 1 人選び、試験用 JWT 鍵でトークンを作る（ASGI 直叩き・admin_call.py と同じ方式）
  2. POST /api/v1/admin/users（画面の「ユーザー追加」と同じ API）で role=admin のユーザーを作る
  3. 新しいユーザーとして POST /api/v1/auth/change-password で、ランダムなパスワードに変える
     （must_change_password が外れ、rk がそのままログインできる）
  4. 識別子とパスワードを 1 回だけ標準出力に出す

安全: DATABASE_URL の向き先が stg-postgres でなければ何もしない。
使い方: python /tmp/stg_create_agent_user.py <email>
"""

import asyncio
import json
import secrets
import string
import sys
from urllib.parse import urlsplit

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import get_session_factory
from app.main import app
from app.models import User

EMAIL = sys.argv[1] if len(sys.argv) > 1 else "rakusuke-agent@example.com"


def _guard() -> None:
    s = get_settings()
    host = urlsplit(s.database_url.replace("+asyncpg", "")).hostname
    if host != "stg-postgres":
        sys.exit(f"中止: DB の向き先が stg-postgres ではありません ({host})。本番では使わない道具です")
    if s.is_production:
        sys.exit("中止: APP_ENV が production です。試験環境ではありません")


def _password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(24))
        if any(c.isdigit() for c in p) and any(c.isalpha() for c in p):
            return p


async def main() -> None:
    _guard()
    factory = get_session_factory()
    async with factory() as db:
        existing = await db.scalar(
            select(User).where(User.email == EMAIL, User.deleted_at.is_(None))
        )
        if existing is not None:
            sys.exit(f"中止: {EMAIL} は試験 DB に既にいます（stg-down → stg-up でやり直してください）")
        actor = await db.scalar(
            select(User)
            .where(User.role.in_(("admin", "manager")), User.deleted_at.is_(None))
            .order_by(User.created_at)
            .limit(1)
        )
        if actor is None:
            sys.exit("中止: 試験 DB に有効な admin がいません")
        actor_tok = create_access_token(subject=actor.id, role="admin", staff_id=actor.staff_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://stg", timeout=60) as c:
        r = await c.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {actor_tok}"},
            json={"email": EMAIL, "role": "admin"},
        )
        if r.status_code != 201:
            sys.exit(f"中止: ユーザー作成が {r.status_code}: {r.text[:300]}")
        body = r.json()
        user_id = body["user"]["id"]
        temp = body["temp_password"]

        new_tok = create_access_token(subject=user_id, role="admin", staff_id=None)
        new_pw = _password()
        r2 = await c.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {new_tok}"},
            json={"current_password": temp, "new_password": new_pw},
        )
        if r2.status_code != 204:
            sys.exit(f"中止: パスワード変更が {r2.status_code}: {r2.text[:300]}")

    print(json.dumps({"identifier": EMAIL, "password": new_pw, "user_id": user_id}, ensure_ascii=False))


asyncio.run(main())
