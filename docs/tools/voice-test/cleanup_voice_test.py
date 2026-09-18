"""音声記録テストの後片付け（manifest 駆動・記載 ID 以外に触れない）。
録音(音声ファイル含む)→訪問→患者を削除し、S009 を manifest の staff_before に戻す。DRY=1 で件数表示のみ。"""
import asyncio, json, os, sys
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.core.security import create_access_token
from app.db.session import get_session_factory
from app.main import app
from app.models import User

EMAIL = "yuji.imaizumi@thousands.jp"
man = json.load(open(sys.argv[1], encoding="utf-8"))
DRY = os.environ.get("DRY") == "1"

async def main():
    factory = get_session_factory()
    async with factory() as db:
        u = await db.scalar(select(User).where(User.email == EMAIL))
        tok = create_access_token(subject=u.id, role=u.role, staff_id=u.staff_id)
    h = {"Authorization": "Bearer " + tok, "Content-Type": "application/json"}
    out = {"recordings": 0, "visits": 0, "patients": 0, "errors": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", timeout=120) as c:
        pids = {p["id"] for p in man["patients"]}
        r = await c.get("/api/v1/visit-recordings", headers=h, params={"staff_id": man["staff_id"], "limit": 200})
        items = r.json().get("items", r.json()) if r.status_code == 200 else []
        recs = [x for x in items if x.get("staff_id") == man["staff_id"]]
        for x in recs:
            out["recordings"] += 1
            if not DRY:
                d = await c.delete(f"/api/v1/visit-recordings/{x['id']}", headers=h)
                if d.status_code not in (200, 204): out["errors"].append(("rec", x["id"], d.status_code, d.text[:120]))
        for v in man["visits"]:
            out["visits"] += 1
            if not DRY:
                d = await c.delete(f"/api/v1/visits/{v['id']}", headers=h)
                if d.status_code not in (200, 204, 404): out["errors"].append(("visit", v["id"], d.status_code, d.text[:120]))
        for p in man["patients"]:
            out["patients"] += 1
            if not DRY:
                d = await c.delete(f"/api/v1/patients/{p['id']}", headers=h)
                if d.status_code not in (200, 204, 404): out["errors"].append(("patient", p["id"], d.status_code, d.text[:120]))
        if not DRY:
            b = man["staff_before"]
            r = await c.patch(f"/api/v1/staff/{man['staff_id']}", headers=h, content=json.dumps({"status": b["status"]}))
            out["staff_restore"] = r.status_code
    print(json.dumps(out, ensure_ascii=False, indent=1))

asyncio.run(main())
