"""backend コンテナ内で admin として API を1回叩く (python - METHOD PATH [JSON_BODY])。
実行者は今泉アカウント固定。reconcile cron と同じ ASGI 直叩き方式。"""
import asyncio
import json
import sys

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.db.session import get_session_factory
from app.main import app
from app.models import User

EMAIL = "yuji.imaizumi@thousands.jp"
method, path = sys.argv[1], sys.argv[2]
body = sys.argv[3] if len(sys.argv) > 3 else None


async def main() -> None:
    factory = get_session_factory()
    async with factory() as db:
        u = await db.scalar(select(User).where(User.email == EMAIL))
        if u is None:
            print("NO_USER")
            return
        tok = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    headers = {"Authorization": "Bearer " + tok}
    if body is not None:
        headers["Content-Type"] = "application/json"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        r = await c.request(method, path, headers=headers, content=body)
        print(r.status_code)
        try:
            print(json.dumps(r.json(), ensure_ascii=False, indent=1))
        except Exception:
            print(r.text)


asyncio.run(main())
